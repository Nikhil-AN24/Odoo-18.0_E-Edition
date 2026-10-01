# -*- coding: utf-8 -*-
"""Inventory custody of a stone, on the customer side (§4.1, §5.1–§5.3).

Everything from Accept onwards is tracked on the sale order line: that is the
record Dispatch works on, and the one the customer's invoice is built from.
The vault is a tick and a timestamp, not a stock location (§2.2) — the stone
is already in stock the moment Inventory accepts it.
"""

import logging

from odoo import _, api, fields, models
from .lgd_ops_mixin import lgd_notify, lgd_responsible_user
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)

GROUP_INVENTORY = 'contact_stage_bar.group_lgd_inventory'
GROUP_SHIPMENT = 'contact_stage_bar.group_lgd_shipment'

#: Line statuses that mean the stone will never ship on this order.
LGD_DEAD_STATUSES = ('cancelled', 'replaced', 'not_available', 'qc_fail')


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    # ── Custody (§4.1) ──────────────────────────────────────────────────
    lgd_accepted_at = fields.Datetime(
        string="Accepted into stock", copy=False, index=True)
    lgd_in_vault = fields.Boolean(string="In vault", copy=False)
    lgd_vault_by = fields.Many2one(
        'res.users', string="Vaulted by", copy=False)
    lgd_vault_at = fields.Datetime(string="Vaulted on", copy=False)
    lgd_to_dispatch = fields.Boolean(string="With dispatch", copy=False)
    lgd_to_dispatch_by = fields.Many2one('res.users', copy=False)
    lgd_to_dispatch_at = fields.Datetime(copy=False)

    lgd_invoice_number = fields.Char(
        related='order_id.sdk_augmont_number', store=True, index=True,
        string="Invoice No.")

    # The Operations record for this stone. Computed rather than stored: the
    # link is owned by the purchase side, and a stale copy here would send a
    # stone through the flow under the wrong set (§4.1).
    lgd_po_line_id = fields.Many2one(
        'purchase.order.line', string="Stone record",
        compute='_compute_lgd_po_line_id')
    lgd_set_incomplete = fields.Boolean(
        string="Set incomplete", compute='_compute_lgd_po_line_id')

    @api.depends('lgd_accepted_at')
    def _compute_lgd_po_line_id(self):
        """Most recent purchase line for this stone that is still alive.

        sudo(): Inventory holds no access to purchase.order.line beyond the
        Operations screens, and this only surfaces the QC set flag (§4.6.2).
        """
        po_lines = self.env['purchase.order.line'].sudo().search(
            [('sale_line_id', 'in', self.ids),
             ('lgd_stage', 'not in',
              ('rejected', 'failed', 'returned', 'split'))],
            order='id desc')
        by_sale_line = {}
        for po_line in po_lines:
            by_sale_line.setdefault(po_line.sale_line_id.id, po_line)
        for line in self:
            po_line = by_sale_line.get(line.id)
            line.lgd_po_line_id = po_line
            line.lgd_set_incomplete = bool(po_line and po_line.lgd_set_incomplete)

    # ── Shared operations helpers (§3.8) ────────────────────────────────
    # Two-line delegates onto models/lgd_ops_mixin.py. The logic itself lives
    # in one place so the de-duplication cannot drift between models.
    @api.model
    def _lgd_responsible_user(self, param_key, group_xmlid):
        return lgd_responsible_user(self.env, param_key, group_xmlid)

    def _lgd_notify(self, record, summary, note, user):
        return lgd_notify(self.env, record, summary, note, user)

    # ── Helpers ─────────────────────────────────────────────────────────
    def _lgd_label(self):
        """How a stone is named in an error: certificate first, else product."""
        self.ensure_one()
        return (self.certificate or self.product_id.display_name
                or _('Stone'))

    def _lgd_check_group(self, group_xmlid):
        if self.env.su or self.env.user.has_group(group_xmlid) \
                or self.env.user.has_group('base.group_system'):
            return
        raise AccessError(_(
            "You are not allowed to perform this Inventory action. "
            "Ask an administrator for the right role."))

    def _lgd_guard(self, problems):
        """R7 — collect every failing stone, one line each, then raise once."""
        self.invalidate_recordset(['lgd_in_vault', 'lgd_to_dispatch'])
        found = []
        for line in self:
            for message in problems(line):
                found.append("%s: %s" % (line._lgd_label(), message))
        if found:
            raise UserError("\n".join(found))

    # ── §5.2 Placed in vault ────────────────────────────────────────────
    def action_lgd_place_in_vault(self):
        self._lgd_check_group(GROUP_INVENTORY)
        return self._lgd_action_place_in_vault()

    def _lgd_action_place_in_vault(self):
        def problems(line):
            if not line.lgd_accepted_at:
                yield _("This stone has not been accepted into stock yet.")
            elif line.lgd_in_vault:
                yield _("Already vaulted by %(who)s on %(when)s.") % {
                    'who': line.lgd_vault_by.name or _('someone'),
                    'when': fields.Datetime.to_string(line.lgd_vault_at),
                }

        self._lgd_guard(problems)
        now = fields.Datetime.now()
        self.write({
            'lgd_in_vault': True,
            'lgd_vault_by': self.env.uid,
            'lgd_vault_at': now,
        })

        # Alert Dispatch once per order, not once per stone (§5.2 step 2).
        responsible = self._lgd_responsible_user(
            'lgd.dispatch_responsible_login', GROUP_SHIPMENT)
        for order in self.mapped('order_id'):
            vaulted = order.order_line.filtered('lgd_in_vault')
            self._lgd_notify(
                order,
                _('Stones ready for dispatch'),
                _("Invoice %(invoice)s: %(count)s stone(s) are now in the "
                  "vault.") % {
                    'invoice': order.sdk_augmont_number or order.name,
                    'count': len(vaulted),
                },
                responsible)

        # §5.2 step 3 (order._lgd_try_create_invoice) belongs to §6 and is not
        # wired yet — see the build note. Nothing here touches accounting.
        return True

    # ── §5.3 Handed to Dispatch ─────────────────────────────────────────
    def action_lgd_hand_to_dispatch(self):
        self._lgd_check_group(GROUP_INVENTORY)
        return self._lgd_action_hand_to_dispatch()

    def _lgd_action_hand_to_dispatch(self):
        def problems(line):
            if not line.lgd_in_vault:
                yield _("Put the stone in the vault first.")
            elif line.lgd_to_dispatch:
                yield _("Already with dispatch since %s.") % (
                    fields.Datetime.to_string(line.lgd_to_dispatch_at))

        self._lgd_guard(problems)
        self.write({
            'lgd_to_dispatch': True,
            'lgd_to_dispatch_by': self.env.uid,
            'lgd_to_dispatch_at': fields.Datetime.now(),
        })
        return True

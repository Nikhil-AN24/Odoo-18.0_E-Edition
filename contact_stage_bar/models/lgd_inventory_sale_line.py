# -*- coding: utf-8 -*-
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

    # ── Custody ──────────────────────────────────────────────────
    lgd_accepted_at = fields.Datetime(
        string="Accepted into stock", copy=False, index=True)
    lgd_in_vault = fields.Boolean(string="In vault", copy=False)
    lgd_vault_by = fields.Many2one(
        'res.users', string="Vaulted by", copy=False)
    lgd_vault_at = fields.Datetime(string="Vaulted on", copy=False)
    lgd_to_dispatch = fields.Boolean(string="With dispatch", copy=False)
    lgd_to_dispatch_by = fields.Many2one('res.users', copy=False)
    lgd_to_dispatch_at = fields.Datetime(copy=False)

    lgd_vendor_memo = fields.Boolean(
        string="On vendor memo", copy=False, index=True,
        help="The stone is in our custody but still belongs to the vendor: "
             "its purchase order is an open RFQ, so nothing is in stock and "
             "nothing is payable. Confirming the PO buys it in and clears "
             "this flag.")

    lgd_invoice_number = fields.Char(
        related='order_id.sdk_augmont_number', store=True, index=True,
        string="Invoice No.")

    # The Operations record for this stone. Computed rather than stored: the
    # link is owned by the purchase side, and a stale copy here would send a stone through the flow under the wrong set.
    lgd_po_line_id = fields.Many2one(
        'purchase.order.line', string="Stone record",
        compute='_compute_lgd_po_line_id')
    lgd_set_incomplete = fields.Boolean(
        string="Set incomplete", compute='_compute_lgd_po_line_id')

    lgd_set_label = fields.Char(
        string="Set Label", compute='_compute_lgd_po_line_id')
    lgd_qc_decided_at = fields.Datetime(
        string="QC Decided On", compute='_compute_lgd_po_line_id')
    lgd_inr_currency_id = fields.Many2one(
        'res.currency', compute='_compute_lgd_po_line_id')
    lgd_po_total = fields.Monetary(
        string="Pricing", compute='_compute_lgd_po_line_id',
        currency_field='lgd_inr_currency_id')

    @api.depends('lgd_accepted_at')
    def _compute_lgd_po_line_id(self):

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
            line.lgd_set_label = po_line.lgd_set_label if po_line else False
            line.lgd_qc_decided_at = po_line.lgd_qc_decided_at if po_line else False
            line.lgd_inr_currency_id = po_line.inr_currency_id if po_line else False
            line.lgd_po_total = po_line.lgd_po_total if po_line else 0.0

    # ── Shared operations helpers ────────────────────────────────
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

    # ── Into the vault ──────────────────────────────────────────────────
    def _lgd_put_in_vault(self):

        fresh = self.filtered(lambda line: not line.lgd_in_vault)
        if not fresh:
            return True

        fresh.write({
            'lgd_in_vault': True,
            'lgd_vault_by': self.env.uid,
            'lgd_vault_at': fields.Datetime.now(),
        })

        # Alert Dispatch once per order, not once per stone.
        responsible = self._lgd_responsible_user(
            'lgd.dispatch_responsible_login', GROUP_SHIPMENT)
        for order in fresh.mapped('order_id'):
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
        return True

    # ── Handed to Dispatch ─────────────────────────────────────────
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

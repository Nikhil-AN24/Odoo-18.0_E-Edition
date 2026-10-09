# -*- coding: utf-8 -*-
import logging
from collections import defaultdict
from odoo import _, api, fields, models
from .lgd_ops_mixin import lgd_notify, lgd_responsible_user
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)

GROUP_INVENTORY = 'contact_stage_bar.group_lgd_inventory'
GROUP_SHIPMENT = 'contact_stage_bar.group_lgd_shipment'

#: Line statuses that mean the stone will never ship on this order.
LGD_DEAD_STATUSES = (
    'cancelled', 'replaced', 'not_available', 'qc_fail', 'memo_returned')

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

    lgd_dispatch_id = fields.Many2one(
        'lgd.dispatch', string="Parcel", copy=False, index=True)
    lgd_ready_to_dispatch = fields.Boolean(
        compute='_compute_lgd_ready_to_dispatch', store=True,
        help="With Dispatch, not yet in a parcel, and still alive.")

    @api.depends('lgd_to_dispatch', 'lgd_dispatch_id', 'availability_status')
    def _compute_lgd_ready_to_dispatch(self):
        for line in self:
            line.lgd_ready_to_dispatch = bool(
                line.lgd_to_dispatch
                and not line.lgd_dispatch_id
                and line.availability_status not in LGD_DEAD_STATUSES)

    lgd_invoice_number = fields.Char(
        related='order_id.sdk_augmont_number', store=True, index=True,
        string="Invoice No.")

    # ── The memo outcome — written only by the memo return screen
    # never by hand. A stone with no outcome is still undecided,
    # which is exactly what makes the return wizard repeatable: it opens
    # with one row per stone that has none yet.
    lgd_memo_outcome = fields.Selection(
        [('kept', 'Kept by client'), ('returned', 'Returned')],
        string="Memo outcome", copy=False, index=True, readonly=True)
    lgd_memo_return_to = fields.Selection(
        [('stock', 'Back to our stock'), ('vendor', 'Back to vendor')],
        string="Returned to", copy=False, readonly=True,
        help="Where a returned stone goes: our own vault, or back to the "
             "vendor it was held from on memo. A fact of the stone's "
             "provenance, never a choice (R5).")
    lgd_memo_decided_by = fields.Many2one(
        'res.users', string="Memo decided by", copy=False, readonly=True)
    lgd_memo_decided_at = fields.Datetime(
        string="Memo decided on", copy=False, readonly=True)

    # ── Destinations ────────────────────────────────────
    lgd_ship_address_id = fields.Many2one(
        'res.partner', string="Deliver to", copy=False, index=True,
        help="Override the order's delivery address for this stone "
             "alone. Leave blank to follow the order's own delivery "
             "address.")
    lgd_ship_address_eff = fields.Many2one(
        'res.partner', string="Effective destination",
        compute='_compute_lgd_ship_address_eff', store=True, index=True,
        help="Where this stone will actually ship: its own override if "
             "set, else the order's delivery address, else the order's "
             "customer. Dispatch groups and searches on this field, so "
             "it must stay stored and never go stale.")
    lgd_ship_label = fields.Char(
        related='lgd_ship_address_eff.lgd_address_label', store=True,
        string="Destination")

    @api.depends('lgd_ship_address_id',
                 'order_id.partner_shipping_id', 'order_id.partner_id')
    def _compute_lgd_ship_address_eff(self):
        for line in self:
            line.lgd_ship_address_eff = (
                line.lgd_ship_address_id
                or line.order_id.partner_shipping_id
                or line.order_id.partner_id)

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
        currency_field='lgd_inr_currency_id',
        groups="contact_stage_bar.group_lgd_inventory,contact_stage_bar.group_lgd_shipment,base.group_system")

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

            # The draft invoice is prepared the moment every live
            # stone on the ORDER is in the vault. Readiness is per order,
            # never per parcel, so this is the right place for it: it is the
            # only moment readiness can turn true. Idempotent.
            order.sudo()._lgd_try_create_invoice()
        return True

    # ── Make up the parcels — one per destination ──────────────
    def action_lgd_make_parcels(self):
        self._lgd_check_group(GROUP_SHIPMENT)
        return self._lgd_action_make_parcels()

    def _lgd_action_make_parcels(self):
        """One press, one parcel per destination among the selected stones.

        Every problem is collected across every stone and raised once
        (R6/R7) before anything is created; nothing is saved if any of
        them fire. Replaces the old single-parcel
        ``_lgd_action_make_parcel``, which treated the whole order as one
        destination."""
        if not self:
            raise UserError(_("Select the stones to put in a parcel."))

        orders = self.mapped('order_id')
        if len(orders) > 1:
            raise UserError(_("Select stones from one order at a time."))
        order = orders

        found = []
        for line in self:
            if not line.lgd_ready_to_dispatch:
                found.append(_("%s: is not ready for dispatch.")
                              % line._lgd_label())
            if line.lgd_set_incomplete:
                found.append(_(
                    "%s: is part of a set that is waiting for a "
                    "replacement.") % line._lgd_label())
            destination = line.lgd_ship_address_eff
            if not (destination.street or destination.city):
                found.append(_("%s: its delivery address is incomplete.")
                              % line._lgd_label())
        if found:
            raise UserError("\n".join(found))

        # Group the selected stones by their EFFECTIVE destination — never
        # the raw override (lgd_ship_address_id): two stones whose override
        # happens to equal the order's own default address are one
        # destination, not two.
        groups = defaultdict(lambda: self.browse())
        for line in self:
            groups[line.lgd_ship_address_eff.id] |= line

        # One batch reference for the whole press, shared by every parcel
        # it makes (R2 — resolved from a sequence, never a literal id).
        batch_ref = self.env['ir.sequence'].sudo().next_by_code(
            'lgd.dispatch.batch') or '/'

        Dispatch = self.env['lgd.dispatch'].sudo()
        parcels = Dispatch.browse()
        for destination_id, group_lines in groups.items():
            destination = self.env['res.partner'].sudo().browse(destination_id)
            approval = Dispatch._lgd_approval_state_for(
                order, destination, group_lines)
            parcel = Dispatch.create({
                'order_id': order.id,
                'approval_state': approval,
                'lgd_batch_ref': batch_ref,
                'ship_name': destination.name,
                'ship_street': destination.street,
                'ship_street2': destination.street2,
                'ship_city': destination.city,
                'ship_state': destination.state_id.name,
                'ship_zip': destination.zip,
                'ship_country': destination.country_id.name,
                'ship_phone': destination.phone or destination.mobile,
                'ship_address_id': destination.id,
                'ship_label': (group_lines[0].lgd_ship_label
                               or destination.city or destination.name),
                'invoice_number': order.sdk_augmont_number,
            })
            group_lines.sudo().write({'lgd_dispatch_id': parcel.id})
            parcels |= parcel

            if approval == 'pending':
                parcel.sudo().message_post(body=_(
                    "Partial dispatch: %(sending)s stone(s) are going to "
                    "%(destination)s. Waiting for the Operations head to "
                    "approve."
                ) % {'sending': len(group_lines),
                     'destination': parcel.ship_label or parcel.ship_city})

        # Dispatch must see that more than one parcel was made R-emphatic
        # a form on the first of several would hide the rest.
        if len(parcels) > 1:
            return {
                'type': 'ir.actions.act_window',
                'name': _('Parcels'),
                'res_model': 'lgd.dispatch',
                'view_mode': 'list,form',
                'domain': [('id', 'in', parcels.ids)],
                'target': 'current',
            }
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'lgd.dispatch',
            'res_id': parcels.id,
            'view_mode': 'form',
            'target': 'current',
        }

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


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    lgd_partial_dispatch_allowed = fields.Boolean(
        copy=False,
        help="Set when the Operations head approves a partial send, so later "
             "parcels on the same order do not ask again.")
    lgd_partial_approved_by = fields.Many2one('res.users', copy=False)
    lgd_partial_approved_at = fields.Datetime(copy=False)

    # ── Destinations ────────────────────────────────────
    lgd_destination_count = fields.Integer(
        compute='_compute_lgd_destination_count', store=True,
        help="Number of distinct EFFECTIVE delivery destinations across "
             "this order's live stones. A stone override that resolves "
             "to the same address as the order default still counts as "
             "one destination, not two.")
    lgd_is_multi_dest = fields.Boolean(
        compute='_compute_lgd_destination_count', store=True,
        help="True when this order's live stones are headed to more "
             "than one effective destination.")

    @api.depends('order_line.lgd_ship_address_eff',
                 'order_line.availability_status')
    def _compute_lgd_destination_count(self):
        for order in self:
            live_lines = order.order_line.filtered(
                lambda l: l.product_id
                and l.availability_status not in LGD_DEAD_STATUSES)
            # Count distinct EFFECTIVE destinations, not raw overrides: an
            # override that resolves to the same address as the order
            # default is one destination, not two.
            destinations = set(live_lines.mapped('lgd_ship_address_eff').ids)
            order.lgd_destination_count = len(destinations)
            order.lgd_is_multi_dest = len(destinations) > 1
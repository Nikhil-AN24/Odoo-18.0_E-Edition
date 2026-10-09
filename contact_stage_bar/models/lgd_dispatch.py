# -*- coding: utf-8 -*-
import logging
from collections import defaultdict
from datetime import datetime, time
from markupsafe import Markup

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError
from .lgd_ops_mixin import lgd_notify, lgd_responsible_user
_logger = logging.getLogger(__name__)

GROUP_SHIPMENT = 'contact_stage_bar.group_lgd_shipment'

#: Statuses that mean a stone will never go out on this order.
LGD_DEAD_STATUSES = (
    'cancelled', 'replaced', 'not_available', 'qc_fail', 'memo_returned')

class LgdDispatch(models.Model):
    _name = 'lgd.dispatch'
    _description = 'Dispatch Parcel'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(required=True, copy=False, readonly=True, default='/')
    order_id = fields.Many2one(
        'sale.order', required=True, index=True, copy=False)
    line_ids = fields.One2many(
        'sale.order.line', 'lgd_dispatch_id', string="Stones")
    stone_count = fields.Integer(compute='_compute_stone_count', store=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('handed_over', 'Handed over'),
        ('delivered', 'Delivered'),
        ('memo_closed', 'Memo closed'),
        ('cancelled', 'Cancelled'),
    ], default='draft', tracking=True)

    # ── Sale or memo — chosen before handover ────────────────────
    dispatch_type = fields.Selection(
        [('sale', 'Sale'), ('memo', 'Memo')],
        default='sale', required=True, tracking=True)
    memo_due_date = fields.Date(string="Return by", tracking=True)
    lgd_is_memo_doc = fields.Boolean(
        string="Is or was a memo", compute='_compute_lgd_is_memo_doc',
        help="True while this parcel is a memo AND after it has closed as "
             "one. Once a memo closes with stones kept, dispatch_type flips "
             "to 'sale' - but the parcel still WENT OUT on approval, so its "
             "memo slip must stay printable and its memo history must stay "
             "visible. Everything that asks 'was this a memo?' keys on this "
             "rather than on dispatch_type.")

    @api.depends('dispatch_type', 'state')
    def _compute_lgd_is_memo_doc(self):
        for parcel in self:
            parcel.lgd_is_memo_doc = (
                parcel.dispatch_type == 'memo'
                or parcel.state == 'memo_closed')
    memo_slip_printed = fields.Boolean(readonly=True)
    memo_decided_at = fields.Datetime(readonly=True)
    memo_decided_by = fields.Many2one('res.users', readonly=True)
    memo_overdue = fields.Boolean(
        compute='_compute_memo_overdue', search='_search_memo_overdue')
    lgd_memo_decided_count = fields.Integer(
        string="Decided", compute='_compute_lgd_memo_decided',
        help="How many stones on this memo already have an outcome.")
    lgd_memo_undecided_count = fields.Integer(
        string="Undecided", compute='_compute_lgd_memo_decided')

    # ── Route and carrier ───────────────────────────────────────────────
    route = fields.Selection(
        [('courier', 'Courier company')], tracking=True, default='courier',
        readonly=True)
    agent_id = fields.Many2one(
        'lgd.courier.agent', string="Handed to", tracking=True)
    agent_phone = fields.Char(related='agent_id.phone', store=True)
    estimated_delivery_date = fields.Date(
        string="Estimated Delivery Date", tracking=True,
        help="The date the carrier expects to deliver this parcel.")
    tracking_number = fields.Char()
    tracking_url = fields.Char()
    upload_slip = fields.Binary(
        string="Uploading Slip", attachment=True,
        help="Scanned courier slip or proof-of-handover document.")
    upload_slip_filename = fields.Char(string="Uploading Slip Filename")

    # ── Paperwork ───────────────────────────────────────────────────────
    invoice_move_id = fields.Many2one(
        'account.move', string="Invoice", copy=False)
    invoice_enclosed = fields.Boolean(string="Invoice enclosed")
    # A memo carries a memo slip, not an invoice, so it gets its own tick with
    # the same force as invoice_enclosed: handover refuses without it.
    memo_enclosed = fields.Boolean(string="Memo enclosed")
    invoice_slip_printed = fields.Boolean(
        string="Invoice Slip Printed", readonly=True)
    label_printed = fields.Boolean(readonly=True)

    # ── Who & when ────────────────────────────────────────────────────
    handed_over_by = fields.Many2one('res.users', readonly=True)
    handed_over_at = fields.Datetime(readonly=True)
    delivery_confirmed_by = fields.Many2one('res.users', readonly=True)
    delivery_confirmed_at = fields.Datetime(readonly=True)
    delivery_note = fields.Text()
    follow_up_count = fields.Integer(default=0, readonly=True)
    last_follow_up_on = fields.Date(readonly=True)
    picking_id = fields.Many2one(
        'stock.picking', copy=False, readonly=True)

    # ── Partial send approval ────────────────────────────────────
    partial_reason = fields.Text()
    approval_state = fields.Selection([
        ('not_required', 'Not required'),
        ('pending', 'Pending approval'),
        ('approved', 'Approved'),
        ('refused', 'Refused'),
    ], default='not_required', tracking=True)
    approved_by = fields.Many2one('res.users', readonly=True)
    approved_at = fields.Datetime(readonly=True)

    # ── Delivery details copied at creation — Dispatch sees no contacts ──
    ship_name = fields.Char(readonly=True)
    ship_street = fields.Char(readonly=True)
    ship_street2 = fields.Char(readonly=True)
    ship_city = fields.Char(readonly=True)
    ship_state = fields.Char(readonly=True)
    ship_zip = fields.Char(readonly=True)
    ship_country = fields.Char(readonly=True)
    ship_phone = fields.Char(readonly=True)
    invoice_number = fields.Char(readonly=True)

    # ── One destination per parcel  ─────────────────────────────
    ship_address_id = fields.Many2one(
        'res.partner', readonly=True, copy=False,
        groups="base.group_system",
        help="Server use only. Never shown to Dispatch (R3).")
    lgd_buyer_name = fields.Char(
        string="Buyer", readonly=True, store=True,
        compute='_compute_lgd_buyer_name',
        help="The customer this parcel belongs to. Stored as plain text: "
             "a stored compute is read straight from this table, so "
             "Dispatch never touches res.partner (R3), and the name stays "
             "as it was even if the account is renamed later.")
    lgd_buyer_phone = fields.Char(
        string="Buyer Phone", readonly=True, store=True,
        compute='_compute_lgd_buyer_phone',
        help="The customer account's own phone number, printed on the "
             "label so the courier has someone to call. Separate from "
             "ship_phone, which is whatever number the delivery address "
             "itself carries - a child delivery address often has none, "
             "since a delivery address may not hold person details.")
    ship_label = fields.Char(string="Destination", readonly=True)

    @api.depends('order_id')
    def _compute_lgd_buyer_phone(self):
        # sudo() and stored for the same reasons as lgd_buyer_name: R3
        # keeps res.partner away from Dispatch, and storing means every
        # later read is a plain column on lgd_dispatch.
        for parcel in self:
            customer = parcel.order_id.sudo().partner_id
            parcel.lgd_buyer_phone = customer.phone or customer.mobile or False

    @api.depends('order_id')
    def _compute_lgd_buyer_name(self):
        # sudo(): a Dispatch user has no res.partner access (R3). Because
        # the field is stored, this runs once on write and every later read
        # is just a column on lgd_dispatch.
        for parcel in self:
            parcel.lgd_buyer_name = \
                parcel.order_id.sudo().partner_id.display_name or False
    lgd_batch_ref = fields.Char(
        readonly=True, index=True, copy=False,
        help="Shared by every parcel made in one press of Make parcels.")
    lgd_sibling_count = fields.Integer(compute='_compute_lgd_parcel_position')
    lgd_parcel_index = fields.Integer(compute='_compute_lgd_parcel_position')
    lgd_parcel_total = fields.Integer(compute='_compute_lgd_parcel_position')
    lgd_parcel_position_label = fields.Char(
        string="Parcel", compute='_compute_lgd_parcel_position',
        help="\"Parcel 1 of 2\" when this order went out as several "
             "parcels in one batch, so the packer can tell the boxes "
             "apart. Empty for a single parcel, where it would only ever "
             "read \"1 of 1\".")
    packing_list_printed = fields.Boolean(readonly=True)
    memo_return_slip_printed = fields.Boolean(readonly=True)

    # ── Computes ────────────────────────────────────────────────────────
    @api.depends('line_ids')
    def _compute_stone_count(self):
        for parcel in self:
            parcel.stone_count = len(parcel.line_ids)

    @api.depends('order_id', 'state')
    def _compute_lgd_parcel_position(self):
        """Index and total over the order's non-cancelled parcels, ordered
        by id — "Parcel 1 of 2" on the label and the form."""
        orders = self.mapped('order_id')
        siblings_by_order = {
            order.id: self.search([
                ('order_id', '=', order.id),
                ('state', '!=', 'cancelled'),
            ], order='id')
            for order in orders
        }
        for parcel in self:
            siblings = siblings_by_order.get(parcel.order_id.id, self.browse())
            total = len(siblings)
            index = siblings.ids.index(parcel.id) + 1 if parcel.id in siblings.ids else 0
            parcel.lgd_parcel_total = total
            parcel.lgd_parcel_index = index
            parcel.lgd_sibling_count = total
            # Only worth showing when there is actually more than one box
            # to tell apart. On a single parcel it could only ever read  "1 of 1", which says nothing.
            parcel.lgd_parcel_position_label = _(
                "Parcel %(index)s of %(total)s",
                index=index, total=total) if total > 1 else False

    @api.depends('line_ids.lgd_memo_outcome')
    def _compute_lgd_memo_decided(self):
        """ the Decided column on Memos Out, so a half-decided memo is
        visible at a glance. Counted off the stones' own outcome, which is
        the only place the decision lives."""
        for parcel in self:
            stones = parcel.line_ids.filtered(lambda l: l.product_id)
            decided = stones.filtered(lambda l: l.lgd_memo_outcome)
            parcel.lgd_memo_decided_count = len(decided)
            parcel.lgd_memo_undecided_count = len(stones) - len(decided)

    @api.depends('dispatch_type', 'state', 'memo_due_date')
    def _compute_memo_overdue(self):
        today = fields.Date.context_today(self)
        for parcel in self:
            parcel.memo_overdue = bool(
                parcel.dispatch_type == 'memo'
                and parcel.state == 'handed_over'
                and parcel.memo_due_date and parcel.memo_due_date < today)

    def _search_memo_overdue(self, operator, value):
        """So the Memos Out list can filter on it."""
        today = fields.Date.context_today(self)
        overdue = [('dispatch_type', '=', 'memo'),
                   ('state', '=', 'handed_over'),
                   ('memo_due_date', '<', today)]
        wants_overdue = (operator in ('=', '==')) == bool(value)
        if wants_overdue:
            return overdue
        return ['!'] + ['&', '&'] + overdue

    # ── The partial-send rule, corrected ──────────────────────────
    def _lgd_approval_state_for(self, order, destination, sending_lines):
        """A parcel is partial only when a stone for THIS destination is
        left behind — not when the order has stones waiting for some
        other place entirely. Called once per destination group from
        ``action_lgd_make_parcels``, and again after a split.

        Replaces the old order-wide ``outstanding`` calculation that used
        to live in ``_lgd_action_make_parcel``: under that rule every
        multi-destination order stopped for an approval that carries no
        information, because stones waiting for a different city always
        looked like a partial send of this one.
        """
        outstanding = order.order_line.filtered(
            lambda l: l not in sending_lines
            and not l.lgd_dispatch_id
            and l.lgd_ship_address_eff == destination
            and l.availability_status not in LGD_DEAD_STATUSES
            and l.product_id)
        if not outstanding:
            return 'not_required'
        return 'approved' if order.lgd_partial_dispatch_allowed else 'pending'

    # ── Guards ──────────────────────────────────────────────────────────
    def _lgd_check_group(self):
        if self.env.su or self.env.user.has_group(GROUP_SHIPMENT) \
                or self.env.user.has_group('base.group_system'):
            return
        raise AccessError(_(
            "You are not allowed to perform this Dispatch action. "
            "Ask an administrator for the right role."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', '/') == '/':
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'lgd.dispatch') or '/'
        return super().create(vals_list)

    # ── Hand over to the courier ───────────────────────────────────
    def action_lgd_hand_over(self):
        self._lgd_check_group()
        return self._lgd_action_hand_over()

    def _lgd_action_hand_over(self):
        """Every check first, collected into one error (R7), then one
        transaction (R8)."""
        self.ensure_one()
        problems = []
        if self.state != 'draft':
            problems.append(_("This parcel has already been handed over."))
        if self.approval_state not in ('not_required', 'approved'):
            problems.append(_("This partial dispatch is waiting for approval."))

        if not self.agent_id:
            problems.append(_("Choose who is carrying it."))

        if self.dispatch_type == 'memo':
            if not self.memo_due_date:
                problems.append(_("Set the return-by date for this memo."))
            elif self.memo_due_date < fields.Date.context_today(self):
                problems.append(_(
                    "The return date has already passed. Set a date in the "
                    "future."))
            if not self.memo_slip_printed:
                problems.append(_("Print the memo slip first."))
            if not self.memo_enclosed:
                problems.append(
                    _("Tick that the memo slip is enclosed before handing over."))
        else:
            if not self.invoice_slip_printed:
                problems.append(_("Print the invoice slip first."))
            if not self.invoice_enclosed:
                problems.append(
                    _("Tick that the paperwork is enclosed before handing over."))
        if not self.label_printed:
            problems.append(_("Print the label first."))
        if problems:
            raise UserError("\n".join(problems))

        # Move the stock before anything is written (R7 — one transaction per
        # click; nothing is saved if this raises).
        picking = self._lgd_create_outbound_picking()

        # Only a sale parcel carries an invoice. A memo is not a sale:
        # nothing is prepared and nothing is posted here (Task 12 invoices the
        # stones the customer keeps, at the return decision).
        invoice = self.env['account.move'].sudo().browse()
        if self.dispatch_type == 'sale':
            order = self.order_id.sudo()
            # The order may only have become ready AFTER an earlier partial
            # parcel left with a packing list and no invoice.
            # No-op when it is not ready, or when an invoice already exists.
            order._lgd_try_create_invoice()
            draft = order._lgd_open_draft_invoice()
            if draft:
                # First parcel on a ready order: this posts it.
                draft._lgd_post_with_platform_number()
                invoice = draft
            else:
                # Either the invoice is already posted (a later parcel on the
                # same order — it still prints a copy) or the order is not
                # ready yet and this parcel travels with a packing list only.
                invoice = order.invoice_ids.filtered(
                    lambda move: move.move_type == 'out_invoice'
                    and move.state == 'posted')[:1]

        now = fields.Datetime.now()
        handover_vals = {
            'state': 'handed_over',
            'picking_id': picking.id,
            'handed_over_by': self.env.uid,
            'handed_over_at': now,
        }
        if invoice:
            # Which invoice this parcel carries a copy of. Never cleared: a
            # parcel that left without one keeps whatever it already had.
            handover_vals['invoice_move_id'] = invoice.id
        self.write(handover_vals)
        self.line_ids.sudo().write({'availability_status': 'dispatched'})

        # The existing website push reads these off the order.
        order_vals = {'courier_partner_name': self.agent_id.name}
        if self.tracking_number:
            order_vals['tracking_number'] = self.tracking_number
        if self.tracking_url:
            order_vals['tracking_url'] = self.tracking_url
        self.order_id.sudo().write(order_vals)

        self.sudo().message_post(body=_(
            "Handed to %(agent)s (%(route)s) by %(user)s."
        ) % {'agent': self.agent_id.name,
             # `or self.route` so a legacy value that no longer has a
             # label - parcels still holding 'angadia' - posts the raw code rather than the word "None".
             'route': (dict(self._fields['route'].selection).get(self.route)
                       or self.route or ''),
             'user': self.env.user.name})
        return True

    # ── Outbound stock, at handover ─────────────────────────────────────
    def _lgd_warehouse(self):
        """Resolve the warehouse from the order's own company (R2 — never
        hardcode a database id)."""
        self.ensure_one()
        company = self.order_id.company_id
        warehouse = self.env['stock.warehouse'].sudo().search(
            [('company_id', '=', company.id)], limit=1)
        if not warehouse:
            raise UserError(_(
                "No warehouse is configured for %s. Ask an administrator to "
                "set one up before this parcel can be dispatched."
            ) % company.display_name)
        return warehouse

    def _lgd_create_outbound_picking(self):
        """One outbound stock transfer per parcel: the warehouse's stock to
        the customer, one move per stone on the parcel. Validated
        immediately so stock on hand drops for real. Task 12 mirrors
        this for the inward (memo return) direction via the same
        ``_lgd_create_picking`` helper, direction-parametrised.

        Cross-module writes use sudo(): Dispatch holds no rights on
        stock.picking / stock.move at all."""
        self.ensure_one()
        lines = self._lgd_stock_lines()

        if not lines:
            return self.env['stock.picking'].sudo().browse()
        warehouse = self._lgd_warehouse()
        customers = self.env.ref('stock.stock_location_customers')
        return self._lgd_create_picking(
            location_id=warehouse.lot_stock_id,
            location_dest_id=customers,
            picking_type_id=warehouse.out_type_id,
            lines=lines,
            check_availability=True,
        )

    def _lgd_stock_lines(self):

        self.ensure_one()
        return self.line_ids.filtered(
            lambda line: line.product_id and not line.lgd_vendor_memo)

    # ── Inward stock, at the memo return ──────────────────
    def _lgd_return_memo_stones(self, lines, received_on=None):

        self.ensure_one()
        if not lines:
            return self.env['stock.picking'].sudo().browse()
        warehouse = self._lgd_warehouse()
        customers = self.env.ref('stock.stock_location_customers')
        picking = self._lgd_create_picking(
            location_id=customers,
            location_dest_id=warehouse.lot_stock_id,
            picking_type_id=warehouse.in_type_id,
            lines=lines,
            check_availability=False,
        )
        if picking and received_on:
            picking.sudo().write({
                'date_done': datetime.combine(
                    fields.Date.to_date(received_on), time.min),
            })
        return picking

    def _lgd_create_picking(self, location_id, location_dest_id,
                             picking_type_id, lines, check_availability=True):

        self.ensure_one()
        # No empty-``lines`` guard here (reverted — review F2): the two
        # legitimate empty-set cases (an all-vendor-memo handover, an
        # all-kept memo return) are each guarded at their own call site
        # (``_lgd_create_outbound_picking``, ``_lgd_return_memo_stones``).
        # A parcel that reaches this shared helper with no lines through any
        # other path is a bug, and ``button_validate`` on a move-less
        # picking already raises loudly — silently handing back an empty
        # recordset here would instead let such a parcel hand over with nothing moved at all.
        if check_availability:
            Quant = self.env['stock.quant'].sudo()
            shortages = []
            for line in lines:
                needed = line.product_uom_qty or 1.0
                available = Quant._get_available_quantity(
                    line.product_id, location_id)
                if available < needed:
                    shortages.append(_(
                        "%(stone)s: only %(available)s in stock at "
                        "%(location)s, %(needed)s needed."
                    ) % {'stone': line._lgd_label(), 'available': available,
                         'location': location_id.display_name,
                         'needed': needed})
            if shortages:
                shortages.append(_("Nothing was saved."))
                raise UserError("\n".join(shortages))

        picking = self.env['stock.picking'].sudo().create({
            'picking_type_id': picking_type_id.id,
            'location_id': location_id.id,
            'location_dest_id': location_dest_id.id,
            'origin': self.name,
            'sale_id': self.order_id.id,
            'lgd_dispatch_id': self.id,
        })
        move_for_line = {}
        for line in lines:
            move = self.env['stock.move'].sudo().create({
                'name': line._lgd_label(),
                'product_id': line.product_id.id,
                'product_uom_qty': line.product_uom_qty or 1.0,
                'product_uom': (line.product_uom.id
                                or line.product_id.uom_id.id),
                'location_id': location_id.id,
                'location_dest_id': location_dest_id.id,
                'picking_id': picking.id,
                'picking_type_id': picking_type_id.id,
                'company_id': picking_type_id.company_id.id,
                'sale_line_id': line.id,
            })
            move_for_line[move.id] = line

        picking.sudo().with_context(
            lgd_ops_flow=True, skip_backorder=True, skip_sms=True,
        ).button_validate()

        failed = picking.move_ids.filtered(lambda m: m.state != 'done')
        if failed:
            lines = [
                _("%s: stock could not be moved.")
                % move_for_line[move.id]._lgd_label()
                for move in failed
            ]
            lines.append(_("Nothing was saved."))
            raise UserError("\n".join(lines))
        return picking

    # ──Confirm delivery ───────────────────────────────────────────
    def action_lgd_confirm_delivery(self):
        self._lgd_check_group()
        return self._lgd_action_confirm_delivery()

    def _lgd_action_confirm_delivery(self, note=None):
        self.ensure_one()
        if self.state != 'handed_over':
            raise UserError(_("Only a handed-over parcel can be confirmed."))
        self.write({
            'state': 'delivered',
            'delivery_confirmed_by': self.env.uid,
            'delivery_confirmed_at': fields.Datetime.now(),
            'delivery_note': note or self.delivery_note,
        })
        self.line_ids.sudo().write({'availability_status': 'delivered'})

        self.activity_ids.filtered(
            lambda a: a.summary == self.LGD_SALE_CHASE_SUMMARY % self.name
        ).unlink()

        body = _(
            "Delivery confirmed by %(user)s. Handed over %(out)s, "
            "confirmed %(in)s%(chases)s."
        ) % {
            'user': self.env.user.name,
            'out': self.handed_over_at or _("(not recorded)"),
            'in': self.delivery_confirmed_at,
            'chases': (_(" after %s follow-up(s)") % self.follow_up_count
                       if self.follow_up_count else ''),
        }
        if self.dispatch_type == 'memo':
            body += _(" This closes memo %s.") % self.name
        if self.delivery_note:
            body += Markup("<br/>") + _("Note: %s") % self.delivery_note
        self.sudo().message_post(body=body)
        return True

    # ── The parcel came back ──────────────────────────────────────
    lgd_has_returned_lines = fields.Boolean(
        compute='_compute_lgd_has_returned_lines',
        string="Has returned stones")

    @api.depends('line_ids.availability_status')
    def _compute_lgd_has_returned_lines(self):
        for parcel in self:
            parcel.lgd_has_returned_lines = any(
                line.availability_status == 'return_of_order'
                for line in parcel.line_ids)

    def action_lgd_return_of_order(self):
        self._lgd_check_group()
        self.ensure_one()
        if self.state not in ('handed_over', 'delivered'):
            raise UserError(_(
                "Only a parcel that has left the building can come back. "
                "This one is still in %s.")
                % dict(self._fields['state'].selection).get(self.state))
        self.line_ids.sudo().write({'availability_status': 'return_of_order'})
        if self.state == 'delivered':
            self.sudo().write({
                'state': 'handed_over',
                'delivery_confirmed_by': False,
                'delivery_confirmed_at': False,
            })
        self.sudo().message_post(body=_(
            "Returned to us — %(count)s stone(s) marked Return of Order by "
            "%(user)s."
        ) % {'count': len(self.line_ids), 'user': self.env.user.name})
        return True

    def action_lgd_re_dispatch(self):
        """Send a returned parcel out again."""
        self._lgd_check_group()
        self.ensure_one()
        returned = self.line_ids.filtered(
            lambda l: l.availability_status == 'return_of_order')
        if not returned:
            raise UserError(_(
                "Nothing on this parcel has come back, so there is nothing to "
                "send out again."))
        returned.sudo().write({'availability_status': 're_dispatched'})
        self.sudo().message_post(body=_(
            "Re-dispatched — %(count)s stone(s) sent out again by %(user)s."
        ) % {'count': len(returned), 'user': self.env.user.name})
        return True

    # ── Complete the order ────────────────────────────────────────
    def action_lgd_complete_order(self):
        self._lgd_check_group()
        self.ensure_one()
        problems = []
        if self.state != 'delivered':
            problems.append(_("Confirm delivery before completing the order."))
        order = self.order_id.sudo()
        if not order:
            problems.append(_("This parcel is not linked to an order."))
        else:
            settled = ('delivered', 'cancelled', 'replaced', 'order_completed')
            outstanding = order.order_line.filtered(
                lambda l: l.availability_status not in settled)
            if outstanding:
                problems.append(_(
                    "%(count)s stone(s) on %(order)s are still in flight, so "
                    "the order is not finished yet."
                ) % {'count': len(outstanding),
                     'order': order.name})
            # The payment leg runs alongside dispatch, not inside it. An order
            # is only complete once the money is in, evidenced by the UTR captured at Payment Completed.
            if 'utr_number' in order._fields and not (order.utr_number or '').strip():
                problems.append(_(
                    "Payment is not recorded for %s. Confirm the payment with "
                    "its UTR number before completing the order."
                ) % order.name)
        if problems:
            raise UserError("\n".join(problems))

        to_close = order.order_line.filtered(
            lambda l: l.availability_status == 'delivered')
        to_close.write({'availability_status': 'order_completed'})
        self.sudo().message_post(body=_(
            "Order completed by %(user)s — %(count)s stone(s) closed."
        ) % {'user': self.env.user.name, 'count': len(to_close)})
        return True

    # ── Cancel a parcel ───────────────────────────────────────────
    def action_lgd_cancel(self):
        self._lgd_check_group()
        return self._lgd_action_cancel()

    def _lgd_action_cancel(self):
        """Unlinks the stones so they return to Ready to Dispatch.

        It does NOT cancel or amend the invoice: a handed-over parcel that
        comes back is a customer return, and any credit note is Accounting's to raise.
        """
        self.ensure_one()
        if self.state != 'draft':
            raise UserError(_(
                "Only a draft parcel can be cancelled. A parcel that has been "
                "handed over is a customer return."))
        self.line_ids.sudo().write({'lgd_dispatch_id': False})
        self.write({'state': 'cancelled'})
        return True

    # ── Approve or refuse a partial send ───────────────────────────
    def action_lgd_approve_partial(self):
        self._lgd_check_ops_head()
        self.ensure_one()
        if self.approval_state != 'pending':
            raise UserError(_("This parcel is not waiting for approval."))
        self.write({
            'approval_state': 'approved',
            'approved_by': self.env.uid,
            'approved_at': fields.Datetime.now(),
        })
        # Later parcels on this order do not ask again.
        self.order_id.sudo().write({
            'lgd_partial_dispatch_allowed': True,
            'lgd_partial_approved_by': self.env.uid,
            'lgd_partial_approved_at': fields.Datetime.now(),
        })
        self.sudo().message_post(body=_("Partial dispatch approved by %s.")
                                 % self.env.user.name)
        return True

    def action_lgd_refuse_partial(self):
        self._lgd_check_ops_head()
        self.ensure_one()
        if self.approval_state != 'pending':
            raise UserError(_("This parcel is not waiting for approval."))
        self.write({'approval_state': 'refused'})
        self.sudo().message_post(body=_("Partial dispatch refused by %s.")
                                 % self.env.user.name)
        return True

    def _lgd_check_ops_head(self):
        """The Operations head, named by a system parameter until the
        Procurement hierarchy exists."""
        if self.env.su or self.env.user.has_group('base.group_system'):
            return
        head = lgd_responsible_user(
            self.env, 'lgd.ops_head_login', GROUP_SHIPMENT)
        if head and head.id == self.env.uid:
            return
        raise AccessError(_(
            "Only the Operations head may approve or refuse a partial "
            "dispatch."))

    # ── Print the memo slip ────────────────────────────────────────
    def action_lgd_print_memo_slip(self):
        """A memo parcel travels with a slip instead of an invoice.

        Printing creates no accounting record of any kind — the slip is simply
        a document saying what went out on approval and by when it must come
        back. Reprinting is always allowed.
        """
        self._lgd_check_group()
        self.ensure_one()

        if not self.lgd_is_memo_doc:
            raise UserError(_("Only a memo parcel has a memo slip."))
        if not self.memo_due_date:
            raise UserError(_("Set the return-by date before printing the slip."))
        self.sudo().write({'memo_slip_printed': True})
        return self.env.ref(
            'contact_stage_bar.action_lgd_memo_slip').report_action(self)

    def action_lgd_print_invoice_slip(self):
        self._lgd_check_group()
        self.ensure_one()
        if self.dispatch_type != 'sale':
            raise UserError(_("Only a sale parcel has an invoice slip."))
        if not self.order_id:
            raise UserError(_(
                "This parcel is not linked to an order, so there is nothing "
                "to invoice. Please report this with the parcel reference "
                "(%s).") % self.name)
        self.sudo().write({'invoice_slip_printed': True})
        return self.env.ref(
            'contact_stage_bar.action_report_augmont_tax_invoice'
        ).report_action(self.order_id)

    # ── Split a draft parcel ──────────────────────────────
    def _lgd_sync_destination_options(self):
        """Materialise this parcel's split destinations as records.

        See LgdDispatchDestinationOption for why these are records and not
        a dynamic Selection. Keyed on (dispatch, partner) so re-opening the
        wizard refreshes the labels rather than duplicating the rows.
        """
        self.ensure_one()
        Option = self.env['lgd.dispatch.destination.option'].sudo()
        order = self.order_id.sudo()

        customer = order.partner_id
        addresses = (
            order.order_line.mapped('lgd_ship_address_eff')
            | order.partner_shipping_id
            | customer.child_ids.filtered(lambda p: p.type == 'delivery')
        )
        seen = Option.browse()
        for address in addresses:
            if not address:
                continue
            label = (address.lgd_address_label or address.city
                     or address.display_name)
            option = Option.search([
                ('dispatch_id', '=', self.id),
                ('partner_id', '=', address.id),
            ], limit=1)
            if option:
                if option.name != label:
                    option.name = label
            else:
                option = Option.create({
                    'dispatch_id': self.id,
                    'partner_id': address.id,
                    'name': label,
                })
            seen |= option
        # Drop options for addresses no longer on the order, so a stale row cannot be picked.
        stale = Option.search([('dispatch_id', '=', self.id)]) - seen
        if stale:
            stale.unlink()
        return seen

    def action_lgd_open_split_wizard(self):
        self._lgd_check_group()
        self.ensure_one()
        if self.state != 'draft':
            raise UserError(_(
                "%s can no longer be split; it has already left Dispatch."
            ) % self.name)
        return {
            'type': 'ir.actions.act_window',
            'name': _('Split parcel'),
            'res_model': 'lgd.dispatch.split.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_dispatch_id': self.id},
        }

    # ── Record the memo return ──────────────────────
    def _lgd_memo_undecided_lines(self):
        """The stones on this memo that have no outcome yet.

        The whole repeatability of rests on this one filter: the wizard
        opens with one row per undecided stone, so a client who keeps 30 of
        100 today leaves 70 rows for next week, and the parcel closes only
        when this comes back empty.
        """
        self.ensure_one()
        return self.line_ids.filtered(
            lambda line: line.product_id and not line.lgd_memo_outcome)

    def action_lgd_open_memo_return(self):
        """The Record memo return button. Four refusals, collected
        and raised once (R6); nothing is created when any of them fire."""
        self.ensure_one()
        self._lgd_check_group()
        problems = []
        if self.dispatch_type != 'memo':
            problems.append(_("Only a memo parcel has a return to record."))
        if self.state not in ('handed_over', 'delivered'):
            problems.append(_("This memo has not been handed over yet."))
        undecided = self._lgd_memo_undecided_lines()
        if not undecided:
            problems.append(_("Every stone on this memo is already decided."))
        if problems:
            raise UserError("\n".join(problems))

        wizard = self.env['lgd.memo.return.wizard'].create({
            'dispatch_id': self.id,
            # One row per UNDECIDED stone, with no outcome on it: the user
            # chooses Kept or Returned for the ones being decided now and
            # removes the rows they are not deciding yet.
            'line_ids': [Command.create({'sale_line_id': line.id})
                         for line in undecided],
        })
        return {
            'type': 'ir.actions.act_window',
            'name': _('Record memo return'),
            'res_model': 'lgd.memo.return.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_dispatch_id': self.id},
        }

    # ── Print the label ────────────────────────────────────────────
    def action_lgd_print_label(self):
        self._lgd_check_group()
        self.ensure_one()
        self.sudo().write({'label_printed': True})
        return self.env.ref(
            'contact_stage_bar.action_lgd_dispatch_label').report_action(self)

    # ── Print the packing list ───────────────────────
    def action_lgd_print_packing_list(self):
        """The packing list travels inside every parcel: certificate,
        shape, carat, colour, clarity, the parcel position and the
        destination — no prices. Reprinting is always allowed."""
        self._lgd_check_group()
        self.ensure_one()
        self.sudo().write({'packing_list_printed': True})
        return self.env.ref(
            'contact_stage_bar.action_lgd_packing_list').report_action(self)

    # ── Print the memo return slip ────
    def action_lgd_print_memo_return_slip(self):
        """Lists only the stones already decided RETURNED, with their
        price, and the date each was decided. Also written by
        ``lgd.memo.return.wizard.action_record`` when a round closes the
        parcel; reprinting here is always allowed and keys on the same flag."""
        self._lgd_check_group()
        self.ensure_one()
        self.sudo().write({'memo_return_slip_printed': True})
        return self.env.ref(
            'contact_stage_bar.action_lgd_memo_return_slip').report_action(self)

    # ── The daily follow-up ──────────────────────────────
    #: The exact summary carried by an overdue-memo chase activity.
    #: memo-return close step (``wizard/lgd_dispatch_wizards.py``,
    #: ``action_record``) unlinks the follow-up activities it is responsible
    #: for clearing by matching this same text — the two must never drift
    #: apart, or closing a memo leaves its chase activity behind.
    LGD_MEMO_OVERDUE_SUMMARY = "Memo overdue — chase the return."

    #: Same reasoning as ``LGD_MEMO_OVERDUE_SUMMARY``, kept a plain literal
    #: (never wrapped in ``_()``) so this cron's write and
    #: ``_lgd_action_confirm_delivery``'s unlink filter always agree
    #: regardless of which language either runs in. Formatted with the
    #: parcel name via ``%`` at each use site.
    LGD_SALE_CHASE_SUMMARY = "Confirm delivery — %s"

    @api.model
    def _lgd_cron_follow_up(self):

        today = fields.Date.context_today(self)
        parcels = self._lgd_follow_up_candidates()
        for parcel in parcels:
            try:
                with self.env.cr.savepoint():
                    parcel._lgd_follow_up_one(today)
            except Exception:
                _logger.warning(
                    "Follow-up cron failed for dispatch %s (id %s); "
                    "skipping it and continuing.",
                    parcel.name, parcel.id, exc_info=True)

    def _lgd_follow_up_candidates(self):

        try:
            with self.env.cr.savepoint(flush=False):
                return self.sudo().search([('state', '=', 'handed_over')])
        except Exception:
            self.env.cr.clear()
            _logger.warning(
                "Follow-up cron: a pending change elsewhere on "
                "lgd.dispatch could not be flushed; retrying the search "
                "once.", exc_info=True)
        try:
            with self.env.cr.savepoint(flush=False):
                return self.sudo().search([('state', '=', 'handed_over')])
        except Exception:
            self.env.cr.clear()
            _logger.error(
                "Follow-up cron: the candidate search failed twice; "
                "skipping today's run.", exc_info=True)
            return self.sudo().browse()

    def _lgd_follow_up_one(self, today):
        """One parcel's share of the daily chase. Called only from
        ``_lgd_cron_follow_up``, each call wrapped in its own savepoint."""
        self.ensure_one()
        # Skip if already run today — this is what makes two cron runs (or
        # a manual + a scheduled one) in the same day chase once, for both
        # a sale and an overdue memo.
        if self.last_follow_up_on == today:
            return

        if self.dispatch_type == 'memo':
            # Nothing until the day AFTER memo_due_date.
            if not self.memo_due_date or self.memo_due_date >= today:
                return
            summary = self.LGD_MEMO_OVERDUE_SUMMARY
            note = _("Memo %(name)s is overdue for return — due %(due)s.") % {
                'name': self.name, 'due': self.memo_due_date}
        else:
            summary = self.LGD_SALE_CHASE_SUMMARY % self.name
            note = _(
                "%s has been handed over and is not yet confirmed "
                "delivered."
            ) % self.name

        responsible = lgd_responsible_user(
            self.env, 'lgd.dispatch_responsible_login', GROUP_SHIPMENT)
        lgd_notify(self.env, self.sudo(), summary, note, responsible)

        before = self.follow_up_count
        after = before + 1
        self.write({
            'follow_up_count': after,
            'last_follow_up_on': today,
        })

        # Notify the Operations head once, the run that pushes the count
        # past 3 (a before/after boundary check rather than ``== 4``, so a
        # count nudged by hand some other way still escalates exactly once).
        if before <= 3 < after:
            head = lgd_responsible_user(
                self.env, 'lgd.ops_head_login', GROUP_SHIPMENT)
            lgd_notify(
                self.env, self.sudo(),
                _("Dispatch %s needs attention — repeated follow-up") % self.name,
                _(
                    "%(name)s has now been chased %(count)s times with no "
                    "result."
                ) % {'name': self.name, 'count': after},
                head)

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    # The courier slip Dispatch uploads on the parcel, surfaced on the order
    # so Sales and Procurement can download the proof of handover without
    # being given access to the Dispatch app.
    #
    # Read-only by design: the file belongs to a parcel, and it is uploaded
    # there. That also satisfies the requirement that LGD Sales, LGD Sales
    # Manager, LGD Regional Sales Head and LGD Procurement get download-only
    # access - none of them can upload here, because nobody can.
    lgd_dispatch_slip = fields.Binary(
        string="Uploading Slip", readonly=True,
        compute='_compute_lgd_dispatch_slip',
        help="The courier slip uploaded by Dispatch against this order's "
             "parcel. Upload it in the Dispatch app; this is a download-only "
             "copy.")
    lgd_dispatch_slip_filename = fields.Char(
        compute='_compute_lgd_dispatch_slip')


    lgd_can_edit_logistics_fields = fields.Boolean(
        compute='_compute_lgd_can_edit_logistics_fields')

    @api.depends_context('uid')
    def _compute_lgd_can_edit_logistics_fields(self):
        user = self.env.user
        # group_lgd_sales alone covers the family: Sales Manager implies it,
        # and Regional Sales Head implies the Manager.
        allowed = not user.has_group('contact_stage_bar.group_lgd_sales')
        for order in self:
            order.lgd_can_edit_logistics_fields = allowed

    # ── Current USD rate, shown on the order ──────────────────────────
    lgd_usd_rate = fields.Float(
        string="Current $ Rate", digits=(12, 4), readonly=True,
        compute='_compute_lgd_usd_rate',
        help="Today's rate for 1 USD in this order's company currency, read "
             "from Accounting's currency rates. It is not stored on the "
             "order: it always shows the rate as of now, not the rate when "
             "the order was raised.")

    @api.depends_context('company')
    @api.depends('company_id')
    def _compute_lgd_usd_rate(self):
        usd = self.env.ref('base.USD', raise_if_not_found=False)
        today = fields.Date.context_today(self)
        for order in self:
            company = order.company_id or self.env.company
            target = company.currency_id
            if not usd or not target or usd == target:
                # A USD company: one dollar is one dollar.
                order.lgd_usd_rate = 1.0
                continue

            has_rate = self.env['res.currency.rate'].sudo().search_count([
                ('currency_id', '=', target.id),
                ('company_id', 'in', [False, company.id]),
                ('name', '<=', today),
            ])
            order.lgd_usd_rate = usd.sudo()._convert(
                1.0, target, company, today, round=False) if has_rate else 0.0

    def _compute_lgd_dispatch_slip(self):
        Dispatch = self.env['lgd.dispatch'].sudo()
        for order in self:
            parcel = Dispatch.search([
                ('order_id', '=', order.id),
                ('upload_slip', '!=', False),
            ], order='id desc', limit=1)
            order.lgd_dispatch_slip = parcel.upload_slip or False
            order.lgd_dispatch_slip_filename = (
                parcel.upload_slip_filename or False)
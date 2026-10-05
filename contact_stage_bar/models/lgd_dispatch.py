# -*- coding: utf-8 -*-
import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from .lgd_ops_mixin import lgd_notify, lgd_responsible_user
_logger = logging.getLogger(__name__)

GROUP_SHIPMENT = 'contact_stage_bar.group_lgd_shipment'

#: Statuses that mean a stone will never go out on this order.
LGD_DEAD_STATUSES = ('cancelled', 'replaced', 'not_available', 'qc_fail')


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
        ('cancelled', 'Cancelled'),
    ], default='draft', tracking=True)

    # ── Sale or memo — chosen before handover ────────────────────
    dispatch_type = fields.Selection(
        [('sale', 'Sale'), ('memo', 'Memo')],
        default='sale', required=True, tracking=True)
    memo_due_date = fields.Date(string="Return by", tracking=True)
    memo_slip_printed = fields.Boolean(readonly=True)
    memo_decided_at = fields.Datetime(readonly=True)
    memo_decided_by = fields.Many2one('res.users', readonly=True)
    memo_overdue = fields.Boolean(
        compute='_compute_memo_overdue', search='_search_memo_overdue')

    # ── Route and carrier ───────────────────────────────────────────────
    route = fields.Selection(
        [('courier', 'Courier'), ('angadia', 'Angadia')], tracking=True)
    agent_id = fields.Many2one(
        'lgd.courier.agent', string="Handed to", tracking=True)
    agent_phone = fields.Char(related='agent_id.phone', store=True)
    tracking_number = fields.Char()
    tracking_url = fields.Char()

    # ── Paperwork ───────────────────────────────────────────────────────
    invoice_move_id = fields.Many2one(
        'account.move', string="Invoice", copy=False)
    invoice_enclosed = fields.Boolean(string="Invoice enclosed")
    label_printed = fields.Boolean(readonly=True)

    # ── Who and when ────────────────────────────────────────────────────
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

    # ── Computes ────────────────────────────────────────────────────────
    @api.depends('line_ids')
    def _compute_stone_count(self):
        for parcel in self:
            parcel.stone_count = len(parcel.line_ids)

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

    # ── Hand over to the courier or Angadia ────────────────────────
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
        if not (self.route and self.agent_id
                and self.agent_id.agent_type == self.route):
            problems.append(_("Choose the route and who is carrying it."))
        if self.route == 'courier' and not self.tracking_number:
            problems.append(_("Enter the courier tracking number."))
        if self.dispatch_type == 'memo':
            if not self.memo_due_date:
                problems.append(_("Set the return-by date for this memo."))
            if not self.memo_slip_printed:
                problems.append(_("Print the memo slip first."))
        if not self.invoice_enclosed:
            problems.append(
                _("Tick that the memo slip is enclosed before handing over.")
                if self.dispatch_type == 'memo' else
                _("Tick that the paperwork is enclosed before handing over."))
        if not self.label_printed:
            problems.append(_("Print the label first."))
        if problems:
            raise UserError("\n".join(problems))

        now = fields.Datetime.now()
        self.write({
            'state': 'handed_over',
            'handed_over_by': self.env.uid,
            'handed_over_at': now,
        })
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
             'route': dict(self._fields['route'].selection).get(self.route),
             'user': self.env.user.name})
        return True

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
        # Clear the chasing activities — the loop is closed.
        self.activity_ids.filtered(
            lambda a: a.summary == _("Confirm delivery — %s") % self.name
        ).unlink()
        self._lgd_close_order_if_done()
        return True

    def _lgd_close_order_if_done(self):
        """When every line on the order is settled, the order is complete."""
        self.ensure_one()
        order = self.order_id.sudo()
        settled = ('delivered', 'cancelled', 'replaced', 'order_completed')
        active = order.order_line.filtered(
            lambda l: l.availability_status not in settled)
        if not active:
            order.order_line.filtered(
                lambda l: l.availability_status == 'delivered'
            ).write({'availability_status': 'order_completed'})

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
        if self.dispatch_type != 'memo':
            raise UserError(_("Only a memo parcel has a memo slip."))
        if not self.memo_due_date:
            raise UserError(_("Set the return-by date before printing the slip."))
        self.sudo().write({'memo_slip_printed': True})
        return self.env.ref(
            'contact_stage_bar.action_lgd_memo_slip').report_action(self)

    # ── Print the label ────────────────────────────────────────────
    def action_lgd_print_label(self):
        self._lgd_check_group()
        self.ensure_one()
        self.sudo().write({'label_printed': True})
        return self.env.ref(
            'contact_stage_bar.action_lgd_dispatch_label').report_action(self)

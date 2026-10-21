from odoo import api, fields, models
from .lgd_dispatch import LGD_DEAD_STATUSES

_LGD_MONEY_IN = ('paid', 'in_payment')

#: availability_status values at or past the point where the WEBSITE treats
#: the money as received. Everything from payment_completed onwards: the
#: platform does not dispatch goods it believes are unpaid.
_LGD_WEBSITE_PAID = (
    'payment_completed', 'dispatched', 're_dispatched', 'return_of_order',
    'delivered', 'memo_returned', 'order_completed',
)

class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    lgd_payment_state = fields.Selection([
        ('not_invoiced', 'Not Invoiced'),
        ('not_paid', 'Payment Pending'),
        ('partial', 'Partially Paid'),
        ('paid', 'Payment Completed'),
        ('reversed', 'Reversed'),
    ], string="Payment", compute='_compute_lgd_payment_state',
        store=True, index=True,
        help="Whether the money for THIS stone has arrived. Read from the "
             "invoice that bills it, so it always agrees with Accounting.")

    @api.depends('invoice_lines.move_id.state',
                 'invoice_lines.move_id.payment_state',
                 'invoice_lines.move_id.move_type')
    def _compute_lgd_payment_state(self):
        for line in self:
            moves = line.invoice_lines.mapped('move_id').filtered(
                lambda m: m.move_type == 'out_invoice' and m.state == 'posted')
            if not moves:
                line.lgd_payment_state = 'not_invoiced'
                continue

            states = set(moves.mapped('payment_state'))
            if states <= {'reversed'}:
                line.lgd_payment_state = 'reversed'
            elif states <= set(_LGD_MONEY_IN):
                line.lgd_payment_state = 'paid'
            elif states & (set(_LGD_MONEY_IN) | {'partial'}):
                # Some of the money is in, some is not. True both for a part-
                # paid invoice and for a stone billed across two invoices of which one is settled.
                line.lgd_payment_state = 'partial'
            else:
                line.lgd_payment_state = 'not_paid'


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    lgd_payment_state = fields.Selection([
        ('not_invoiced', 'Not Invoiced'),
        ('not_paid', 'Payment Pending'),
        ('partial', 'Partially Paid'),
        ('paid', 'Payment Completed'),
    ], string="Payment Status", compute='_compute_lgd_payment_state',
        store=True, index=True,
        help="Rolled up from the stones. 'Partially Paid' means the money has "
             "arrived for some stones on this order but not all of them.")

    lgd_unpaid_stone_count = fields.Integer(
        string="Stones Awaiting Payment",
        compute='_compute_lgd_payment_state', store=True,
        help="How many live stones on this order have not been paid for.")

    @api.depends('order_line.lgd_payment_state', 'order_line.display_type',
                 'order_line.availability_status')
    def _compute_lgd_payment_state(self):
        for order in self:
            # Dead stones are excluded. A cancelled or failed stone will never
            # be invoiced, and counting it would hold the order short of 'Paid' for ever however much money came in.
            lines = order.order_line.filtered(
                lambda l: not l.display_type
                and l.availability_status not in LGD_DEAD_STATUSES)
            states = lines.mapped('lgd_payment_state')

            order.lgd_unpaid_stone_count = len(
                [s for s in states
                 if s in ('not_paid', 'partial', 'not_invoiced')])

            if not states or all(s == 'not_invoiced' for s in states):
                order.lgd_payment_state = 'not_invoiced'
            elif all(s == 'paid' for s in states):
                # EVERY live stone, not merely every billed one. An order with
                # one stone paid and another not yet invoiced is not paid for.
                order.lgd_payment_state = 'paid'
            elif any(s in ('paid', 'partial') for s in states):
                order.lgd_payment_state = 'partial'
            else:
                order.lgd_payment_state = 'not_paid'

    lgd_payment_mismatch = fields.Boolean(
        string="Payment Mismatch", compute='_compute_lgd_payment_mismatch',
        store=True, index=True,
        help="The website says this order is paid but no invoice for it has "
             "been settled, or the reverse. Worth reconciling: one of the two "
             "records is wrong.")

    @api.depends('lgd_payment_state', 'order_line.availability_status',
                 'order_line.display_type')
    def _compute_lgd_payment_mismatch(self):
        for order in self:
            lines = order.order_line.filtered(
                lambda l: not l.display_type
                and l.availability_status not in LGD_DEAD_STATUSES)
            # What the website believes. 'payment_completed' is the furthest
            # the platform's own workflow goes on the money; anything at or
            # past dispatch implies the platform considered it settled.
            website_says_paid = bool(lines) and all(
                l.availability_status in _LGD_WEBSITE_PAID for l in lines)
            accounting_says_paid = order.lgd_payment_state == 'paid'
            order.lgd_payment_mismatch = (
                website_says_paid != accounting_says_paid
                and order.lgd_payment_state != 'not_invoiced')

    def action_lgd_open_payment_breakdown(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _lgd_breakdown_name(self),
            'res_model': 'sale.order.line',
            'view_mode': 'list',
            'domain': [('order_id', '=', self.id),
                       ('display_type', '=', False)],
            'context': {'search_default_group_payment': 1,
                        'create': False},
        }

def _lgd_breakdown_name(order):
    return "Payment breakdown — %s" % (
        order.sdk_augmont_number or order.name)

from odoo import fields, models


class CustomSaleOrderCancelWizard(models.TransientModel):
    _name = 'custom.sale.order.cancel.wizard'
    _description = 'Cancel Offline Sale Order'

    # Mirrors customer.rfq.cancel.wizard (contact_stage_bar): cancelling an
    # offline order goes through the same reason prompt as a Customer RFQ,
    # against the same master list of reasons.
    order_id = fields.Many2one('custom.sale.order', string='Order',
                               required=True, ondelete='cascade')
    reason_ids = fields.Many2many(
        'customer.rfq.cancel.reason', string='Cancellation Reason', required=True,
        help='Pick every reason that applies.')
    comment = fields.Text(
        string='Comment',
        help='Describe what happened, for whoever reviews cancelled orders later.')

    def action_confirm_cancel(self):
        self.ensure_one()
        self.order_id._apply_cancellation(self.reason_ids, self.comment)
        return {'type': 'ir.actions.act_window_close'}

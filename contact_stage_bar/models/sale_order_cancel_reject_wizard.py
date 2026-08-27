from odoo import fields, models

class SaleOrderCancelRejectWizard(models.TransientModel):
    _name = 'sale.order.cancel.reject.wizard'
    _description = 'Reject Sale Order Cancellation Request'

    order_id = fields.Many2one('sale.order', string='Sale Order',
                               required=True, ondelete='cascade')
    rejection_reason = fields.Text(
        string='Rejection Reason', required=True,
        help='Explain why the cancellation request is being turned down.')

    def action_confirm_reject(self):
        self.ensure_one()
        self.order_id._apply_cancel_rejection(self.rejection_reason)
        return {'type': 'ir.actions.act_window_close'}

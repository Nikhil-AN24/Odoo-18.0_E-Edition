from odoo import fields, models

class SaleOrderCancelRequestWizard(models.TransientModel):

    _name = 'sale.order.cancel.request.wizard'
    _description = 'Request Sale Order Cancellation'

    order_id = fields.Many2one('sale.order', string='Sale Order',
                               required=True, ondelete='cascade')
    reason_ids = fields.Many2many(
        'customer.rfq.cancel.reason', string='Cancellation Reason', required=True,
        help='Pick every reason that applies.')
    comment = fields.Text(
        string='Comment',
        help='Describe what happened, for whoever reviews the request.')

    def action_confirm_request(self):
        self.ensure_one()
        self.order_id._apply_cancel_request(self.reason_ids, self.comment)
        return {'type': 'ir.actions.act_window_close'}

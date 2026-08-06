from odoo import models, fields

class CustomSaleOrderCancelWizard(models.TransientModel):
    _name = 'custom.sale.order.cancel.wizard'
    _description = 'Cancellation Reason Wizard'

    order_id = fields.Many2one('custom.sale.order', string="Order")
    reason = fields.Text(string="Reason for Cancellation", required=True)

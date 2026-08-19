from odoo import models
from odoo.exceptions import UserError

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_acefone_click_to_call(self):
        self.ensure_one()
        partner = self.partner_id
        if not partner:
            raise UserError("This sales order does not have a Customer set.")
        return partner.action_acefone_click_to_call()

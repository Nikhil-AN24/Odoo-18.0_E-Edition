# -*- coding: utf-8 -*-
from odoo import fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    # Back-link to the offline order this sale.order was created from.
    # Defined here (not in contact_stage_bar) because custom_sale_order loads
    # after contact_stage_bar, so the custom.sale.order comodel is available.
    custom_sale_order_id = fields.Many2one(
        'custom.sale.order', string='Offline Order',
        copy=False, readonly=True, index=True)

# -*- coding: utf-8 -*-
from odoo import api, fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    # Back-link to the offline order this sale.order was created from.
    # Defined here (not in contact_stage_bar) because custom_sale_order loads
    # after contact_stage_bar, so the custom.sale.order comodel is available.
    custom_sale_order_id = fields.Many2one(
        'custom.sale.order', string='Offline Order',
        copy=False, readonly=True, index=True)


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    @api.depends('order_id.custom_sale_order_id.customer_rfq_id.stone_type',
                 'order_id.custom_sale_order_id.customer_rfq_id.stone_certification_type')
    def _compute_stone_classification(self):
        """Offline orders are raised from a Customer RFQ, which records the
        stone type and certification Sales asked for, so take both from there
        instead of the website's lab-grown default."""
        super()._compute_stone_classification()
        for line in self.filtered(lambda l: not l.display_type):
            rfq = line.order_id.custom_sale_order_id.customer_rfq_id
            if rfq:
                line.stone_type = rfq.stone_type
                line.stone_certification_type = rfq.stone_certification_type

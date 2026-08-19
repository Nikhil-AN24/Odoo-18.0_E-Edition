from odoo import models, fields, api, _
from markupsafe import Markup
import logging
from datetime import datetime, timedelta
from odoo.exceptions import UserError, ValidationError


class CustomSaleOrderAddProductWizard(models.TransientModel):
    _name = 'custom.sale.order.add.product.wizard'
    _description = "Add Product by Certificate/Stock Number"

    number = fields.Char(string="Number", required=True)

    def action_add_product(self):
        self.ensure_one()
        number = self.number.strip()
        order_id = self.env.context.get("default_order_id")
        order = self.env['custom.sale.order'].browse(order_id)

        empty_lines = order.order_line.filtered(
            lambda l: not (l.product_template_id or l.lgd_stock_number or l.certificate_number)
        )
        if empty_lines:
            empty_lines.unlink()

        # if not order:
        #     raise UserError("No active Sale Order found.")

        product = self.env['product.template'].search([
            '|', '|',
            ('certificate', '=', number),
            ('stock_number', '=', number),
            ('lgd_stock_number', '=', number),
        ], limit=1)

        if not product:
            product = self.env['product.template'].create({
                'certificate': number,
                'name': "[NEW]"
            })
            product.action_fetch_certificate_data()

        existing_line = order.order_line.filtered(
            lambda l: l.product_id == product.product_variant_id
        )

        if existing_line:
            existing_line.product_uom_qty += 1
        else:
            order.order_line.create({
                'order_id': order.id,
                'product_id': product.product_variant_id.id,
                'product_template_id': product.id,
                'name': product.name,
                'product_uom_qty': 1,
                'price_unit': product.list_price,
                'shapes': product.shapes,
                'color': product.color,
                'clarity': product.clarity,
                'carat_weight': product.weight_carat,
                # 'vendor_id': product.seller_ids[:1].partner_id.id if product.seller_ids else False,
            })

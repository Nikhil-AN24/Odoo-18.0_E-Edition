from odoo import models, fields,api,_
from markupsafe import Markup
import  logging
from datetime import datetime, timedelta
from odoo.exceptions import UserError, ValidationError


class SaleOrderAddProductWizard(models.TransientModel):
    _name = 'sale.order.add.product.wizard'
    _description = "Add Product by Certificate/Stock Number"

    number = fields.Char(string="Number", required=True)


    def action_add_product(self):
        self.ensure_one()
        number = self.number.strip()

        if number.isalnum():
            number = ''.join(char.upper() if char.isalpha() else char for char in number)

        order_id = self.env.context.get("default_order_id")
        order = self.env['sale.order'].browse(order_id)
        
        product = self.env['product.template'].search([
            '|', '|',
            ('certificate', '=', number),
            ('stock_number', '=', number),
            ('lgd_stock_number', '=', number),
        ], limit=1)

        if not product:
            product = self.env['product.template'].create({
                'certificate': number,
                'name': "[NEW]",
                'is_storable': True,
            })
            product.action_fetch_certificate_data()

        # Order of work matters here. The sale order line has to exist before
        # the RFQ, because the PO line carries sale_line_id back to it — that
        # link is what puts the stone on Logistics' Expected Stones list and
        # what every later stage. Raising the RFQ first, as this
        # did, left the PO line orphaned and the stone invisible to Logistics.
        #
        # The duplicate check also has to run before anything is created, so a
        # rejected add never raises an RFQ at all.
        duplicate_line = order.order_line.filtered(
            lambda l: l.certificate == product.certificate and
                    l.stock_number == product.stock_number and
                    l.lgd_stock_number == product.lgd_stock_number and
                    (product.certificate or product.stock_number or product.lgd_stock_number)
        )

        if duplicate_line:
            raise UserError(
                "This product already exists in the order!\n\n"
                f"Certificate Number: {product.certificate or 'N/A'}\n"
                f"Vendor SKU: {product.stock_number or 'N/A'}\n"
                f"LGD SKU: {product.lgd_stock_number or 'N/A'}\n\n"
                "Please add a different product."
            )

        vendor = product.seller_ids[:1].partner_id
        if not vendor:
            raise UserError("Vendor is missing for this product. Cannot create purchase order.")

        line_vals = {
            'order_id': order.id,
            'is_custom_product': True,
            'product_id': product.product_variant_id.id,
            'product_template_id': product.id,
            'name': product.name,
            'product_uom_qty': 1,
            'price_unit': product.final_price_margin if product.final_price_margin > 0 else product.final_price,
            'final_price': product.list_price,
            'vendor_id': vendor.id,
        }
        # If the order already has a QC-failed / unavailable line, this product
        # is a replacement added for the remaining flow — start it fresh at
        # diamond_booked so it re-enters the availability workflow. Normal
        # (draft) adds are left untouched.
        if any(l.availability_status in ('qc_fail', 'not_available')
               for l in order.order_line):
            line_vals['availability_status'] = 'diamond_booked'

        # Always a new line, never product_uom_qty += 1 on an existing one.
        # Each physical stone is tracked on its own through
        # inward, QC and acceptance, so two stones sharing one order line
        # cannot be received, passed or failed independently.
        sale_line = order.order_line.create(line_vals)

        purchase_order = self.env['purchase.order'].create({
            'partner_id': vendor.id,
            'origin': order.name,
            'location': order.location,
            'order_number': order.sdk_augmont_number,
        })

        self.env['purchase.order.line'].create({
            'order_id': purchase_order.id,
            'product_id': product.product_variant_id.id,
            'name': product.name,
            'product_qty': 1,
            'price_unit': product.final_price,
            'date_planned': fields.Datetime.now(),
            'sale_line_id': sale_line.id,
        })

        order.message_post(
            body=f"New RFQ <b>{purchase_order.name}</b> created automatically for the added product."
        )

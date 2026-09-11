from odoo import models, fields


class ProductTemplate(models.Model):
    """Define product.template fields that custom.sale.order.line uses
    via related expressions. These fields are also defined in
    contact_stage_bar; defining them here ensures custom_sale_order can
    set up its related fields without depending on contact_stage_bar
    (which would create a circular dependency since contact_stage_bar
    depends on custom_sale_order).
    """
    _inherit = "product.template"

    certificate = fields.Char(string="Certificate Number")
    lgd_stock_number = fields.Char(string="Website Stock Number")
    cut = fields.Char(string='Cut')
    polish = fields.Char(string='Polish')
    symmetry = fields.Char(string='Symmetry')
    fluorescence_color = fields.Char(string="Fluorescence Color")
    treatments = fields.Char(string='Treatments')

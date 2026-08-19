from odoo import models, fields


class ResPartnerStage(models.Model):
    _name = "res.partner.stage"

    name = fields.Char(string="Name")
    sequence = fields.Integer(string="Sequence")
    
    

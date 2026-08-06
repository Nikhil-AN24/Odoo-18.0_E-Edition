from odoo import models, fields
from random import randint


class ResPartnerCategory(models.Model):
    _name = "custom.category"

    def _get_default_color(self):
        return randint(1, 11)

    name = fields.Char(string="Name")
    color = fields.Integer(string='Color', default=_get_default_color, aggregator=False)



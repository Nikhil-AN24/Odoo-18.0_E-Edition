from odoo import models, fields,api


class ResUsers(models.Model):
    _inherit = "res.users"

    location = fields.Selection([('mumbai', 'India'), ('surat', 'USA')], string='Location')    
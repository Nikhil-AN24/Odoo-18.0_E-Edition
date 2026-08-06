from odoo import fields, models

class ResUsers(models.Model):
    _inherit = 'res.users'

    acefone_agent_number = fields.Char(
        string='Acefone Agent Number',
        help="Your own phone/extension number as registered in Acefone. "
             "This is the number that will ring FIRST when you click 'Call' "
             "on a contact, lead, or sales order. Once you answer, Acefone "
             "dials the customer and connects you both.",
    )

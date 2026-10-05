# -*- coding: utf-8 -*-
from odoo import fields, models


class LgdCourierAgent(models.Model):
    _name = 'lgd.courier.agent'
    _description = 'Courier or Angadia'
    _order = 'agent_type, name'

    name = fields.Char(required=True)                       # "Sequel Logistics"
    agent_type = fields.Selection(
        [('courier', 'Courier company'), ('angadia', 'Angadia')],
        required=True, default='courier')
    firm_name = fields.Char(help="The firm an Angadia works for.")
    phone = fields.Char(required=True, help="The number Dispatch rings.")
    city = fields.Char()
    note = fields.Text()
    active = fields.Boolean(default=True)

# -*- coding: utf-8 -*-
from odoo import api, fields, models

class LgdCourierAgent(models.Model):
    _name = 'lgd.courier.agent'
    _description = 'Courier or Angadia'
    _order = 'agent_type, name'

    name = fields.Char(string="Contact Name", required=True)  # "Sequel Logistics"
    agent_type = fields.Selection(
        [('courier', 'Courier company'), ('angadia', 'Angadia')],
        required=True, default='courier')
    firm_name = fields.Char(string="Firm Name", help="The firm this contact works for.")
    phone = fields.Char(help="The number Dispatch rings.")
    city = fields.Char()
    note = fields.Text()
    active = fields.Boolean(default=True)

    # Typing either the firm or the contact name finds the record.
    _rec_names_search = ['firm_name', 'name']

    @api.depends('firm_name', 'name')
    def _compute_display_name(self):
        """Show the firm wherever a carrier is referenced — "Handed to" on the
        parcel is asking which courier company took it, not which individual.
        Falls back to the contact name for records with no firm recorded."""
        for agent in self:
            agent.display_name = agent.firm_name or agent.name or ''
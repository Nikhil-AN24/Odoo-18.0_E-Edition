from odoo import models, fields


class Lead(models.Model):
    _inherit = "crm.lead"

    lead_source = fields.Selection([('website', 'Website'), ('cold_call', 'Cold Call'), ('email', 'Email'), ('marketing_campaign', 'Marketing Campaign')], string='Lead Source')
    lead_tagging = fields.Selection([('high_intent', 'High Intent'), ('engaged', 'Engaged'), ('dormant', 'Dormant'), ('high_value', 'High Value')], string='Lead Tagging')

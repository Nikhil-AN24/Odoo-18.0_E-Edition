from odoo import models
from odoo.exceptions import UserError

class CrmLead(models.Model):
    _inherit = 'crm.lead'

    def _acefone_call(self, number):
        self.ensure_one()
        if not number:
            raise UserError("This lead/opportunity does not have that number set.")

        agent_number = self.env.user.acefone_agent_number
        if not agent_number:
            raise UserError(
                "Please set your Acefone Agent Number first.\n"
                "Click your avatar (top right) > My Profile / Preferences, and "
                "fill in 'Acefone Agent Number'."
            )

        caller_id = self.env['ir.config_parameter'].sudo().get_param('acefone.default_caller_id')
        self.env['acefone.service'].click_to_call(number, agent_number, caller_id)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Acefone Click to Call',
                'message': f"Call initiated to {number}. Your number ({agent_number}) "
                           f"will ring shortly — answer it to connect to the customer.",
                'type': 'success',
                'sticky': False,
            },
        }

    def action_acefone_click_to_call(self):
        """Call the Phone field (falls back to Mobile if Phone is empty)."""
        self.ensure_one()
        return self._acefone_call(self.phone or self.mobile)

    def action_acefone_click_to_call_mobile(self):
        """Call the Mobile field specifically."""
        self.ensure_one()
        return self._acefone_call(self.mobile)
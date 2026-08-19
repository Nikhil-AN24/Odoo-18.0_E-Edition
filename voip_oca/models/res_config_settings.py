from odoo import models, fields, api

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    operator_company_id = fields.Char(string="Company Id")
    operator_secret_token = fields.Char(string="Secret Token")
    operator_x_api_key = fields.Char(string="X-Api-Key")
    operator_caller_id = fields.Char(string="Caller Id")
    operator_public_ivr_id = fields.Char(string="Public IVR Id")
    operator_region = fields.Char(string="Region")
    operator_group = fields.Char(string="Group")
    operator_url = fields.Char(string="URL")

    @api.model
    def get_values(self):
        res = super(ResConfigSettings, self).get_values()
        params = self.env['ir.config_parameter'].sudo()

        res.update(
            operator_company_id=params.get_param('operator_company_id', default=''),
            operator_secret_token=params.get_param('operator_secret_token', default=''),
            operator_x_api_key=params.get_param('operator_x_api_key', default=''),
            operator_caller_id=params.get_param('operator_caller_id', default=''),
            operator_public_ivr_id=params.get_param('operator_public_ivr_id', default=''),
            operator_region=params.get_param('operator_region', default=''),
            operator_group=params.get_param('operator_group', default=''),
            operator_url=params.get_param('operator_url', default=''),
        )
        return res

    def set_values(self):
        super(ResConfigSettings, self).set_values()
        params = self.env['ir.config_parameter'].sudo()

        params.set_param('operator_company_id', self.operator_company_id or '')
        params.set_param('operator_secret_token', self.operator_secret_token or '')
        params.set_param('operator_x_api_key', self.operator_x_api_key or '')
        params.set_param('operator_caller_id', self.operator_caller_id or '')
        params.set_param('operator_public_ivr_id', self.operator_public_ivr_id or '')
        params.set_param('operator_region', self.operator_region or '')
        params.set_param('operator_group', self.operator_group or '')
        params.set_param('operator_url', self.operator_url or '')
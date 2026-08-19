from odoo import fields, models

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    acefone_base_url = fields.Char(
        string='Acefone API Base URL',
        config_parameter='acefone.base_url',
        default='https://api.acefone.co.uk',
        help='Confirmed working endpoint for UK/international accounts: '
             'https://api.acefone.co.uk. For India-platform accounts, try '
             'https://api.acefone.in. Verify with Acefone support if unsure.',
    )
    acefone_email = fields.Char(
        string='Acefone Login ID',
        config_parameter='acefone.email',
        help='Used only if no API Key is provided below (JWT login method). '
             'This is your Acefone portal login ID/email.',
    )
    acefone_password = fields.Char(
        string='Acefone Password',
        config_parameter='acefone.password',
        help='Used only if no API Key is provided below (JWT login method).',
    )
    acefone_api_key = fields.Char(
        string='Acefone API Key',
        config_parameter='acefone.api_key',
        help='Recommended. Generate this under Services > Click To Call API '
             'in your Acefone portal. If set, this is used instead of '
             'Email/Password and never expires.',
    )
    acefone_default_caller_id = fields.Char(
        string='Default Caller ID (DID)',
        config_parameter='acefone.default_caller_id',
        help='Optional. The Acefone DID/virtual number to display to the '
             'customer as the caller ID. Leave empty to use your account default.',
    )

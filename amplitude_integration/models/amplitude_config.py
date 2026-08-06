# -*- coding: utf-8 -*-
from odoo import api, fields, models


class AmplitudeConfig(models.Model):
    """Connection + feature settings for the Amplitude integration.

    Treated as a singleton: the active record returned by `get_config()`
    drives both the backend HTTP client and the frontend SDK.
    """
    _name = 'amplitude.config'
    _description = 'Amplitude Configuration'

    name = fields.Char(string='Name', required=True, default='Amplitude')
    active = fields.Boolean(string='Active', default=True)

    # --- Credentials ---
    api_key = fields.Char(
        string='API Key',
        help="Amplitude project API key. Safe to expose to the browser "
             "(used by the frontend SDK and the backend HTTP V2 API).")
    secret_key = fields.Char(
        string='Secret Key',
        help="Amplitude secret key. Kept server-side only "
             "(reserved for the Dashboard/Export REST API).")
    project_id = fields.Char(
        string='Project ID',
        help="Amplitude project / app id (used in dashboard URLs).")
    server_url = fields.Char(
        string='Server URL',
        default='https://api2.amplitude.com/2/httpapi',
        help="Backend HTTP V2 API endpoint. Use "
             "https://api.eu.amplitude.com/2/httpapi for the EU data centre.")
    server_zone = fields.Selection(
        selection=[('US', 'US'), ('EU', 'EU')],
        string='Server Zone', default='US',
        help="Data residency zone used by the frontend browser SDK.")

    # --- Feature toggles ---
    enable_analytics = fields.Boolean(
        string='Enable Analytics', default=True,
        help="Master switch. When off, neither frontend nor backend "
             "events are sent.")
    enable_heatmap = fields.Boolean(
        string='Enable Heatmap', default=True,
        help="Enable Autocapture element interactions (powers heatmaps).")
    enable_session_replay = fields.Boolean(
        string='Enable Session Replay', default=True)
    enable_event_tracking = fields.Boolean(
        string='Enable Event Tracking', default=True,
        help="Enable backend business workflow events.")
    enable_debug = fields.Boolean(
        string='Enable Debug Mode', default=False,
        help="Verbose logging in the browser console and Odoo server log.")

    session_replay_sample_rate = fields.Float(
        string='Session Replay Sample Rate', default=1.0,
        help="Fraction of sessions to record (0.0 - 1.0). 1.0 = all.")

    @api.model
    def get_config(self):
        """Return the active configuration singleton (or empty recordset)."""
        return self.sudo().search([('active', '=', True)], limit=1)

    def frontend_values(self):
        """Subset of settings exposed to the browser SDK.

        The secret key is intentionally never sent to the frontend.
        """
        self.ensure_one()
        return {
            'enabled': self.enable_analytics,
            'api_key': self.api_key or '',
            'server_zone': self.server_zone or 'US',
            'enable_heatmap': self.enable_heatmap,
            'enable_session_replay': self.enable_session_replay,
            'enable_event_tracking': self.enable_event_tracking,
            'session_replay_sample_rate': self.session_replay_sample_rate or 1.0,
            'debug': self.enable_debug,
        }

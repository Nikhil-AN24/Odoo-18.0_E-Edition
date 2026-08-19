# -*- coding: utf-8 -*-
import logging

import odoo
from odoo import SUPERUSER_ID, api, models
from odoo.exceptions import AccessDenied

_logger = logging.getLogger(__name__)


class ResUsers(models.Model):
    _inherit = 'res.users'

    @classmethod
    def _login(cls, db, credential, user_agent_env):
        """Track successful logins and failed login attempts.

        Failed attempts can only be captured server-side (there is no
        browser session yet), so this hook complements the frontend SDK.
        Everything here is fully defensive: analytics must never block auth.
        """
        login = (credential or {}).get('login')
        try:
            uid = super()._login(db, credential, user_agent_env)
        except AccessDenied:
            cls._amplitude_safe_track(db, None, 'Failed Login Attempt',
                                      {'login': login})
            raise

        cls._amplitude_safe_track(db, uid, 'User Login', {'login': login})
        return uid

    @classmethod
    def _amplitude_safe_track(cls, db, uid, event_type, properties):
        """Open a short-lived cursor to emit an auth event, swallowing errors."""
        try:
            registry = odoo.registry(db)
            with registry.cursor() as cr:
                env = api.Environment(cr, uid or SUPERUSER_ID, {})
                client = env['amplitude.client']
                if uid:
                    client.track_event(event_type, properties)
                else:
                    # No real user: identify by attempted login string.
                    config = client._get_config()
                    if config and config.enable_event_tracking and config.api_key:
                        event = {
                            'user_id': properties.get('login') or 'unknown',
                            'event_type': event_type,
                            'event_properties': properties,
                            'platform': 'Odoo',
                        }
                        client._send_events([event])
        except Exception as exc:  # pragma: no cover - never break login
            _logger.warning("Amplitude: auth tracking failed: %s", exc)

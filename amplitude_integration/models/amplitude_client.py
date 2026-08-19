# -*- coding: utf-8 -*-
import json
import logging
import time

import requests

from odoo import api, models

_logger = logging.getLogger(__name__)

DEFAULT_URL = 'https://api2.amplitude.com/2/httpapi'
REQUEST_TIMEOUT = 10


class AmplitudeClient(models.AbstractModel):
    """Reusable server-side Amplitude client.

    Public API (as per requirements):
        - track_event()
        - track_business_event()
        - identify_user()
        - start_session()
        - end_session()

    Events are queued to be sent *after* the current transaction commits, so
    a network failure can never roll back or break an Odoo business action.
    """
    _name = 'amplitude.client'
    _description = 'Amplitude Client'

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    @api.model
    def _get_config(self):
        return self.env['amplitude.config'].get_config()

    @api.model
    def _user_id_for(self, user):
        """Stable Amplitude user id (>= 5 chars recommended)."""
        return user.login or ('odoo-%s-%s' % (self.env.cr.dbname, user.id))

    @api.model
    def _user_properties(self, user):
        employee = self.env['hr.employee'].sudo().search(
            [('user_id', '=', user.id)], limit=1)
        return {
            'user_name': user.name,
            'login': user.login,
            'email': user.email or '',
            'employee_name': employee.name if employee else '',
            'company': user.company_id.name if user.company_id else '',
            'db_name': self.env.cr.dbname,
        }

    @api.model
    def _build_event(self, event_type, properties=None, user=None, session_id=None):
        user = user or self.env.user
        event = {
            'user_id': self._user_id_for(user),
            'event_type': event_type,
            'time': int(time.time() * 1000),
            'platform': 'Odoo',
            'event_properties': properties or {},
            'user_properties': self._user_properties(user),
        }
        if session_id is not None:
            event['session_id'] = session_id
        return event

    @api.model
    def _send_events(self, events):
        """Register a post-commit callback that POSTs events to Amplitude."""
        if not events:
            return
        config = self._get_config()
        if not config or not config.enable_analytics or not config.api_key:
            return

        api_key = config.api_key
        url = config.server_url or DEFAULT_URL
        debug = config.enable_debug

        payload = {'api_key': api_key, 'events': events}

        def _do_post():
            try:
                resp = requests.post(
                    url, json=payload, timeout=REQUEST_TIMEOUT,
                    headers={'Content-Type': 'application/json'})
                if debug:
                    _logger.info(
                        "Amplitude: sent %s event(s) -> %s %s",
                        len(events), resp.status_code, resp.text)
                else:
                    resp.raise_for_status()
            except Exception as exc:  # never break the caller
                _logger.warning("Amplitude: failed to send events: %s", exc)

        # Fire after the transaction commits so analytics never affects data.
        self.env.cr.postcommit.add(_do_post)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    @api.model
    def track_event(self, event_type, properties=None, user=None, session_id=None):
        """Send a single arbitrary event."""
        config = self._get_config()
        if not config or not config.enable_event_tracking:
            return
        self._send_events([
            self._build_event(event_type, properties, user, session_id)])

    @api.model
    def track_business_event(self, record, event_type, extra=None):
        """Send a workflow event tied to a single Odoo record.

        Automatically attaches model / record / user identification context.
        """
        config = self._get_config()
        if not config or not config.enable_event_tracking:
            return
        properties = {
            'model': record._name,
            'record_id': record.id,
            'record_name': record.display_name,
        }
        if extra:
            properties.update(extra)
        self._send_events([self._build_event(event_type, properties)])

    @api.model
    def identify_user(self, user=None):
        """Push the current user's profile properties to Amplitude."""
        config = self._get_config()
        if not config or not config.enable_analytics or not config.api_key:
            return
        user = user or self.env.user

        identification = [{
            'user_id': self._user_id_for(user),
            'user_properties': self._user_properties(user),
        }]
        api_key = config.api_key
        base = (config.server_url or DEFAULT_URL).split('/2/httpapi')[0]
        url = '%s/identify' % base
        debug = config.enable_debug
        data = {'api_key': api_key, 'identification': json.dumps(identification)}

        def _do_post():
            try:
                resp = requests.post(url, data=data, timeout=REQUEST_TIMEOUT)
                if debug:
                    _logger.info("Amplitude identify -> %s %s",
                                 resp.status_code, resp.text)
                else:
                    resp.raise_for_status()
            except Exception as exc:
                _logger.warning("Amplitude: identify failed: %s", exc)

        self.env.cr.postcommit.add(_do_post)

    @api.model
    def start_session(self, user=None, session_id=None):
        """Emit a 'Session Start' event."""
        self.track_event('Session Start', user=user, session_id=session_id)

    @api.model
    def end_session(self, user=None, session_id=None):
        """Emit a 'Session End' event."""
        self.track_event('Session End', user=user, session_id=session_id)

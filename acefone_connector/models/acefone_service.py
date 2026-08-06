import logging
from datetime import datetime, timedelta
import requests
from odoo import models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

class AcefoneService(models.AbstractModel):
    """Helper model (no database table) that talks to the Acefone REST API.

    Call it from anywhere like this:
        self.env['acefone.service'].click_to_call(destination_number, agent_number)
    """
    _name = 'acefone.service'
    _description = 'Acefone API Helper Service'

    # Configuration helpers
    def _get_config(self):
        icp = self.env['ir.config_parameter'].sudo()
        base_url = icp.get_param('acefone.base_url', 'https://api.acefone.co.uk')
        email = icp.get_param('acefone.email')
        password = icp.get_param('acefone.password')
        return base_url.rstrip('/'), email, password

    def _get_api_key(self):
        return self.env['ir.config_parameter'].sudo().get_param('acefone.api_key')

    # Token management
    def _get_access_token(self):
        """Return a cached, still-valid token, or log in again if expired/missing."""
        icp = self.env['ir.config_parameter'].sudo()
        token = icp.get_param('acefone.access_token')
        expires_at_str = icp.get_param('acefone.token_expires_at')

        if token and expires_at_str:
            try:
                expires_at = datetime.strptime(expires_at_str, '%Y-%m-%d %H:%M:%S')
                # refresh 2 minutes before actual expiry, to be safe
                if datetime.now() < expires_at - timedelta(minutes=2):
                    return token
            except ValueError:
                pass

        return self._login()

    def _login(self):
        base_url, email, password = self._get_config()
        if not email or not password:
            raise UserError(
                "Acefone Email/Password are not configured.\n"
                "Go to Settings > General Settings > Acefone section and enter "
                "your Email and Password, OR enter an API Key instead."
            )
        url = f"{base_url}/v1/auth/login"

        try:
            response = requests.post(
                url,
                json={'login_id': email, 'password': password},
                headers={'accept': 'application/json', 'content-type': 'application/json'},
                timeout=15,
            )
        except requests.exceptions.RequestException as exc:
            _logger.exception("Acefone login request failed")
            raise UserError(f"Could not reach the Acefone server: {exc}") from exc

        if response.status_code != 200:
            raise UserError(
                f"Acefone login failed (HTTP {response.status_code}).\n"
                f"Response: {response.text}"
            )

        data = response.json()
        token = data.get('access_token')
        expires_in = data.get('expires_in', 3600)

        if not token:
            raise UserError(f"Acefone login did not return an access token. Response: {data}")

        icp = self.env['ir.config_parameter'].sudo()
        icp.set_param('acefone.access_token', token)
        expires_at = datetime.now() + timedelta(seconds=int(expires_in))
        icp.set_param('acefone.token_expires_at', expires_at.strftime('%Y-%m-%d %H:%M:%S'))
        return token


    # Public API methods
    def click_to_call(self, destination_number, agent_number, caller_id=False):
        """Trigger an Acefone Click-to-Call: rings agent_number first, then bridges
        to destination_number once the agent answers.
        Uses the static API Key (Services > Click To Call API 
        """
        base_url, _email, _password = self._get_config()
        url = f"{base_url}/v1/click_to_call"

        payload = {
            'destination_number': destination_number,
            'agent_number': agent_number,
            'async': '1',
        }
        if caller_id:
            payload['caller_id'] = caller_id

        api_key = self._get_api_key()
        if api_key:
            auth_value = api_key
        else:
            auth_value = self._get_access_token()

        try:
            response = requests.post(
                url,
                json=payload,
                headers={
                    'accept': 'application/json',
                    'content-type': 'application/json',
                    'Authorization': auth_value,
                },
                timeout=15,
            )
        except requests.exceptions.RequestException as exc:
            _logger.exception("Acefone click_to_call request failed")
            raise UserError(f"Could not reach the Acefone server: {exc}") from exc

        if response.status_code not in (200, 201):
            raise UserError(
                f"Acefone Click-to-Call failed (HTTP {response.status_code}).\n"
                f"Response: {response.text}"
            )

        return response.json() if response.content else {}

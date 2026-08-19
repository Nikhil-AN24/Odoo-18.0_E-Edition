from odoo import http,fields
from odoo.http import request
import requests
import json
import traceback
import logging
from markupsafe import Markup
from odoo.exceptions import UserError
import datetime


_logger = logging.getLogger(__name__)
import uuid

from odoo import http
from odoo.http import request
import logging

_logger = logging.getLogger(__name__)

class PulseController(http.Controller):
    
    @http.route('/pulse/call', type='json', auth='user')
    def make_call(self, customer_number=False, agent_number=False,agent_password=False):
        API_KEY = "3bde4d4f7d5e9c3fe3bc5eac420a8996"
        pulse_agent_number = request.env.user.pulse_agent_number
        # pulse_agent_password = request.env.user.pulse_agent_password

        if not agent_number:
            agent_number = request.env.user.pulse_agent_number

        remote_id = str(uuid.uuid4())
        url = "https://a30gecs4mk.execute-api.ap-south-1.amazonaws.com/v1/c2c"
        params = {
            "agent_number": agent_number,
            "customer_number": customer_number,
            "apikey": API_KEY,
            "remote_id": remote_id,
        }

        try:
            response = requests.get(url, params=params, timeout=25)

            if response.status_code == 200:
                # Build iframe URL (from PDF doc)
                userID = agent_number
                # secret = "Anirath@1001"
                authID = "3bde4d4f7d5e9c3fe3bc5eac420a8996"
                customer_number = customer_number

                # iframe_url = (
                #     f"https://calldesk.pulsework360.com/Dialer/{authID}/clicktocallpage.php"
                #     f"?userID={userID}&secret={secret}&authID={authID}&customer_number={customer_number}"
                # )

                return {
                    'status': 'success',
                    'details': response.json(),
                    # 'iframe_url': iframe_url,
                }
            else:
                return {
                    'status': 'error',
                    'code': response.status_code,
                    'details': response.text
                }
        except Exception as e:
            return {
                'status': 'error',
                'details': str(e)
            }


    @http.route('/odoo/pulse/webhook', type='json', auth='public', csrf=False, methods=['POST'])
    def pulse_webhook(self, **kwargs):
        """
        Webhook endpoint for Pulse call events (start & end).
        Pulse will POST JSON with call details, including recording_url.
        """
        try:
            # Pulse sends JSON → already parsed into kwargs
            data = kwargs
            _logger.info(f"Pulse Webhook received: {data}")

            # Extract fields
            call_type = data.get('call_type', '')
            if call_type in ['incoming', 'inbound']:
                phone_number = data.get('destination', '')      # Customer
                agent_number = data.get('source', '') # Agent
            else:  # outbound/outgoing
                phone_number = data.get('destination', '') # Customer
                agent_number = data.get('source', '')      # Agent

            call_duration = data.get('call_duration', 0)
            recording_url = data.get('recording_url', '')
            call_status   = data.get('call_status', 'unknown')
            unique_id     = data.get('Unique_id', '')

            def parse_dt(raw):
                if not raw:
                    return ""
                try:
                    # Case 1: "2025-09-19 12:00:00"
                    dt = datetime.datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
                except Exception:
                    try:
                        # Case 2: ISO 8601 e.g. "2025-09-19T12:00:00Z"
                        dt = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
                    except Exception:
                        _logger.warning(f"Unrecognized datetime format: {raw}")
                        return raw  # fallback: return raw string
                # Convert to Odoo UTC string
                return fields.Datetime.to_string(dt)

            start_time = parse_dt(data.get('start_time'))
            end_time   = parse_dt(data.get('end_time'))

            # if not phone_number:
            #     _logger.error("Missing phone number in webhook data")
            #     return {"status": "error", "message": "Missing phone number"}

            # Clean phone for matching
            clean_phone = phone_number.replace('+', '').replace('-', '').replace(' ', '')
            partner = request.env['res.partner'].sudo().search([
                '|', '|', '|',
                ('phone', 'ilike', phone_number),
                ('mobile', 'ilike', phone_number),
                ('phone', 'ilike', clean_phone),
                ('mobile', 'ilike', clean_phone)
            ], limit=1)

            _logger.info(f"Webhook phone={phone_number}, partner={partner.id if partner else 'None'}")

            # Format duration
            duration_text = f"{call_duration} seconds"
            if call_duration and int(call_duration) > 60:
                minutes = int(call_duration) // 60
                seconds = int(call_duration) % 60
                duration_text = f"{minutes}m {seconds}s"

            # Create chatter message
            message = Markup(f"""
                <b><i class="fa fa-phone"></i> Call Log from Pulse</b><br/>
                <ul>
                    <li><b>Call ID:</b> {unique_id}</li>
                    <li><b>Type:</b> {call_type.title()}</li>
                    <li><b>Status:</b> {call_status.title()}</li>
                    <li><b>Phone Number:</b> {phone_number}</li>
                    <li><b>Agent:</b> {agent_number}</li>
                    <li><b>Start Time:</b> {start_time}</li>
                    {f"<li><b>End Time:</b> {end_time}</li>" if end_time else ""}
                    <li><b>Duration:</b> {duration_text}</li>
                    <li><b>Recording:</b><br/>
                        {"No recording available" if not recording_url else f"""
                        <audio controls style="width: 300px; margin-top: 5px;">
                            <source src="{recording_url}" type="audio/wav">
                            <source src="{recording_url}" type="audio/mpeg">
                            Your browser does not support the audio element.
                        </audio>
                        <br/>
                        <a href="{recording_url}" target="_blank" rel="noopener noreferrer">Open Recording</a>
                        """}
                    </li>
                </ul>
            """)

            if partner:
                partner.message_post(
                    body=message,
                    message_type='comment',
                    subtype_xmlid='mail.mt_note'
                )
                _logger.info(f"Message posted to partner {partner.id} ({partner.name})")
                return {"status": "success", "message": f"Call log added to partner {partner.name}"}
            else:
                _logger.warning(f"No partner found for phone: {phone_number}")
                return {"status": "warning", "message": f"No partner found for phone {phone_number}"}

        except Exception as e:
            _logger.error(f"Error processing Pulse webhook: {str(e)}")
            return {"status": "error", "message": f"Error: {str(e)}"}
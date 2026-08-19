from odoo import http,fields
from odoo.http import request
import requests
import json
import traceback
import logging
from markupsafe import Markup

_logger = logging.getLogger(__name__)
import uuid

class MyOperatorController(http.Controller):

    # Route to initiate a call via MyOperator
    @http.route('/myoperator/call', type='json', auth='user')
    def make_call(self, customer_number, partner=False):
        operator_user_id = request.env.user.operator_user_id
        COMPANY_ID = request.env['ir.config_parameter'].sudo().get_param('operator_company_id')
        SECRET_TOKEN = request.env['ir.config_parameter'].sudo().get_param('operator_secret_token')
        X_API_KEY = request.env['ir.config_parameter'].sudo().get_param('operator_x_api_key')
        CALLER_ID = request.env['ir.config_parameter'].sudo().get_param('operator_caller_id')
        PUBLIC_IVR_ID = request.env['ir.config_parameter'].sudo().get_param('operator_public_ivr_id')

        USER_ID = operator_user_id
        REGION = ''
        GROUP = ''
        REFERENCE_ID = str(uuid.uuid4())

        payload = {
            "company_id": COMPANY_ID,
            "secret_token": SECRET_TOKEN,
            "type": "1",
            "user_id": USER_ID,
            "number": customer_number,
            "public_ivr_id": PUBLIC_IVR_ID,
            "reference_id": REFERENCE_ID,
            "region": REGION,
            "caller_id": CALLER_ID,
            "group": GROUP
        }
        _logger.info(f"payload: {payload}")
        headers = {
            'x-api-key': X_API_KEY,
            'Content-Type': 'application/json'
        }

        url = "https://obd-api.myoperator.co/obd-api-v1"
        response = requests.post(url, headers=headers, data=json.dumps(payload))

        if response.status_code == 200:
            return {
                'status': 'success',
                'details': response.json()
            }
        else:
            return {
                'status': 'error',
                'code': response.status_code,
                'details': response.text
            }

    @http.route('/myoperator/download_recording', type='http', auth='public')
    def download_recording(self, **kwargs):
        recording_url = kwargs.get('url')  # URL passed as query param

        if not recording_url:
            return request.not_found()

        # Fetch audio from the URL (optional: skip this if you want direct redirect)
        response = requests.get(recording_url)
        if response.status_code != 200:
            return request.not_found()

        filename = "recording.mp3"  # or extract from URL
        headers = [
            ('Content-Type', 'audio/mpeg'),
            ('Content-Disposition', f'attachment; filename="{filename}"')
        ]
        return request.make_response(response.content, headers)
    
    @http.route('/odoo/myoperator/webhook', type='http', auth='public', csrf=False, methods=['POST'])
    def myoperator_webhook(self, **kwargs):
        phone_number = kwargs.get('phone_number')
        call_duration = kwargs.get('call_duration')
        recording_url = kwargs.get('recording_url')

        if not phone_number:
            return request.make_response("Missing phone number", status=400)

        # Search for partner by phone number
        partner = request.env['res.partner'].sudo().search([
            ('phone', 'ilike', phone_number)
        ], limit=1)
        
        # cleaned_phone = phone_number.replace('+91', '').replace(' ', '').replace('-', '')
        # partner = request.env['res.partner'].sudo().search([
        #     ('phone', 'ilike', cleaned_phone)
        # ], limit=1)

        
        _logger.info(f"Webhook received for phone: {phone_number}. Found partner ID: {partner.id if partner else 'None'}")

        # if not partner:
        #     _logger.warning(f"No partner found for phone number: {phone_number}")
        #     return request.make_response("Partner not found", status=404)

        try:
            message = Markup(f"""
                <b><i class="fa fa-phone"></i> Call Log from MyOperator</b><br/>
                <ul>
                    <li><b>Phone Number:</b> {phone_number}</li>
                    <li><b>Call Duration:</b> {call_duration}</li>
                    <li><b>Recording:</b><br/>
                        <audio controls style="width: 300px; margin-top: 5px;">
                            <source src="{recording_url}" type="audio/mpeg">
                            Your browser does not support the audio element.
                        </audio>
                        <br/>
                        # <a href="{recording_url}" download> Download Recording</a>

                        <a href="/odoo/myoperator/webhook?url={recording_url}" class="btn btn-primary">
                             Download Recording
                        </a>

                    </li>
                </ul>
            """)
            # Ensure we're working with a single record
            if len(partner) > 1:
                partner = partner[0]
                _logger.warning(f"Multiple partners found for phone {phone_number}. Using first one (ID: {partner.id})")

            message_check = partner.message_post(
                body=message,
                message_type='comment',
                subtype_xmlid='mail.mt_note'
            )
            
            _logger.info(f"Message posted successfully. Partner ID: {partner.id}, Message ID: {message_check}")
            return request.make_response("Webhook received and logged in partner chatter", status=200)
            
        except Exception as e:
            _logger.error(f"Error processing webhook for partner {partner.id}: {str(e)}")
            return request.make_response(f"Error processing webhook: {str(e)}", status=500)
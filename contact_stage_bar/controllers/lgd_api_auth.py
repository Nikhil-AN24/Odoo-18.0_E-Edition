# -*- coding: utf-8 -*-
import hmac
import json
import logging

from odoo.http import Response

_logger = logging.getLogger(__name__)

# System parameter holding the shared secret. Set it in Settings > Technical > System Parameters.
LGD_API_TOKEN_PARAM = 'website.api.token'


def _refuse(message, code):
    return Response(
        json.dumps({'status': 'error', 'message': message, 'code': code}),
        content_type='application/json', status=code)


def lgd_check_api_token(request):
    """Return None when the caller is authorised, else a Response to send back.

    Usage at the top of a controller method::

        denied = lgd_check_api_token(request)
        if denied:
            return denied
    """
    auth_header = request.httprequest.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        _logger.warning("[AUTH FAIL] Missing or invalid Authorization header")
        return _refuse('Missing Authorization header', 401)

    token = auth_header.split(' ', 1)[1].strip()
    expected = (request.env['ir.config_parameter'].sudo()
                .get_param(LGD_API_TOKEN_PARAM, default='') or '')

    if not expected:

        _logger.error(
            "[CONFIG] %s is not set; refusing API calls until it is.",
            LGD_API_TOKEN_PARAM)
        return _refuse('Server configuration error', 500)

    if not hmac.compare_digest(token, expected):
        _logger.warning("[AUTH FAIL] Invalid token")
        return _refuse('Invalid API token', 401)

    return None

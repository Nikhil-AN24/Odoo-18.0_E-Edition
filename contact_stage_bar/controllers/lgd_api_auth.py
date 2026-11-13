# -*- coding: utf-8 -*-
import hmac
import json
import logging

from odoo.http import Response

_logger = logging.getLogger(__name__)

# System parameter holding the shared secret. Set it in Settings > Technical > System Parameters.
LGD_API_TOKEN_PARAM = 'website.api.token'
LGD_OUTBOUND_TOKEN_PARAM = 'lgd_offline_order_token'


def _refuse(message, code):
    return Response(
        json.dumps({'status': 'error', 'message': message, 'code': code}),
        content_type='application/json', status=code)


def _who(request):
    http = request.httprequest
    return "%s %s from %s" % (
        http.method, http.path,
        http.headers.get('CF-Connecting-IP')
        or http.headers.get('X-Forwarded-For') or http.remote_addr)


def lgd_check_api_token(request):
    """Return None when the caller is authorised, else a Response to send back.

    Every refusal is logged with its exact reason - the endpoint, the
    caller, and the length of the token received against the one expected
    (never the token itself) - so a 401 can be diagnosed from the log alone.

    Usage at the top of a controller method::

        denied = lgd_check_api_token(request)
        if denied:
            return denied
    """
    auth_header = (request.httprequest.headers.get('Authorization') or '').strip()
    scheme, _sep, token = auth_header.partition(' ')
    token = token.strip()
    if scheme.lower() != 'bearer' or not token:
        _logger.warning(
            "[AUTH FAIL] %s: %s. Expected the header "
            "'Authorization: Bearer <%s>'.", _who(request),
            "no Authorization header" if not auth_header
            else "Authorization header is not 'Bearer <token>' (got scheme %r)"
            % scheme, LGD_API_TOKEN_PARAM)
        return _refuse('Missing Authorization header', 401)

    Param = request.env['ir.config_parameter'].sudo()
    expected = (Param.get_param(LGD_API_TOKEN_PARAM, default='') or '').strip()

    if not expected:
        _logger.error(
            "[CONFIG] %s: %s is not set; refusing API calls until it is.",
            _who(request), LGD_API_TOKEN_PARAM)
        return _refuse('Server configuration error', 500)

    if not hmac.compare_digest(token, expected):
        outbound = (Param.get_param(LGD_OUTBOUND_TOKEN_PARAM, default='')
                    or '').strip()
        if outbound and hmac.compare_digest(token, outbound):
            hint = ("the caller sent %s - the token Odoo sends TO the "
                    "website - instead of %s" % (
                        LGD_OUTBOUND_TOKEN_PARAM, LGD_API_TOKEN_PARAM))
        elif expected.startswith(token) or token.startswith(expected):
            hint = "the token is cut short or has extra characters"
        else:
            hint = "the token does not match %s" % LGD_API_TOKEN_PARAM
        _logger.warning(
            "[AUTH FAIL] %s: invalid token (%s). Received %s characters, "
            "expected %s.", _who(request), hint, len(token), len(expected))
        return _refuse('Invalid API token', 401)

    _logger.info("[AUTH OK] %s", _who(request))
    return None

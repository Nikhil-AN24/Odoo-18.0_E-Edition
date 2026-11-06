# -*- coding: utf-8 -*-
import logging

_logger = logging.getLogger(__name__)

LGD_API_TOKEN_PARAM = 'lgd_offline_order_token'

# The bearer token that used to be written out at five call sites. Kept ONLY
# as a fallback for a database where lgd_offline_order_token has never been
# set, so nothing that worked yesterday stops working today. A token in
# source is a credential in git and cannot be rotated without a deploy:
# set the system parameter and this constant stops being read.
LGD_API_LEGACY_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJtZXJjaGFudElkIjoiZjA3NDA3YzItZTI1ZS00YjI2LTk1MWUtODliZGQxZjI4YmQ2Iiwic2hvcnROYW1lIjoib2RvbyIsImlhdCI6MTc2MDYxNjU3OCwiZXhwIjoyMDc2MTkyNTc4fQ.XpKKN78VVCezg6BrEhQYxvTs1FTmES59pyrp3zFpmdI")


def lgd_api_auth_headers(env, content_type=True):
    """Authorization (and Content-Type) for a call to the LGD platform.

    One place, so every endpoint presents the same credential. The offline
    order and replacement pushes already read the system parameter;
    product/add, utr-tracking, gst/update, orderStatusUpdate and the
    certificate fetch each carried their own copy of a hardcoded token, so
    rotating it fixed some calls and left the others answering
    "You are not logged-in user".
    """
    token = env['ir.config_parameter'].sudo().get_param(
        LGD_API_TOKEN_PARAM) or ''
    token = token.strip()
    if not token:
        _logger.warning(
            "%s is not set, so the LGD call falls back to the token compiled "
            "into the source. Set the parameter: the fallback cannot be "
            "rotated without a deploy.", LGD_API_TOKEN_PARAM)
        token = LGD_API_LEGACY_TOKEN
    headers = {'Authorization': 'Bearer %s' % token}
    if content_type:
        headers['Content-Type'] = 'application/json'
    return headers
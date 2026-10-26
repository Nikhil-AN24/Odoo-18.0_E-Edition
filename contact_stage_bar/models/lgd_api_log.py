"""One place that prints an API payload to the server log.

Every call between the LGD platform and Odoo goes through here, in both
directions, so a terminal tailing the Odoo log shows the whole conversation
rather than just werkzeug's one-line summary.

Each entry is framed and labelled with its direction, so the two sides are
never confused when several calls interleave:

    ==== LGD -> ODOO  POST /api/lgd/order/cart_add ====
    { ... the body as received ... }
    ==== end LGD -> ODOO ====
"""

import json
import logging

_logger = logging.getLogger(__name__)

IN = "LGD -> ODOO"
OUT = "ODOO -> LGD"

#: Header names whose value is a credential. Matched case-insensitively, as
#: a substring, so "X-Auth-Token" and "authorization" are both caught.
_SECRET_HEADERS = (
    'auth', 'token', 'key', 'secret', 'password', 'cookie', 'session',
)

#: ir.config_parameter: cap each printed body at this many characters.
#: 0 or unset means print it whole, which is the default - an order payload
#: runs to hundreds of lines and the point of this is to see all of it.
MAX_CHARS_PARAM = 'lgd.api_log_max_chars'


def _redact(headers):
    if not headers:
        return None
    out = {}
    for name, value in headers.items():
        low = str(name).lower()
        out[name] = '***' if any(s in low for s in _SECRET_HEADERS) else value
    return out


def _render(body, limit):
    """A payload as readable text, whatever form it arrived in."""
    if body is None:
        return '(no body)'
    if isinstance(body, (bytes, bytearray)):
        try:
            body = body.decode('utf-8')
        except UnicodeDecodeError:
            return '(%d bytes, not UTF-8)' % len(body)
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except (ValueError, TypeError):
            text = body                      # not JSON; print as-is
        else:
            text = json.dumps(body, indent=2, default=str, ensure_ascii=False)
    else:
        try:
            text = json.dumps(body, indent=2, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            text = repr(body)
    if limit and len(text) > limit:
        text = "%s\n... [%d more characters, raise %s to see them]" % (
            text[:limit], len(text) - limit, MAX_CHARS_PARAM)
    return text


def _limit(env):
    if env is None:
        return 0
    try:
        raw = env['ir.config_parameter'].sudo().get_param(MAX_CHARS_PARAM, '0')
        return max(0, int(raw or 0))
    except Exception:                         # never let logging break a call
        return 0


def log_api(direction, label, body=None, headers=None, env=None,
            response=None, status=None):
    """Print one API call.

    direction  IN or OUT, from this module.
    label      the method and path, e.g. "POST /api/lgd/order/cart_add".
    body       the request payload: dict, list, str or bytes.
    headers    optional; secret-looking names are redacted.
    env        optional; used only to read the truncation limit.
    response   optional reply body, for outbound calls.
    status     optional HTTP status of that reply.
    """
    try:
        limit = _limit(env)
        parts = ["", "==== %s  %s ====" % (direction, label)]
        safe = _redact(headers)
        if safe:
            parts.append("headers: %s" % json.dumps(safe, default=str))
        parts.append(_render(body, limit))
        if response is not None or status is not None:
            parts.append("---- response%s ----"
                         % ("" if status is None else " %s" % status))
            parts.append(_render(response, limit))
        parts.append("==== end %s ====" % direction)
        _logger.info("\n".join(parts))
    except Exception:
        # A logging failure must never take down the call it is describing.
        _logger.exception("Could not log the %s payload for %s",
                          direction, label)

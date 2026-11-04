from odoo import http
from odoo.http import request


class LgdCsrfToken(http.Controller):
    """Hand an already-open page a CSRF token for the session it is on.

    The token the web client posts with a report download is baked into the
    page HTML when that page is served (web.webclient_bootstrap), and it is
    an HMAC over the SESSION ID (odoo/http.py:csrf_token). Logging in
    rotates that id, so every tab opened before the login keeps posting a
    token that no longer matches, and core offers no way back except
    reloading the tab.

    Printing is where this surfaces, because /report/download is one of the
    few type='http' POSTs the client makes; everything else is JSON-RPC,
    which is not CSRF-checked at all. That is why a stale tab browses,
    edits and confirms perfectly well and only fails at Print.

    This route cannot be used to lift a token: it is a JSON route, so a
    browser will not hand the response to another origin, and a same-origin
    script could already read the token out of the page. auth='user' means
    a caller only ever receives a token for its own session.
    """

    @http.route('/lgd/session/csrf_token', type='json', auth='user')
    def lgd_session_csrf_token(self):
        return {'csrf_token': request.csrf_token()}

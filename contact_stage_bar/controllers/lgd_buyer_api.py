# -*- coding: utf-8 -*-
import json
import logging

from odoo import http
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request, Response

from .lgd_api_auth import lgd_check_api_token
from ..models.lgd_api_log import IN, log_api
from ..models.lgd_salesperson_sync import lgd_salesperson_info

_logger = logging.getLogger(__name__)

LGD_BUYER_API_REQUIRE_TOKEN_PARAM = 'lgd.buyer_api_require_token'


def _reply(body, status=200):
    _logger.info("[BUYER API] -> HTTP %s %s", status,
                 json.dumps(body, default=str)[:1000])
    return Response(json.dumps(body, default=str),
                    content_type='application/json', status=status)


def _error(message, status, code=None, **extra):
    body = {'status': 'error', 'code': code or status, 'message': message}
    body.update(extra)
    return _reply(body, status)


class LgdBuyerApi(http.Controller):

    @http.route('/api/lgd/buyer/upsert', type='http', auth='public',
                methods=['POST'], csrf=False, save_session=False)
    def lgd_buyer_upsert(self, **_kw):
        # Before the token check, so a refused call still shows what came in.
        log_api(IN, "POST /api/lgd/buyer/upsert",
                request.httprequest.get_data(as_text=True),
                headers=dict(request.httprequest.headers), env=request.env)
        require = (request.env['ir.config_parameter'].sudo().get_param(
            LGD_BUYER_API_REQUIRE_TOKEN_PARAM) or '').strip().lower()
        if require in ('1', 'true', 'yes'):
            denied = lgd_check_api_token(request)
            if denied:
                return denied
        else:
            _logger.info("[BUYER API] token check off (%s not set); "
                         "accepting the call without one.",
                         LGD_BUYER_API_REQUIRE_TOKEN_PARAM)

        try:
            data = json.loads(request.httprequest.get_data(as_text=True) or '{}')
        except ValueError:
            return _error("Body is not valid JSON.", 400, 'invalid_json')
        if isinstance(data, dict) and isinstance(data.get('buyer'), dict):
            data = data['buyer']
        if not isinstance(data, dict):
            return _error("Body must be a JSON object.", 400, 'invalid_body')

        email = (data.get('email') or '').strip()
        website_id = str(data.get('websiteBuyerId') or '').strip()
        missing = [k for k, v in (('email', email),
                                  ('websiteBuyerId', website_id)) if not v]
        if missing:
            return _error("Required: %s." % ', '.join(missing), 400,
                          'missing_fields', fields=missing)

        party = (data.get('partyType') or 'buyer').strip().lower()
        if party != 'buyer':
            return _error("This endpoint only takes buyers (partyType "
                          "'buyer').", 400, 'invalid_party_type')

        payload = dict(data, email=email, websiteBuyerId=website_id,
                       partyType='buyer')
        if not (payload.get('companyName') or '').strip():
            payload['companyName'] = ' '.join(filter(None, [
                (data.get('firstName') or '').strip(),
                (data.get('lastName') or '').strip()])) or email

        Partner = request.env['res.partner'].sudo()
        # The website id is the stable key; email is the fallback for an
        # account Odoo already had before the two were linked.
        target = Partner.with_context(active_test=False).search(
            [('lgd_website_buyer_id', '=', website_id)], limit=1)
        if not target:
            by_email = Partner.search([('email', '=ilike', email),
                                       ('parent_id', '=', False)], limit=1)
            if by_email.lgd_website_buyer_id \
                    and by_email.lgd_website_buyer_id != website_id:
                return _error(
                    "Email %s already belongs to Odoo contact %s, linked to "
                    "website buyer %s." % (email, by_email.id,
                                           by_email.lgd_website_buyer_id),
                    409, 'email_conflict',
                    odooPartnerId=by_email.id,
                    existingWebsiteBuyerId=by_email.lgd_website_buyer_id)
            if by_email.partner_kind == 'supplier':
                return _error(
                    "Email %s belongs to a supplier in Odoo, not a buyer."
                    % email, 409, 'email_is_supplier',
                    odooPartnerId=by_email.id)
            target = by_email

        _logger.info("[BUYER API] upsert websiteBuyerId=%s email=%s -> %s",
                     website_id, email,
                     'update %s' % target.id if target else 'create')
        try:
            with request.env.cr.savepoint():
                result = Partner._lgd_upsert_from_website(
                    payload, enforce_registration=False,
                    target=target or None)
        except (UserError, ValidationError, AccessError) as err:
            _logger.warning("[BUYER API] refused %s: %s", website_id, err)
            return _error(str(err), 422, 'validation_error')
        except Exception:
            _logger.exception("[BUYER API] failed for %s", website_id)
            return _error("Internal error while saving the buyer.", 500,
                          'server_error')

        if not isinstance(result, dict) or result.get('status') == 'error':
            message = (result or {}).get('message') if isinstance(result, dict) \
                else None
            return _error(message or "The buyer was not saved.", 422,
                          'validation_error')

        partner = Partner.browse(result.get('partner_id')).exists()
        if not partner:
            return _error("The buyer was not saved.", 500, 'server_error')

        salesperson_error = partner._lgd_apply_website_salesperson(
            lgd_salesperson_info(data))

        missing_fields = [label for name, label
                          in partner._LGD_ACCOUNT_REQUIRED_FIELDS
                          if not partner[name]]
        registration = partner._lgd_missing_registration_number()
        if registration:
            missing_fields.append(registration)

        created = not target
        return _reply({
            'status': 'created' if created else 'updated',
            'odooPartnerId': partner.id,
            'websiteBuyerId': partner.lgd_website_buyer_id,
            'email': partner.email,
            'stage': partner.stage_id.name or '',
            'missingFields': missing_fields,
            'salesperson': partner._lgd_salesperson_block(partner.user_id),
            'salespersonError': salesperson_error,
        }, 201 if created else 200)

# -*- coding: utf-8 -*-
import logging
import requests

from odoo import _, api, fields, models
from .lgd_api_auth import lgd_api_auth_headers
from .lgd_api_log import OUT, log_api

_logger = logging.getLogger(__name__)

LGD_BUYER_PUSH_PATH_PARAM = 'lgd_buyer_push_path'
LGD_BUYER_PUSH_DEFAULT_PATH = '/api/v1/odoo/buyer/create'
_WEBSITE_ID_KEYS = ('websiteBuyerId', 'buyerId', 'id', '_id', 'userId')
# Unreachable / 5xx / 408 / 429 stay pending; this many tries and it stops.
LGD_BUYER_PUSH_MAX_ATTEMPTS = 20


class ResPartner(models.Model):
    _inherit = 'res.partner'

    lgd_website_buyer_id = fields.Char(
        string="Website Buyer ID", copy=False, index=True, tracking=True,
        help="The LGD website's id for this buyer. Set when the website "
             "creates the buyer here, or when Odoo sends a buyer created "
             "here to the website. Both sides match on it.")
    lgd_website_sync_state = fields.Selection([
        ('pending', 'Waiting to send'),
        ('synced', 'On the website'),
        ('failed', 'Failed'),
    ], string="Website Sync", copy=False, readonly=True, tracking=True,
        help="Empty for accounts that existed before the sync, and for "
             "suppliers and child addresses, which are not synced.")
    lgd_website_sync_error = fields.Text(
        string="Website Sync Error", copy=False, readonly=True)
    lgd_website_sync_attempts = fields.Integer(copy=False, readonly=True)

    _sql_constraints = [
        ('lgd_website_buyer_id_unique', 'unique(lgd_website_buyer_id)',
         "Another contact already has this Website Buyer ID."),
    ]

    # ------------------------------------------------------------------
    # Marking new Odoo buyers for the push
    # ------------------------------------------------------------------
    def _lgd_is_syncable_buyer(self):
        self.ensure_one()
        return (self.partner_kind == 'buyer' and not self.parent_id
                and self.active)

    @api.model_create_multi
    def create(self, vals_list):
        partners = super().create(vals_list)
        if self.env.context.get('lgd_from_website'):
            partners.filtered(lambda p: p.lgd_website_buyer_id
                              or p._lgd_is_syncable_buyer()).sudo().write({
                'lgd_website_sync_state': 'synced'})
            return partners
        to_send = partners.filtered(lambda p: p._lgd_is_syncable_buyer()
                                    and not p.lgd_website_buyer_id)
        if to_send:
            to_send.sudo().write({'lgd_website_sync_state': 'pending'})
            _logger.info("[BUYER SYNC] Queued %s new buyer(s) for the "
                         "website: %s", len(to_send), to_send.ids)
            to_send._lgd_push_buyer_after_commit()
        return partners

    #: Fields the website holds for a buyer. Changing any of them in Odoo
    #: sends the buyer again; anything else (stage, tags, salesperson, the sync bookkeeping itself) does not.
    _LGD_WEBSITE_SYNCED_FIELDS = frozenset({
        'name', 'company_name', 'first_name', 'last_name',
        'contact_person_name', 'email', 'phone', 'mobile', 'lead_source',
        'street', 'street2', 'city', 'zip', 'state_id', 'country_id',
        'shipping_address_differs', 'shipping_street', 'shipping_street2',
        'shipping_city', 'shipping_zip', 'shipping_state_id',
        'shipping_country_id',
        'vat', 'ein_number', 'l10n_in_pan', 'gst_stages', 'ein_status',
        'user_id', 'l10n_in_gst_treatment',
    })

    def write(self, vals):
        res = super().write(vals)
        if self.env.context.get('lgd_from_website') \
                or not self._LGD_WEBSITE_SYNCED_FIELDS & set(vals):
            return res
        # Edited in Odoo: the website must get the change. A buyer it has
        # never received goes out as a create; one it has, as an update carrying its websiteBuyerId. Either way the same endpoint.
        changed = self.filtered(lambda p: p._lgd_is_syncable_buyer())
        if changed:
            changed.sudo().with_context(lgd_from_website=True).write({
                'lgd_website_sync_state': 'pending',
                'lgd_website_sync_attempts': 0,
            })
            changed._lgd_push_buyer_after_commit()
        return res

    def _lgd_push_buyer_after_commit(self):
        if not self.ids:
            return
        queue = self.env.cr.postcommit.data.setdefault(
            'lgd_buyer_push_ids', set())
        first = not queue
        queue.update(self.ids)
        if not first:
            return
        dbname, uid = self.env.cr.dbname, self.env.uid
        context = dict(self.env.context)

        def push():
            from odoo.modules.registry import Registry
            ids = sorted(queue)
            try:
                with Registry(dbname).cursor() as cr:
                    env = api.Environment(cr, uid, context)
                    buyers = env['res.partner'].sudo().browse(ids).exists() \
                        .filtered(lambda p: p.lgd_website_sync_state == 'pending')
                    buyers._lgd_push_buyer_to_website(timeout=8)
            except Exception:
                _logger.exception("[BUYER SYNC] Instant send of %s failed; "
                                  "the cron will retry.", ids)

        self.env.cr.postcommit.add(push)

    # ------------------------------------------------------------------
    # Odoo -> Website
    # ------------------------------------------------------------------
    def _lgd_website_buyer_payload(self):
        """The buyer block the website already parses on the offline order
        push, plus the Odoo id and PAN."""
        self.ensure_one()
        block = self.env['sale.order']._lgd_offline_buyer_block(self)
        block.update({
            'odooPartnerId': self.id,
            'websiteBuyerId': self.lgd_website_buyer_id or '',
            'panNumber': self.l10n_in_pan or '',
            'leadSource': self.lead_source or '',
            'partyType': 'buyer',
            # The exact status of the registration that applies - GSTIN in
            # India, EIN elsewhere, the same split the website makes. The
            # offline order block folds it to verified/pending; this
            # endpoint takes the four values as they are.
            'registrationStatus': self._lgd_website_registration_status(),
            # {"name", "email"} or null; null is still sent, and recorded.
            'salesperson': self._lgd_salesperson_block(self.user_id),
        })
        self._lgd_record_salesperson_error(None if self.user_id else _(
            "Sent to the website without a salesperson: none is assigned "
            "to this buyer in Odoo."))
        return block

    #: The only registrationStatus values /buyer/create accepts; anything else is refused with a 400.
    _LGD_WEBSITE_REGISTRATION_STATUSES = (
        'pending', 'verified', 'manual_verified', 'failed')

    def _lgd_website_registration_status(self):
        self.ensure_one()
        status = (self.gst_stages
                  if self.country_id.code == self._LGD_INDIA_COUNTRY_CODE
                  else self.ein_status)
        return (status if status in self._LGD_WEBSITE_REGISTRATION_STATUSES
                else 'pending')

    @api.model
    def _lgd_website_buyer_url(self):
        Param = self.env['ir.config_parameter'].sudo()
        base = (Param.get_param('base_augmont_url') or '').rstrip('/')
        if not base:
            return ''
        path = (Param.get_param(LGD_BUYER_PUSH_PATH_PARAM)
                or LGD_BUYER_PUSH_DEFAULT_PATH).strip()
        return base + '/' + path.lstrip('/')

    @staticmethod
    def _lgd_extract_website_id(data):
        for scope in (data, (data or {}).get('data') if isinstance(data, dict) else None):
            if not isinstance(scope, dict):
                continue
            for key in _WEBSITE_ID_KEYS:
                value = scope.get(key)
                if value not in (None, '', False):
                    return str(value)
        return ''

    def _lgd_push_buyer_to_website(self, timeout=20):
        """Send each buyer once. Returns True when the website accepted it.

        timeout is short for the instant send, which runs while the user's
        Save is still returning, and longer for the cron."""
        url = self._lgd_website_buyer_url()
        ok = True
        for partner in self.sudo():
            # A login's own contact (made with a res.users record) is not a buyer account, even when its creator's role tagged it one.
            if partner.user_ids:
                partner.write({'lgd_website_sync_state': False,
                               'lgd_website_sync_error': False})
                continue
            if not partner.email:
                partner.write({'lgd_website_sync_error': _(
                    "Waiting for an email address: the website identifies "
                    "buyers by email.")})
                ok = False
                continue
            if not url:
                partner._lgd_buyer_sync_fail(
                    _("base_augmont_url is not set, so there is nowhere to "
                      "send the buyer."), permanent=False)
                ok = False
                continue
            payload = partner._lgd_website_buyer_payload()
            attempt = partner.lgd_website_sync_attempts + 1
            headers = lgd_api_auth_headers(self.env)
            log_api(OUT, "POST %s" % url, payload, headers=headers,
                    env=self.env)
            try:
                response = requests.post(
                    url, json=payload, timeout=timeout, headers=headers)
                log_api(OUT, "reply from %s" % url, None, env=self.env,
                        response=response.text, status=response.status_code)
            except requests.exceptions.RequestException as err:
                # Down is not refused: keep waiting without using up attempts, the same as the order retry queue does.
                partner.write({'lgd_website_sync_error': _(
                    "Website unreachable, will retry: %s") % err})
                ok = False
                continue

            code = response.status_code
            if 200 <= code < 300:
                try:
                    data = response.json()
                except ValueError:
                    data = {}
                website_id = self._lgd_extract_website_id(data)
                vals = {'lgd_website_sync_state': 'synced',
                        'lgd_website_sync_error': False,
                        'lgd_website_sync_attempts': attempt}
                linked = partner.lgd_website_buyer_id
                if website_id and linked and website_id != linked:
                    # An update must come back with the id we sent. A
                    # different one means the website made a second record; keep the original link and let someone look.
                    vals['lgd_website_sync_error'] = _(
                        "Sent as an update of website buyer %(linked)s, but "
                        "the website answered with %(other)s. The link to "
                        "%(linked)s was kept; check the website for a "
                        "duplicate buyer.") % {'linked': linked,
                                               'other': website_id}
                    _logger.warning("[BUYER SYNC] %s: %s", partner.display_name,
                                    vals['lgd_website_sync_error'])
                    website_id = linked
                elif website_id and not linked:
                    clash = self.search([
                        ('lgd_website_buyer_id', '=', website_id),
                        ('id', '!=', partner.id)], limit=1)
                    if clash:
                        partner._lgd_buyer_sync_fail(
                            _("The website answered with buyer id %(id)s, "
                              "which is already on %(other)s.") % {
                                'id': website_id,
                                'other': clash.display_name},
                            permanent=True, attempt=attempt)
                        ok = False
                        continue
                    vals['lgd_website_buyer_id'] = website_id
                elif not website_id:
                    website_id = linked
                    _logger.warning(
                        "[BUYER SYNC] %s accepted by the website (HTTP %s) "
                        "but no buyer id came back: %s",
                        partner.display_name, code, response.text[:500])
                partner.write(vals)
                _logger.info("[BUYER SYNC] %s sent to the website; website "
                             "id %s", partner.display_name,
                             website_id or '(none returned)')
                continue

            if code == 404:
                # The website has not built the endpoint yet (or the path
                # parameter is wrong). Wait without using up attempts, so every buyer queued meanwhile goes out once it exists.
                partner.write({'lgd_website_sync_error': _(
                    "The website has no buyer endpoint at %s yet (HTTP 404). "
                    "Waiting; it will be sent once the endpoint exists.") % url})
                ok = False
                continue
            transient = code >= 500 or code in (408, 429)
            partner._lgd_buyer_sync_fail(
                _("Website refused the buyer with HTTP %(code)s: %(body)s") % {
                    'code': code, 'body': response.text[:1000]},
                permanent=not transient, attempt=attempt)
            ok = False
        return ok

    def _lgd_buyer_sync_fail(self, message, permanent, attempt=None):
        self.ensure_one()
        attempt = attempt or self.lgd_website_sync_attempts
        give_up = permanent or attempt >= LGD_BUYER_PUSH_MAX_ATTEMPTS
        self.write({
            'lgd_website_sync_state': 'failed' if give_up else 'pending',
            'lgd_website_sync_error': message,
            'lgd_website_sync_attempts': attempt,
        })
        _logger.warning("[BUYER SYNC] %s: %s%s", self.display_name, message,
                        '' if not give_up else ' (stopped retrying)')

    def action_lgd_resend_buyer_to_website(self):
        """Put a failed buyer back in the queue and try at once."""
        buyers = self.filtered(lambda p: p._lgd_is_syncable_buyer())
        buyers.sudo().write({'lgd_website_sync_state': 'pending',
                             'lgd_website_sync_attempts': 0})
        buyers._lgd_push_buyer_to_website()
        return True

    @api.model
    def _cron_lgd_push_buyers(self, limit=50):
        pending = self.sudo().search(
            [('lgd_website_sync_state', '=', 'pending')],
            order='id asc', limit=limit)
        if pending:
            _logger.info("[BUYER SYNC] Sending %s pending buyer(s).",
                         len(pending))
            pending._lgd_push_buyer_to_website()

# -*- coding: utf-8 -*-
"""Replacement push, Odoo -> website.

When Procurement replaces a stone from inside Odoo, the website knows
nothing about the replacement: it has never seen the new stone, and it has
no order id for it. The status endpoint cannot carry that - it only moves a
status word on a line that already exists - so the website exposes a
dedicated endpoint for this, and Odoo posts the new stone's details to it.

The website answers with the orderUniqueId it allocated for the new line,
which Odoo stores. That is what stops a procurement-raised replacement
sharing the original's id: the website names the new line, exactly as it
already does for an offline order push.
"""
import json
import logging
from odoo.addons.contact_stage_bar.models.lgd_api_log import (
    IN, OUT, log_api)
import re

import requests

from odoo import models, fields, _

_logger = logging.getLogger(__name__)

#: Where the website receives a replacement raised in Odoo. Overridable
#: through ir.config_parameter so the path can move without a code change -
#: the same arrangement as lgd_offline_order_path.
LGD_REPLACEMENT_PATH_PARAM = 'lgd_replacement_path'
LGD_REPLACEMENT_DEFAULT_PATH = '/api/v1/odoo/order/replace-product'

#: The merchant JWT this endpoint expects. Falls back to the offline order
#: token, which is the same bearer in every environment so far.
LGD_REPLACEMENT_TOKEN_PARAM = 'lgd_merchant_jwt'

LGD_REPLACEMENT_CONNECT_TIMEOUT = 10
LGD_REPLACEMENT_TIMEOUT = 30

#: manualStone.location is an ISO 3166-1 alpha-3 code ("IND"), per their
#: samples. Odoo's res.country carries only alpha-2, and there is no alpha-3
#: column to read, so the trading countries are mapped here. Anything not
#: listed falls back to the alpha-2 code with a warning: a wrong code is
#: worse than a visible gap, and their endpoint rejects a blank one outright.
LGD_ALPHA2_TO_ALPHA3 = {
    'IN': 'IND', 'US': 'USA', 'BE': 'BEL', 'IL': 'ISR', 'AE': 'ARE',
    'HK': 'HKG', 'CN': 'CHN', 'TH': 'THA', 'SG': 'SGP', 'GB': 'GBR',
    'CH': 'CHE', 'ZA': 'ZAF', 'BW': 'BWA', 'RU': 'RUS', 'CA': 'CAN',
    'AU': 'AUS', 'JP': 'JPN', 'DE': 'DEU', 'FR': 'FRA', 'IT': 'ITA',
    'NL': 'NLD', 'ES': 'ESP', 'TR': 'TUR', 'VN': 'VNM', 'MY': 'MYS',
    'ID': 'IDN', 'LK': 'LKA', 'BD': 'BGD', 'NP': 'NPL', 'PK': 'PAK',
    'MU': 'MUS', 'KE': 'KEN', 'NG': 'NGA', 'EG': 'EGY', 'BR': 'BRA',
    'MX': 'MEX', 'AR': 'ARG', 'KR': 'KOR', 'TW': 'TWN', 'NZ': 'NZL',
    'IE': 'IRL', 'PL': 'POL', 'PT': 'PRT', 'SE': 'SWE', 'NO': 'NOR',
    'DK': 'DNK', 'FI': 'FIN', 'AT': 'AUT', 'CZ': 'CZE', 'GR': 'GRC',
    'HU': 'HUN', 'RO': 'ROU', 'UA': 'UKR', 'SA': 'SAU', 'QA': 'QAT',
    'KW': 'KWT', 'BH': 'BHR', 'OM': 'OMN', 'JO': 'JOR', 'LB': 'LBN',
    'AM': 'ARM', 'GE': 'GEO', 'KZ': 'KAZ', 'UZ': 'UZB', 'AO': 'AGO',
    'NA': 'NAM', 'CD': 'COD', 'TZ': 'TZA', 'ZW': 'ZWE', 'GH': 'GHA',
    'LS': 'LSO', 'SZ': 'SWZ', 'ZM': 'ZMB', 'LU': 'LUX', 'MC': 'MCO',
}

#: manualStone fields their contract marks required. Checked before sending
#: so a blank one is named here rather than coming back as an opaque 400.
LGD_MANUAL_STONE_REQUIRED = (
    'vendor', 'shape', 'cut', 'clarity', 'carat', 'polish', 'symmetry',
    'pricePerCarat', 'length', 'width', 'height', 'tableDepth',
    'depthPercentage', 'location',
)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    # ------------------------------------------------------------------
    # Where
    # ------------------------------------------------------------------
    def _lgd_replacement_push_url(self):
        Param = self.env['ir.config_parameter'].sudo()
        base = (Param.get_param('base_augmont_url') or '').rstrip('/')
        if not base:
            return False
        path = (Param.get_param(LGD_REPLACEMENT_PATH_PARAM)
                or LGD_REPLACEMENT_DEFAULT_PATH)
        return '%s%s' % (base, path if path.startswith('/') else '/' + path)

    # ------------------------------------------------------------------
    # What
    # ------------------------------------------------------------------
    def _lgd_replacement_identifier(self, new_line):
        """The website's own handle for this stone, when it has one.

        productIdentifier means a stone already in THEIR inventory, which is
        only true of a stone that came from their catalogue in the first
        place - website_product_id is what records that. A stone Procurement
        sourced from an IGI report has never been on their platform, so it
        has to travel as a full manualStone spec instead.
        """
        template = new_line.product_template_id
        if not template.website_product_id:
            return ''
        for value in (template.certificate, template.lgd_stock_number,
                      template.stock_number):
            if (value or '').strip():
                return value.strip()
        return ''

    def _lgd_replacement_price_per_carat(self, new_line):
        """Vendor price per carat for the new stone."""
        # Seeded from the wizard's own entry where that field exists; the
        # guard matches _create_replacement, which writes it the same way.
        if 'procurement_price_per_carat' in new_line._fields:
            per_carat = new_line.procurement_price_per_carat or 0.0
            if per_carat:
                return float(per_carat)
        template = new_line.product_template_id
        if template.price_per_carat:
            return float(template.price_per_carat)
        carat = float(template.carat_value or 0.0)
        if carat:
            return round(float(template.final_price or 0.0) / carat, 2)
        return 0.0

    @staticmethod
    def _lgd_dimensions_from_measurements(text):
        """Length, width and height out of a measurements string.

        The Replace Stone wizard collects `measurements` but not the three
        dimensions separately, and _spec_vals does not carry them onto the
        product - so a replacement stone has the numbers only inside this
        string. The website requires all three, so they are read back out
        rather than sent as zeros.

        Handles "7.34 - 7.38 x 4.52" and "6.51 - 6.5 * 4.02"; anything that
        does not yield exactly three numbers is left alone.
        """
        numbers = re.findall(r'\d+(?:\.\d+)?', str(text or ''))
        if len(numbers) != 3:
            return ()
        try:
            return tuple(float(n) for n in numbers)
        except (TypeError, ValueError):
            return ()

    def _lgd_replacement_location(self, new_line):
        """manualStone.location — where the stone is, as an alpha-3 code.

        The Replace Stone wizard captures no country: _spec_vals carries
        fourteen fields and country is not one of them, so a replacement
        stone's own country is almost always empty. The vendor's country
        comes next - the stone sits with whoever is supplying it - and the
        company's country last, which for a replacement Augmont is sourcing
        is the honest answer rather than a guess.
        """
        template = new_line.product_template_id
        sources = (
            ('stone', template.country_id),
            ('vendor', new_line.vendor_id.country_id
             if new_line.vendor_id else False),
            ('company', self.company_id.country_id),
        )
        for label, country in sources:
            code = (country.code or '').strip().upper() if country else ''
            if not code:
                continue
            alpha3 = LGD_ALPHA2_TO_ALPHA3.get(code)
            if alpha3:
                return alpha3
            _logger.warning(
                "[REPLACEMENT PUSH] %s: no alpha-3 code known for %s country "
                "%r; sending it as-is. Add it to LGD_ALPHA2_TO_ALPHA3 if the "
                "website refuses it.",
                self.sdk_augmont_number or self.name, label, code)
            return code
        _logger.warning(
            "[REPLACEMENT PUSH] %s: no country on the stone, the vendor or "
            "the company, so manualStone.location will be blank and the "
            "website will refuse the replacement. Set a Country on vendor "
            "%s or on the company.",
            self.sdk_augmont_number or self.name,
            new_line.vendor_id.display_name or '(none)')
        return ''

    def _lgd_manual_stone_block(self, new_line):
        """The new stone, in the manualStone shape their contract defines."""
        template = new_line.product_template_id
        length = float(template.length or 0.0)
        width = float(template.width or 0.0)
        height = float(template.depth or 0.0)
        if not (length and width and height):
            parsed = self._lgd_dimensions_from_measurements(
                template.measurements)
            if parsed:
                length, width, height = parsed
                _logger.info(
                    "[REPLACEMENT PUSH] %s: dimensions read from "
                    "measurements %r -> %s x %s x %s.",
                    self.sdk_augmont_number or self.name,
                    template.measurements, length, width, height)
        return {
            'vendor': new_line.vendor_id.name or '',
            # Not in their published manualStone sample, but asked for: a
            # replacement sourced from an IGI report has a certificate, and
            # without it the stone lands on the website uncertified.
            'certNumber': template.certificate or '',
            'lab': template.labs or '',
            'shape': template.shapes or '',
            'cut': template.cut or '',
            'color': template.color or '',
            'clarity': template.clarity or '',
            'carat': float(template.carat_value or 0.0),
            'polish': template.polish or '',
            'symmetry': template.symmetry or '',
            'pricePerCarat': self._lgd_replacement_price_per_carat(new_line),
            'length': length,
            'width': width,
            'height': height,
            'tableDepth': float(template.table_pct or 0.0),
            'depthPercentage': float(template.depth_pct or 0.0),
            'location': self._lgd_replacement_location(new_line),
            'growthType': template.treatments or '',
            'luster': template.luster or '',
            'image': template.image_augmont or None,
            'video': template.video_360 or None,
        }

    def _lgd_replacement_payload(self, original, new_line):
        """The replacement, as the website needs to receive it.

        Either productIdentifier or manualStone, never both - their contract
        is explicit about that, and sending both would leave them to guess
        which stone was meant.
        """
        self.ensure_one()
        payload = {
            'orderUniqueId': self._lgd_status_unique_id(original.order_number),
            'user': self._lgd_status_user_block(self.env.user),
        }
        identifier = self._lgd_replacement_identifier(new_line)
        if identifier:
            payload['productIdentifier'] = identifier
            return payload

        stone = self._lgd_manual_stone_block(new_line)
        missing = [name for name in LGD_MANUAL_STONE_REQUIRED
                   if not stone.get(name)]
        if missing:
            # Said plainly here, or the 400 that follows looks like the
            # website rejecting us for no reason. tableDepth and
            # depthPercentage are the usual two: the Replace Stone wizard
            # does not ask for them, and only an IGI lookup fills them.
            _logger.warning(
                "[REPLACEMENT PUSH] %s: manualStone is missing %s. The "
                "website requires these and will refuse the replacement.",
                self.sdk_augmont_number or self.name, ', '.join(missing))
        payload['manualStone'] = stone
        return payload

    # ------------------------------------------------------------------
    # Send
    # ------------------------------------------------------------------
    def _lgd_push_replacement(self, original, new_line):
        """POST a replacement raised in Odoo and store the id that comes back.

        Returns True on success. Never raises: this runs inside the Replace
        Stone wizard, and a website outage must not undo a replacement
        Procurement has already made. A failure queues an augmont.api.retry
        row for the existing cron, exactly like the offline push.
        """
        self.ensure_one()
        url = self._lgd_replacement_push_url()
        if not url:
            _logger.warning(
                "[REPLACEMENT PUSH] %s: base_augmont_url is not set, so the "
                "website cannot be told about this replacement. The new stone "
                "is reporting under the original's order id.",
                self.sdk_augmont_number or self.name)
            return False

        payload = self._lgd_replacement_payload(original, new_line)
        headers = {'Content-Type': 'application/json'}
        Param = self.env['ir.config_parameter'].sudo()
        token = (Param.get_param(LGD_REPLACEMENT_TOKEN_PARAM)
                 or Param.get_param('lgd_offline_order_token'))
        if token:
            headers['Authorization'] = 'Bearer %s' % token
        else:
            # Their endpoint requires it and answers 401 without it. Worth
            # saying before the call rather than reading it off the response.
            _logger.warning(
                "[REPLACEMENT PUSH] %s: no merchant token set (%s or "
                "lgd_offline_order_token), and this endpoint requires one. "
                "Expect HTTP 401.",
                self.sdk_augmont_number or self.name,
                LGD_REPLACEMENT_TOKEN_PARAM)

        body = json.dumps(payload)
        _logger.info(
            "[REPLACEMENT PUSH] POST %s | auth=%s | %s replaces %s | mode=%s",
            url, 'yes' if 'Authorization' in headers else 'NO',
            new_line.certificate or new_line.id,
            original.order_number or original.id,
            'productIdentifier' if 'productIdentifier' in payload
            else 'manualStone')

        log_api(OUT, "POST %s" % url, body, headers=headers, env=self.env)
        try:
            response = requests.post(
                url, headers=headers, data=body,
                timeout=(LGD_REPLACEMENT_CONNECT_TIMEOUT,
                         LGD_REPLACEMENT_TIMEOUT))
        except requests.exceptions.RequestException as error:
            _logger.error(
                "[REPLACEMENT PUSH] %s: no response from %s - %s. Queued for "
                "retry.", self.sdk_augmont_number or self.name, url, error)
            self._lgd_queue_replacement_retry(
                new_line, payload, headers, str(error), False)
            return False

        _logger.info("[REPLACEMENT PUSH] <- HTTP %s | body: %s",
                     response.status_code, (response.text or '')[:600])

        if response.status_code != 200:
            self._lgd_queue_replacement_retry(
                new_line, payload, headers, response.text,
                str(response.status_code))
            return False

        try:
            data = response.json()
        except ValueError:
            self._lgd_queue_replacement_retry(
                new_line, payload, headers,
                'Response was not JSON: %s' % response.text,
                str(response.status_code))
            return False

        self._lgd_store_replacement_unique_id(new_line, data)
        self._lgd_mark_replacement_pushed(original, new_line)
        return True

    def _lgd_mark_replacement_pushed(self, original, new_line):
        """Stop the ordinary status push repeating what replace-product just did.

        That endpoint does the whole swap on their side: it cancels the
        original line and adds the replacement as a new Diamond Booked line.
        Both stones are therefore already at their final status on the
        website before Odoo's own status push runs.

        Left alone, that push then sends "Replaced" for the original - which
        the website now holds as **Cancelled**, a final status nothing moves
        out of - and the call is refused with "Status transition from
        'Cancelled' to 'Replaced' is not allowed". The same goes for the new
        line, which they have already created as Diamond Booked.

        The two systems simply spell the same event differently: Odoo closes
        the original as Replaced, the website cancels it. Neither is wrong,
        and neither needs telling twice.
        """
        self.ensure_one()
        cr = self.env.cr
        seen = getattr(cr, '_lgd_pushed_statuses', None)
        if seen is None:
            seen = set()
            cr._lgd_pushed_statuses = seen
        for line, status in ((original, 'Replaced'),
                             (new_line, 'Diamond Booked')):
            if line.order_number:
                seen.add((self.id, str(line.order_number), status))
        _logger.info(
            "[REPLACEMENT PUSH] %s: the website has applied the swap itself, "
            "so '%s → Replaced' and '%s → Diamond Booked' will not be sent "
            "again by the status push.",
            self.sdk_augmont_number or self.name,
            original.order_number or '(none)',
            new_line.order_number or '(none)')

    # ------------------------------------------------------------------
    # The id that comes back
    # ------------------------------------------------------------------
    def _lgd_store_replacement_unique_id(self, new_line, response_data):
        """Write the website's id for the replacement line onto that line.

        Their contract puts it at data.newOrder.orderUniqueId. The flatter
        spellings are still read as a fallback: this endpoint is new on both
        sides, and silently storing nothing would leave the two stones
        sharing one id with nothing in the log to explain it.
        """
        self.ensure_one()
        data = (response_data or {}).get('data') or {}
        new_order = data.get('newOrder') or {}

        unique_id = (new_order.get('orderUniqueId')
                     or data.get('orderUniqueId')
                     or data.get('newOrderUniqueId'))

        if not unique_id:
            _logger.error(
                "[REPLACEMENT PUSH] %s: the website accepted the replacement "
                "but returned no newOrder.orderUniqueId, so the new stone "
                "keeps the original's id (%s) and the two share one id. "
                "Response: %s",
                self.sdk_augmont_number or self.name,
                new_line.order_number or '(none)',
                json.dumps(response_data)[:400])
            return False

        unique_id = str(unique_id).strip()
        if unique_id == str(new_line.order_number or ''):
            return True

        clash = self.env['sale.order.line'].sudo().search([
            ('order_number', '=', unique_id),
            ('id', '!=', new_line.id),
        ], limit=1)
        if clash:
            _logger.error(
                "[REPLACEMENT PUSH] %s: the website returned orderUniqueId %s "
                "for the replacement, but line %s (order %s) already holds "
                "it. Not stored - status updates for it could not be routed.",
                self.sdk_augmont_number or self.name, unique_id, clash.id,
                clash.order_id.name)
            return False

        new_line.sudo().with_context(
            from_website_api=True).write({'order_number': unique_id})
        _logger.info(
            "[REPLACEMENT PUSH] %s: replacement line %s is orderUniqueId %s "
            "on the website.",
            self.sdk_augmont_number or self.name, new_line.id, unique_id)

        # Their price for the new line, recorded but NOT applied. Odoo has
        # already decided what the customer pays - holding the original
        # price when the replacement is within the agreed threshold, and
        # asking Sales to approve it when it is not. Overwriting that here
        # would undo a decision that may still be awaiting sign-off.
        their_price = new_order.get('invoiceAmount')
        if their_price not in (None, '') and \
                abs(float(their_price) - (new_line.price_unit or 0.0)) > 0.01:
            _logger.warning(
                "[REPLACEMENT PUSH] %s: the website priced the replacement at "
                "%s; Odoo is charging %s. Odoo's figure stands. Worth "
                "reconciling if this keeps happening.",
                self.sdk_augmont_number or self.name,
                their_price, new_line.price_unit)
        return True

    # ------------------------------------------------------------------
    # Failure
    # ------------------------------------------------------------------
    def _lgd_queue_replacement_retry(self, new_line, payload, headers,
                                     error, status_code):
        """Hand a failed replacement push to the augmont.api.retry cron."""
        self.ensure_one()
        url = self._lgd_replacement_push_url()
        _logger.warning("[REPLACEMENT PUSH] failed for %s (%s): %s",
                        self.sdk_augmont_number or self.name,
                        status_code, error)
        self.message_post(
            body=_("The website could not be told about this stone "
                   "replacement (%(code)s). The replacement itself is saved; "
                   "the retry job will send it again. Until it succeeds the "
                   "new stone has no order id of its own on the website.",
                   code=status_code or 'no response'),
            subject=_("Replacement push failed"))
        self.env['augmont.api.retry'].sudo().create({
            'order_id': self.id,
            'order_line_id': new_line.id,
            # Without this the cron would POST this body to the status
            # endpoint, which is its hardcoded default.
            'lgd_endpoint_url': url,
            'sdk_augmont_number': self.sdk_augmont_number,
            'order_number': new_line.order_number or '',
            'payload_json': json.dumps(payload),
            'headers_json': json.dumps(headers),
            'new_status': 'Replaced',
            'error_message': error,
            'status_code': status_code or '',
            'state': 'pending',
            'updated_on': fields.Datetime.now(),
        })

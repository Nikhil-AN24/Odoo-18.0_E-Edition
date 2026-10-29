import json
import logging
import re
from odoo.addons.contact_stage_bar.models.lgd_api_log import (
    IN, OUT, log_api)
import requests

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .lgd_inventory_sale_line import LGD_DEAD_STATUSES, LINE_STATUS_TO_AUGMONT

_logger = logging.getLogger(__name__)

LGD_OFFLINE_PUSH_TIMEOUT = 20
# Separate CONNECT timeout, deliberately short. A hostname resolves to several
# Cloudflare addresses and socket.create_connection tries each one with the
# full connect timeout, so a single unreachable tunnel cost 55s of a confirm -
# the sales team watching a spinner while the website is down is exactly what
# this push is designed never to do. Five seconds is far longer than a healthy
# connect (20ms observed) and bounds the worst case to a few seconds.
LGD_OFFLINE_PUSH_CONNECT_TIMEOUT = 5


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    # The last push failure as the website worded it. Kept so the retry cron
    # can tell a NEW problem from the one already in the chatter: without it
    # every sweep would post the same paragraph again. Cleared on success.
    lgd_offline_push_error = fields.Text(
        string="Last Website Push Error", copy=False, readonly=True)

    # ------------------------------------------------------------------
    # Payload
    # ------------------------------------------------------------------
    def _lgd_offline_name_parts(self, partner):
        source = ((partner.contact_person_name or '').strip()
                  if 'contact_person_name' in partner._fields else '')
        source = (source or (partner.name or '').strip()
                  or (partner.parent_id.name or '').strip())
        parts = source.split()
        if not parts:
            return '', ''
        if len(parts) == 1:
            _logger.warning(
                "Offline push %s: '%s' has a one-word name and no contact "
                "person, so the surname is repeated to satisfy the website. "
                "Set a Contact Person Name to send a real one.",
                self.sdk_augmont_number or self.name, parts[0])
            return parts[0], parts[0]
        return parts[0], ' '.join(parts[1:])

    _LGD_OFFLINE_DEFAULT_COUNTRY = 'IN'

    def _lgd_offline_country_code(self, partner, label=''):
        own = (partner.country_id.code if partner else '') or ''
        for candidate in (own, self.company_id.country_id.code):
            code = (candidate or '').strip().upper()
            if len(code) == 2 and code.isalpha():
                if not own and partner:
                    _logger.warning(
                        "Offline push %s: %s '%s' has no Country set; sending "
                        "the company's %s instead. Set a Country on the "
                        "contact to send the real one.",
                        self.sdk_augmont_number or self.name,
                        label or 'contact', partner.display_name, code)
                return code
        if partner:
            _logger.warning(
                "Offline push %s: %s '%s' has no Country and neither does the "
                "company; falling back to %s.",
                self.sdk_augmont_number or self.name, label or 'contact',
                partner.display_name, self._LGD_OFFLINE_DEFAULT_COUNTRY)
        return self._LGD_OFFLINE_DEFAULT_COUNTRY

    def _lgd_offline_address(self, partner):
        """One address block in the website's shape."""
        first, last = self._lgd_offline_name_parts(partner)
        fallback = partner.parent_id
        return {
            'firstName': first,
            'lastName': last,
            'mobileNumber': (partner.mobile or partner.phone
                             or fallback.mobile or fallback.phone or ''),
            'address': ', '.join(filter(None, [partner.street, partner.street2])),
            'landmark': '',
            'city': partner.city or '',
            'state': partner.state_id.name or '',
            'postalCode': partner.zip or '',
            'countryIsoCode': self._lgd_offline_country_code(partner, 'buyer'),
        }

    def _lgd_offline_tax_rates(self):
        """Tax RATES, not amounts - matching what the website already receives
        from the KYC/GST update call. Read from the tax definitions rather than
        from the computed totals so a zero-value order still reports its rates.
        """
        sgst = cgst = igst = 0.0
        for line in self.order_line:
            for tax in line.tax_id:
                description = (tax.description or tax.name or '').upper()
                if 'IGST' in description:
                    igst = tax.amount
                elif 'GST' in description:
                    sgst = cgst = tax.amount / 2
            if sgst or cgst or igst:
                break
        return sgst, cgst, igst

    def _lgd_offline_carat(self, line):

        match = re.search(r'([\d.]+)', str(line.carat_weight or ''))
        if match:
            try:
                return float(match.group(1))
            except (TypeError, ValueError):
                pass
        return float(line.product_template_id.carat_value or 0.0)

    def _lgd_offline_price_block(self, line):
        """The four price fields, never left at zero when the line knows better.

        `*Margin` is the customer-facing figure (what we sell the stone for);
        the other two are the pre-margin equivalents. Per-carat values are
        derived by dividing by the stone's weight, which is the only honest
        way to get them when nothing recorded them directly.
        """
        template = line.product_template_id
        carat = self._lgd_offline_carat(line)

        final_margin = float(line.price_unit or 0.0)
        final_base = float(template.final_price or 0.0) or final_margin
        per_carat_margin = float(template.final_price_margin or 0.0)
        per_carat_base = float(template.price_per_carat or 0.0)
        if carat:
            per_carat_margin = per_carat_margin or (final_margin / carat)
            per_carat_base = per_carat_base or (final_base / carat)
        return {
            'pricePerCarat': per_carat_base,
            'pricePerCaratMargin': per_carat_margin,
            'finalPrice': final_base,
            'finalPriceMargin': final_margin,
        }

    def _lgd_offline_product_block(self, line):
        """The `product` block for one stone."""
        template = line.product_template_id
        return {
            'title': template.name or line.name or '',
            'stockNum': (line.lgd_stock_number or template.stock_number
                         or template.lgd_stock_number or ''),
            # From the LINE, not the template: a non-certified parcel shares
            # its product with other lines, so the template's SKU would name all of them identically.
            'lgdStockNum': line.lgd_stock_number or '',
            # certNumber / lab / certType, built as one unit - see
            # product.template._lgd_cert_block.
            **template._lgd_cert_block(),
            # Natural vs Lab Grown. Without it the website has nothing to
            # file the stone under and shows every offline stone as Lab
            # Grown - which is right for its own catalogue and wrong for a
            # natural stone Sales raised from an RFQ. Taken from the LINE:
            # one invoice can carry both kinds, and the line is where that
            # distinction is held.
            'stoneType': dict(
                line._fields['stone_type'].selection
            ).get(line.stone_type, '') if line.stone_type else '',
            'shape': template.shapes or '',
            'weight': self._lgd_offline_carat(line),
            'color': template.color or '',
            'clarity': template.clarity or '',
            'cut': template.cut or '',
            'polish': template.polish or '',
            'symmetry': template.symmetry or '',
            'shade': template.shade or '',
            'treatment': template.treatments or '',
            'eyeClean': template.eye_clean or '',
            'measurements': template.measurements or '',
            'length': float(template.length or 0.0),
            'width': float(template.width or 0.0),
            'height': float(template.depth or 0.0),
            'fluorescenceColor': template.fluorescence_color or '',
            'fluorescenceIntensity': template.fluorescence_intensity or '',
            'country': template.country_id.name or '',
            **self._lgd_offline_price_block(line),
            'diamondImage': template.image_augmont or '',
            'diamondVideo360': template.video_360 or '',
        }

    def _lgd_offline_seller_block(self, line):
        vendor = line.vendor_id
        first, last = self._lgd_offline_name_parts(vendor) if vendor else ('', '')
        if not vendor:
            _logger.warning(
                "Offline push %s: line %s (%s) has no vendor, so its seller "
                "block is empty.",
                self.sdk_augmont_number or self.name, line.id,
                line.product_template_id.display_name or '-')
        return {
            'companyName': vendor.name or '',
            'firstName': first,
            'lastName': last,
            'email': vendor.email or '',
            'mobileNumber': vendor.mobile or vendor.phone or '',
            'countryIsoCode': self._lgd_offline_country_code(vendor, 'vendor'),
        }

    #: Addresses that cannot belong to a real seller on the website. A vendor
    #: invented in Odoo for an offline order gets one of these, and the
    #: website then refuses the whole order with "Seller profile not found".
    _LGD_PLACEHOLDER_EMAIL_PREFIXES = ('dummy', 'test', 'noreply', 'no-reply')

    _LGD_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]{2,}$')

    def _lgd_warn_unusable_sellers(self):
        """Name vendors whose email the website cannot match, before sending.

        The website looks a seller up by email and refuses the ENTIRE order
        when it finds none - one unusable vendor loses every line. Said here,
        the cause is obvious; left to the response it arrives as
        "Seller profile not found for dummyX@augmont.com" with nothing
        pointing at which Odoo record to fix.

        A warning, never a block: the push must not stop an order being
        booked, and the vendor can be corrected and the order re-sent.
        """
        self.ensure_one()
        problems = []
        for vendor in self._lgd_offline_lines().mapped('vendor_id'):
            email = (vendor.email or '').strip()
            if not email:
                problems.append(_("%s has no email") % vendor.display_name)
                continue
            if not self._LGD_EMAIL_RE.match(email):

                problems.append(
                    _("%(name)s has an email that is not an address "
                      "(%(email)s)")
                    % {'name': vendor.display_name, 'email': email})
                continue
            local = email.split('@', 1)[0].lower()
            if local.startswith(self._LGD_PLACEHOLDER_EMAIL_PREFIXES):
                problems.append(
                    _("%(name)s has a placeholder email (%(email)s)")
                    % {'name': vendor.display_name, 'email': email})
        if problems:
            _logger.warning(
                "Offline push %s: the website matches sellers by email and "
                "will refuse this order - %s. Set the address their seller "
                "profile is registered under.",
                self.sdk_augmont_number or self.name, '; '.join(problems))
        return problems

    def _lgd_warn_missing_lab(self):
        self.ensure_one()
        problems = []
        for line in self._lgd_offline_lines():
            template = line.product_template_id
            # An ungraded stone is sent as Non-Cert by _lgd_cert_block and
            # is accepted. Only a stone that HOLDS a certificate with no Lab
            # recorded still goes out blank - it gets no fallback, because
            # calling a certified stone uncertified would be a lie - so that
            # is the one case left worth raising.
            if ((template.certificate or '').strip()
                    and not (template.labs or '').strip()):
                problems.append(
                    _("%(stock)s holds certificate %(cert)s but has no Lab set")
                    % {'stock': (line.lgd_stock_number
                                 or template.lgd_stock_number
                                 or template.display_name),
                       'cert': (template.certificate or '').strip()})
        if problems:
            _logger.warning(
                "Offline push %s: the website requires a grading lab on "
                "every stone and will refuse this order - %s. Set the Lab "
                "field on the product; the sweep will pick it up.",
                self.sdk_augmont_number or self.name, '; '.join(problems))
        return problems

    # The status every stone is expected to hold when the order is punched.
    _LGD_OFFLINE_PUSH_STATUS = 'diamond_booked'

    def _lgd_offline_lines(self):
        """Stone lines the website should be told about.

        Excluded:
          * section and note rows - they carry no stone;
          * MELEE parent rows - summaries of their children, not goods;
          * dead lines (LGD_DEAD_STATUSES) - a replaced or cancelled stone is
            never going to ship. Sending one would have the website allocate
            an orderUniqueId for it and then wait forever for status updates
            that cannot come, and it would show the customer a diamond that
            is not part of their order.

        A live line in some OTHER status is still sent: the order has simply
        moved on faster than the push, and withholding it would leave the
        website missing a stone it is going to be invoiced for.
        _lgd_push_offline_order logs those so they are visible.
        """
        return self.order_line.filtered(
            lambda l: not l.display_type and not l.is_melee_parent
            and l.availability_status not in LGD_DEAD_STATUSES)

    # The website requires an integer in this range on every line.
    _LGD_OFFLINE_UNIQUE_ID_BASE = 90000000
    _LGD_OFFLINE_UNIQUE_ID_MAX = 2147483647

    def _lgd_offline_unique_id(self, line):
        """The per-line id the website expects, derived from the Odoo line id.

        Derived rather than drawn from a sequence on purpose: it has to be
        STABLE. A retry, a manual re-send and the original push must all carry
        the same id, or the website would book the same stone three times
        under three different references. A sequence hands out a new number on
        every call; the line id never changes, so this cannot drift.

        It also needs no storage. order_number holds what the website sends
        back, and must keep doing so - overloading it to mean "the id we
        allocated" would make a failed push look like a successful one and
        the order could never be re-sent.
        """
        unique_id = self._LGD_OFFLINE_UNIQUE_ID_BASE + line.id
        if unique_id > self._LGD_OFFLINE_UNIQUE_ID_MAX:
            _logger.error(
                "Offline push %s: line %s produces orderUniqueId %s, past the "
                "website's maximum of %s.", self.sdk_augmont_number or
                self.name, line.id, unique_id, self._LGD_OFFLINE_UNIQUE_ID_MAX)
        return unique_id

    def _lgd_offline_line_status(self, line):

        status = line.availability_status or ''
        word = LINE_STATUS_TO_AUGMONT.get(status)
        if not word:
            _logger.warning(
                "Offline push %s: line %s has status %r, which has no website "
                "equivalent. Sending 'Diamond Booked'. Add it to "
                "LINE_STATUS_TO_AUGMONT.",
                self.sdk_augmont_number or self.name, line.id, status)
            return 'Diamond Booked'
        return word

    def _lgd_offline_line_type(self, line):
        """LGD / MELEE / NATURAL - the word the website files the stone under.

        NOT line.line_type. That field answers "single stone or parcel?" and
        has only two values, so a natural stone went out as "LGD" and the
        website filed it as lab grown - the same payload carries
        product.stoneType = "Natural", but lineType is what the website goes
        by. Odoo reads it the same way coming the other direction:
        lgd_stone_type_from_payload checks lineType first and maps "LGD" to
        lab_grown.

        The agreed mapping, as documented on
        sale.order.line._lgd_is_non_certified:

            LGD     -> LabGrown Certified
            MELEE   -> LabGrown Melle
            NATURAL -> Natural Certified

        A parcel stays MELEE whatever it is made of: Natural Melle has no
        lineType of its own yet, and MELEE is the only word the website has
        for a parcel.
        """
        if line.line_type == 'melee':
            return 'MELEE'
        if line.stone_type == 'natural':
            return 'NATURAL'
        return 'LGD'

    def _lgd_offline_payload(self):
        """Build the POST body for /odoo/order/offline.

        Carries an orderUniqueId per line, derived from the Odoo line id.
        The website's schema requires one and refuses the order without it.
        Odoo still stores whatever ids come back in the response, so if the
        website prefers to allocate its own it can simply return those and
        _lgd_store_order_unique_ids will record them instead.
        """
        self.ensure_one()
        sgst, cgst, igst = self._lgd_offline_tax_rates()
        partner = self.partner_id

        orders = []
        for line in self._lgd_offline_lines():
            orders.append({
                # Odoo's own line id, echoed back by the website so each
                # orderUniqueId lands on the line it belongs to. Product
                # identity is NOT sufficient: a non-certified order can carry
                # several lines of the same product, which then share a
                # certNumber and an lgdStockNum and cannot be told apart.
                'lineRef': line.id,
                'orderUniqueId': self._lgd_offline_unique_id(line),
                'lineType': self._lgd_offline_line_type(line),
                'status': self._lgd_offline_line_status(line),
                'quantity': int(line.product_uom_qty or 1),
                'productPrice': float(line.price_unit or 0.0),
                'invoiceAmount': float(line.price_total or 0.0),
                'orderNote': line.order_specific or '',
                'generalNotes': line.popup_item_notes or '',
                'customerReference': line.customer_reference_note or '',
                'defaultQcRequirements': line.popup_default_qc_requirements or '',
                'seller': self._lgd_offline_seller_block(line),
                'product': self._lgd_offline_product_block(line),
            })

        return {
            'orderSource': 'offline',
            'invoiceNumber': self.sdk_augmont_number or '',
            'invoiceDate': fields.Datetime.to_string(
                self.date_order or fields.Datetime.now()),
            'currency': self.currency_id.name or 'INR',
            'countryIsoCode': self._lgd_offline_country_code(partner, 'buyer'),
            'shippingCharges': 0,
            'sgst': sgst,
            'cgst': cgst,
            'igst': igst,
            'vat': 0,
            'buyer': {
                **self._lgd_offline_address(partner),
                'email': partner.email or '',
                'kyc': {
                    'type': 'GSTIN' if self.vat else 'EIN',
                    'number': self.vat or self.ein_number or '',
                },
            },
            'billingAddress': self._lgd_offline_address(
                self.partner_invoice_id or partner),
            'shippingAddress': self._lgd_offline_address(
                self.partner_shipping_id or partner),
            'orders': orders,
        }

    # ------------------------------------------------------------------
    # Response
    # ------------------------------------------------------------------
    def _lgd_store_order_unique_ids(self, response_data):
        """Write the website's per-line ids onto the matching order lines.

        Matched on identity, never on array position. Positional matching would
        look like it worked and would silently mis-assign the moment the
        website reorders, filters or merges a line; a mis-assigned order_number
        then routes a later status callback to the wrong stone.

        Three keys are accepted, in order:
          * lineRef - the Odoo line id we sent. The only one that is always
            correct, because it identifies the LINE rather than the product;
          * certNumber - the natural key for a certified stone;
          * lgdStockNum - our own LGD-OFF- series.
        The fallbacks exist because the website may not echo lineRef yet, but
        both of them identify a PRODUCT, and a non-certified order routinely
        carries several lines of the same product. Where that happens only the
        first such line can be matched, and the rest are left for lineRef.

        Never overwrites an id already stored, so a re-push cannot renumber
        lines the website has already been told about.
        """
        self.ensure_one()
        entries = (response_data or {}).get('data', {}).get('orders') or []
        lines = self._lgd_offline_lines()
        stored = 0

        for entry in entries:
            unique_id = entry.get('orderUniqueId')
            if not unique_id:
                continue

            details = entry.get('productDetails') or {}
            line_ref = entry.get('lineRef')
            certificate = details.get('certNumber')
            sku = details.get('lgdStockNum')

            line = self.env['sale.order.line']
            if line_ref:
                line = lines.filtered(lambda l: l.id == int(line_ref))[:1]
            # Product-identity fallbacks skip lines that are already matched,
            # so several lines sharing one product are filled in turn rather
            # than all collapsing onto the first.
            if not line and certificate:
                line = lines.filtered(
                    lambda l: l.certificate == certificate
                    and not l.order_number)[:1]
            if not line and sku:
                line = lines.filtered(
                    lambda l: l.product_template_id.lgd_stock_number == sku
                    and not l.order_number)[:1]

            if not line:
                if not line_ref and not certificate and not sku:
                    _logger.error(
                        "Offline push %s: response entry %s carries no "
                        "lineRef, certNumber or lgdStockNum, so it cannot be "
                        "matched to a line. Ask the website to echo the "
                        "lineRef we send.",
                        self.sdk_augmont_number, unique_id)
                else:
                    _logger.warning(
                        "Offline push %s: response referenced lineRef=%s "
                        "certNumber=%s lgdStockNum=%s, which matches no "
                        "unmatched line on this order. Skipped.",
                        self.sdk_augmont_number, line_ref, certificate, sku)
                continue

            if line.order_number:
                continue

            # The website owns this value and it must identify exactly one
            # line. Catch a collision as it arrives: left undetected it only
            # surfaces much later, as a status update applied to the wrong
            # stone, with nothing in the log to explain it.
            clash = self.env['sale.order.line'].sudo().search([
                ('order_number', '=', str(unique_id)),
                ('id', '!=', line.id),
            ], limit=1)
            if clash:
                _logger.error(
                    "Offline push %s: the website returned orderUniqueId %s "
                    "for line %s, but line %s (order %s) already holds it. "
                    "Status updates for this id cannot be routed. Not stored.",
                    self.sdk_augmont_number, unique_id, line.id, clash.id,
                    clash.order_id.name)
                continue

            line.order_number = str(unique_id)
            stored += 1

        if stored:
            _logger.info("Offline push %s: stored %s orderUniqueId(s).",
                         self.sdk_augmont_number, stored)
        return stored

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------
    def _lgd_offline_push_url(self):
        Param = self.env['ir.config_parameter'].sudo()
        base = (Param.get_param('base_augmont_url') or '').rstrip('/')
        if not base:
            return False
        path = (Param.get_param('lgd_offline_order_path')
                or '/api/v1/odoo/order/offline')
        return '%s%s' % (base, path if path.startswith('/') else '/' + path)

    def _lgd_needs_offline_push(self):
        """Skip orders the website already knows about in full.

        Covers a double-clicked Confirm and a re-confirm after cancellation:
        if every stone line already carries an orderUniqueId there is nothing
        to collect.
        """
        self.ensure_one()
        if self.order_source != 'offline':
            return False
        lines = self._lgd_offline_lines()
        if not lines:
            return False
        return not all(line.order_number for line in lines)

    def _lgd_push_offline_order(self):
        """POST this offline order to the website and store the ids it returns.

        Returns True on success. Never raises: this runs inside action_confirm,
        and a website outage must not stop the sales team booking an order. A
        failure queues an augmont.api.retry row for the existing cron instead.
        """
        self.ensure_one()
        if not self._lgd_needs_offline_push():
            return False

        url = self._lgd_offline_push_url()
        if not url:
            _logger.warning(
                "Offline push skipped for %s: base_augmont_url is not set.",
                self.sdk_augmont_number or self.name)
            return False

        # The push is specified to run at punch time, when every stone should
        # still be Diamond Booked. Anything else means the order moved on
        # before it was pushed - worth seeing, but not worth withholding the
        # order over.
        unexpected = self._lgd_offline_lines().filtered(
            lambda l: l.availability_status != self._LGD_OFFLINE_PUSH_STATUS)
        if unexpected:
            _logger.warning(
                "Offline push %s: %s of %s stone(s) are not '%s' at push "
                "time (%s). Sending them anyway.",
                self.sdk_augmont_number or self.name, len(unexpected),
                len(self._lgd_offline_lines()), self._LGD_OFFLINE_PUSH_STATUS,
                ', '.join(sorted(set(
                    l.availability_status or '(empty)' for l in unexpected))))

        self._lgd_warn_unusable_sellers()
        self._lgd_warn_missing_lab()

        payload = self._lgd_offline_payload()
        headers = {'Content-Type': 'application/json'}
        token = self.env['ir.config_parameter'].sudo().get_param(
            'lgd_offline_order_token')
        if token:
            headers['Authorization'] = 'Bearer %s' % token

        body = json.dumps(payload)
        _logger.info(
            "[OFFLINE PUSH] POST %s | auth=%s | %s line(s) | %d bytes",
            url, 'yes' if 'Authorization' in headers else 'NO',
            len(payload.get('orders') or []), len(body))

        log_api(OUT, "POST %s" % url, body, headers=headers, env=self.env)
        try:
            response = requests.post(
                url, headers=headers, data=body,
                timeout=(LGD_OFFLINE_PUSH_CONNECT_TIMEOUT,
                         LGD_OFFLINE_PUSH_TIMEOUT))
        except requests.exceptions.RequestException as error:
            # Never reached the server at all: DNS gone, tunnel down,
            # connection refused, or slower than the timeout.
            _logger.error(
                "[OFFLINE PUSH] %s: no response from %s - %s. Queued for "
                "retry.", self.sdk_augmont_number or self.name, url, error)
            self._lgd_queue_offline_retry(payload, headers, str(error), False)
            return False

        _logger.info(
            "[OFFLINE PUSH] <- HTTP %s from %s | body: %s",
            response.status_code, url, (response.text or '')[:600])

        if response.status_code != 200:
            _logger.error(
                "[OFFLINE PUSH] %s: website refused with HTTP %s. Queued for "
                "retry. %s",
                self.sdk_augmont_number or self.name, response.status_code,
                '401 usually means lgd_offline_order_token is wrong or unset'
                if response.status_code == 401 else
                '400 means the payload failed the website\'s validation - '
                'the body above names the fields'
                if response.status_code == 400 else '')
            self._lgd_queue_offline_retry(
                payload, headers, response.text, str(response.status_code))
            return False

        try:
            data = response.json()
        except ValueError:
            self._lgd_queue_offline_retry(
                payload, headers, 'Response was not JSON: %s' % response.text,
                str(response.status_code))
            return False

        self._lgd_store_order_unique_ids(data)
        if self.lgd_offline_push_error:
            self.sudo().lgd_offline_push_error = False
            try:
                self.message_post(
                    body=Markup("<p style='color:#2E7D32;'><b>%s</b></p>") % (
                        _("Sent to the website successfully on a later "
                          "attempt.")),
                    subject=_("Website push succeeded"))
            except Exception:
                _logger.exception(
                    "Could not post the push recovery to %s's chatter.",
                    self.name)
        return True

    _LGD_ERROR_EXCERPT = 500

    def _lgd_explain_push_error(self, error, status_code):
        headline, fields_hit = (error or '').strip(), []
        try:
            parsed = json.loads(error)
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            headline = (parsed.get('message')
                        or parsed.get('error')
                        or headline)
            for item in (parsed.get('errors') or [])[:12]:
                if isinstance(item, dict):
                    name = item.get('path') or item.get('param') or ''
                    why = item.get('msg') or item.get('message') or ''
                    fields_hit.append('%s: %s' % (name, why) if name else why)

        if len(headline) > self._LGD_ERROR_EXCERPT:
            headline = headline[:self._LGD_ERROR_EXCERPT] + ' …'

        # What the code usually means, so nobody has to go and look it up.
        hint = {
            '401': _("The website rejected our token. Check the "
                     "lgd_offline_order_token system parameter on THIS "
                     "database - it is stored per database, so setting it "
                     "elsewhere does not help."),
            '400': _("The website accepted our token but refused the "
                     "payload. The fields it named are listed above."),
            # Not an HTTP code — matched on the message below.
            'seller': _("The website matches sellers by email address and "
                        "has no seller registered under the one we sent. "
                        "Open the vendor in Contacts and set the email their "
                        "seller profile uses; the re-send sweep picks it "
                        "up on its next pass. A placeholder address will "
                        "always be refused."),
            '404': _("The endpoint was not found. Check base_augmont_url "
                     "and lgd_offline_order_path."),
            '500': _("The website hit an error of its own. Worth sending "
                     "this to them rather than changing anything here."),
        }
        # The seller lookup is the commonest 400 and has its own fix, so it
        # wins over the generic "check the fields above".
        if 'seller profile not found' in (error or '').lower():
            hint = hint['seller']
        else:
            hint = hint.get(str(status_code or ''))
        if not status_code:
            hint = _("The request never reached the website - DNS, a "
                     "firewall, or the host being down. Nothing was sent, so "
                     "nothing was half-created on their side.")
        return headline, fields_hit, hint

    def _lgd_post_push_failure(self, error, status_code, url):
        
        self.ensure_one()
        headline, fields_hit, hint = self._lgd_explain_push_error(
            error, status_code)

        rows = Markup('').join(
            Markup("<li>%s</li>") % f for f in fields_hit)
        body = Markup("<p style='color:#A8322B;'><b>%s</b></p><p>%s</p>") % (
            _("Could not send this order to the website (%s)")
            % (_("HTTP %s") % status_code if status_code
               else _("no response")),
            headline or _("No message returned."))
        if rows:
            body += Markup("<p>%s</p><ul>%s</ul>") % (
                _("Fields the website refused:"), rows)
        if hint:
            body += Markup("<p><i>%s</i></p>") % hint
        body += Markup("<p style='color:#76838F;font-size:90%%;'>%s</p>") % (
            _("Endpoint: %s — Odoo will keep trying on its own every 10 "
              "minutes. Fix the cause and the next attempt goes through; "
              "nothing needs to be pressed.") % (url or '(not configured)'))

        signature = '%s|%s' % (status_code or '', headline or '')
        if (self.lgd_offline_push_error or '') == signature:
            _logger.info(
                "Offline push %s: same failure as last time, chatter not "
                "posted again (%s).", self.sdk_augmont_number or self.name,
                headline or status_code)
            return
        self.sudo().lgd_offline_push_error = signature

        try:
            self.message_post(body=body, subject=_("Website push failed"))
        except Exception:
            # Never let a logging failure swallow the real error.
            _logger.exception(
                "Could not post the push failure to %s's chatter.", self.name)

    def _lgd_queue_offline_retry(self, payload, headers, error, status_code):
        """Hand a failed push to the existing augmont.api.retry cron."""
        self.ensure_one()
        _logger.warning("Offline push failed for %s (%s): %s",
                        self.sdk_augmont_number or self.name, status_code, error)
        self._lgd_post_push_failure(
            error, status_code, self._lgd_offline_push_url())
        self.env['augmont.api.retry'].sudo().create({
            'order_id': self.id,
            # Without this the retry cron would POST this offline-order body
            # to the status-update endpoint, which is its hardcoded default.
            'lgd_endpoint_url': self._lgd_offline_push_url(),
            'sdk_augmont_number': self.sdk_augmont_number,
            'payload_json': json.dumps(payload),
            'headers_json': json.dumps(headers),
            'error_message': error,
            'status_code': status_code or '',
            'state': 'pending',
            'updated_on': fields.Datetime.now(),
        })

    # ------------------------------------------------------------------
    # Automatic re-send
    # ------------------------------------------------------------------
    _LGD_OFFLINE_RESEND_DAYS = 7

    @api.model
    def _cron_lgd_push_offline_orders(self):
        """Re-send offline orders the website has not accepted yet.

        The push on confirm is the normal route, but it fires once. Anything
        that made the website refuse it - a stone with no Lab, a vendor whose
        email is not an address, the host being down - is corrected in Odoo
        minutes or hours later, and the order must then go on its own.

        The payload is rebuilt from the order on every attempt, which is the
        whole point: augmont.api.retry replays the JSON that failed, so it
        can never carry a correction. It also skips 400s entirely, treating
        them as permanent, which is right for a status transition the website
        will never accept and wrong for an order waiting on a data fix.

        Idempotent by invoiceNumber on the website's side, and guarded here by
        _lgd_needs_offline_push, so a stone that already has its id is never
        sent twice.
        """
        cutoff = fields.Datetime.subtract(
            fields.Datetime.now(), days=self._LGD_OFFLINE_RESEND_DAYS)
        candidates = self.search([
            ('order_source', '=', 'offline'),
            ('state', 'in', ('sale', 'done')),
            ('date_order', '>=', cutoff),
        ]).filtered(lambda o: o._lgd_needs_offline_push())

        if not candidates:
            return True

        _logger.info(
            "[OFFLINE PUSH] sweep: %s order(s) still unsent - %s",
            len(candidates),
            ', '.join(o.sdk_augmont_number or o.name for o in candidates))

        for order in candidates:
            try:
                order._lgd_push_offline_order()
            except Exception:
                # One bad order must not stop the sweep reaching the rest.
                _logger.exception(
                    "[OFFLINE PUSH] sweep: %s raised; continuing with the "
                    "others.", order.sdk_augmont_number or order.name)
                continue
            # A push that worked makes the queued rows pointless; left
            # pending they would be retried against the status endpoint.
            if not order._lgd_needs_offline_push():
                self.env['augmont.api.retry'].sudo().search([
                    ('order_id', '=', order.id),
                    ('state', '=', 'pending'),
                    ('lgd_endpoint_url', '!=', False),
                ]).write({'state': 'success'})
        return True

    # ------------------------------------------------------------------
    # Trigger
    # ------------------------------------------------------------------
    def action_confirm(self):
        result = super().action_confirm()
        for order in self:
            if order.order_source != 'offline':
                continue
            try:
                order._lgd_push_offline_order()
            except Exception:
                # Belt and braces. _lgd_push_offline_order already swallows
                # transport failures; this catches anything unforeseen so a
                # website problem can never block a confirmation.
                _logger.exception(
                    "Offline push raised unexpectedly for %s; order confirmed "
                    "regardless.", order.sdk_augmont_number or order.name)
        return result
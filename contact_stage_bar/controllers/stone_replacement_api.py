# -*- coding: utf-8 -*-

import json
import logging

from odoo.addons.contact_stage_bar.models.lgd_api_log import (
    IN, OUT, log_api)
from datetime import datetime

from odoo import http
from odoo.http import request, Response
from odoo.exceptions import UserError
from markupsafe import Markup

from .lgd_api_auth import lgd_check_api_token

_logger = logging.getLogger(__name__)

REPLACEABLE = ('not_available', 'qc_fail')

REPLACEABLE_LABELS = {'not_available': 'Not available', 'qc_fail': 'QC Fail'}


class ReplacementRefused(Exception):
    """A refusal the website can act on: a code it can switch on, and a
    sentence it can show the customer."""

    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _json(payload, status=200):
    return Response(json.dumps(payload, default=str),
                    content_type='application/json', status=status)


def _parse_dt(value):
    """The website sends ISO 8601 with a Z. Odoo stores naive UTC."""
    if not value:
        return False
    text = str(value).strip().replace('Z', '+00:00')
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        _logger.warning("[REPLACE] Unparseable requestedAt %r; leaving blank.",
                        value)
        return False
    return parsed.replace(tzinfo=None) if parsed.tzinfo is None else \
        parsed.astimezone(tz=None).replace(tzinfo=None)


def _as_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class StoneReplacementAPI(http.Controller):

    # ------------------------------------------------------------------
    # POST /api/lgd/order/stone/replace
    # ------------------------------------------------------------------
    @http.route('/api/lgd/order/stone/replace', type='http',
                auth='public', methods=['POST'], csrf=False)
    def replace_stone(self, **kwargs):
        """Replace one stone on an existing order, at the customer's request."""
        # ------------------------------------------------------------------
        # Auth
        # ------------------------------------------------------------------

        try:
            data = json.loads(request.httprequest.data)
            log_api(IN, "POST /api/lgd/order/stone/replace", data,
                    headers=dict(request.httprequest.headers),
                    env=request.env)
        except (json.JSONDecodeError, TypeError) as exc:
            return _json({'status': 'error', 'errorCode': 'INVALID_JSON',
                          'message': 'Invalid JSON: %s' % exc, 'code': 400}, 400)

        request_id = str(data.get('websiteReplacementId') or '').strip()
        # The identifiers, spelled out. A refusal here is almost always about
        # a field that was not sent or was sent empty, and guessing which one
        # from the error alone wastes a round trip with the website.
        details = ((data.get('replacementStone') or {})
                   .get('productDetails') or {})
        _logger.info(
            "📥 [WEBSITE→ODOO] Replace stone | request %s | invoice %s | "
            "original %s (cert %s) -> new %s (cert %s, stockNum %s, "
            "lgdStockNum %s, lab %s) | productDetails keys: %s",
            request_id or '(none)', data.get('invoiceNumber') or '(none)',
            (data.get('originalStone') or {}).get('orderUniqueId')
            or data.get('replacesOrderUniqueId') or '(none)',
            (data.get('originalStone') or {}).get('certNumber') or '(none)',
            (data.get('replacementStone') or {}).get('orderUniqueId')
            or data.get('replacedByOrderUniqueId') or '(none)',
            details.get('certNumber') or '(none)',
            details.get('stockNum') or '(none)',
            details.get('lgdStockNum') or '(none)',
            details.get('lab') or '(none)',
            ', '.join(sorted(details)) or '(none)')

        try:
            result = self._process(data, request_id)
        except ReplacementRefused as refusal:
            _logger.warning("❌ [REPLACE REFUSED] %s | %s | request %s",
                            refusal.code, refusal.message, request_id)
            return _json({'status': 'error', 'errorCode': refusal.code,
                          'message': refusal.message,
                          'code': refusal.status}, refusal.status)
        except UserError as exc:
            # The shared rules refused it. Their wording is already written
            # for a person to read, so it is passed through rather than
            # replaced with something vaguer.
            message = exc.args[0] if exc.args else str(exc)
            _logger.warning("❌ [REPLACE REJECTED] %s | request %s",
                            message, request_id)
            return _json({'status': 'error', 'errorCode': 'VALIDATION_FAILED',
                          'message': message, 'code': 422}, 422)
        except Exception as exc:
            request.env.cr.rollback()
            _logger.exception("💥 [REPLACE FAILED] request %s: %s",
                              request_id, exc)
            return _json({'status': 'error', 'errorCode': 'SERVER_ERROR',
                          'message': 'Server error: %s' % exc, 'code': 500}, 500)

        return _json(result, 200)

    # ------------------------------------------------------------------
    # The work
    # ------------------------------------------------------------------
    def _process(self, data, request_id):
        # ── Idempotency ────────────────────────────────────────────────
        # Checked before anything is created, so a retried POST - a timeout
        # on their side, a redelivered webhook - returns the first answer
        # instead of replacing the stone a second time.
        #
        # websiteReplacementId is optional: the website has no such id of its
        # own, and inventing one for our benefit would be a field they have
        # to store and keep unique forever. The replacement line's own order
        # id does the same job - it is unique on their side and there is
        # exactly one per replacement - so it stands in when none is sent.
        request_id = request_id or self._fallback_request_id(data)
        if not request_id:
            raise ReplacementRefused(
                'MISSING_FIELD',
                "Either websiteReplacementId or the replacement stone's "
                "order id is required, so a retried call can be recognised "
                "and not replace the stone twice.")
        replayed = request.env['sale.order.line'].sudo().search(
            [('lgd_replacement_request_id', '=', request_id)], limit=1)
        if replayed:
            _logger.info("🔁 [REPLACE REPLAY] request %s already processed as "
                         "line %s; returning the original result.",
                         request_id, replayed.id)
            return self._success_payload(
                replayed.replaces_line_id, replayed, replayed=True)

        original = self._find_original(data)
        new_spec = data.get('replacementStone') or {}
        details = new_spec.get('productDetails') or {}
        if not details:
            raise ReplacementRefused(
                'MISSING_FIELD',
                "replacementStone.productDetails is required: without the "
                "spec a stone Odoo has not seen before cannot be created.")

        new_order_number = self._new_order_number(data)
        if not new_order_number:
            raise ReplacementRefused(
                'MISSING_FIELD',
                "The replacement stone's order id is required, as "
                "replacementStone.orderUniqueId or replacedByOrderUniqueId. "
                "It must be a NEW order id, not the original's: two lines "
                "sharing one id cannot be told apart on later status updates.")
        if new_order_number == str(original.order_number or ''):
            raise ReplacementRefused(
                'ORDER_UNIQUE_ID_REUSED',
                "replacementStone.orderUniqueId (%s) is the original's id. "
                "The replacement stone needs its own." % new_order_number,
                409)
        clash = request.env['sale.order.line'].sudo().search(
            [('order_number', '=', new_order_number)], limit=1)
        if clash:
            raise ReplacementRefused(
                'ORDER_UNIQUE_ID_IN_USE',
                "orderUniqueId %s is already on order line %s (%s)."
                % (new_order_number, clash.id, clash.order_id.name), 409)

        original = original.sudo()
        vendor = self._resolve_vendor(new_spec.get('seller') or {})
        product = original._replacement_resolve_product(
            self._spec_vals(details))

        # Shared rules — the same two the wizard runs, in the same order.
        original._replacement_check_duplicate(
            product.certificate, exclude_line=original)
        original._replacement_check_hard_rules(product)

        gross_total = _as_float(details.get('finalPrice'))
        if gross_total <= 0.0:
            raise ReplacementRefused(
                'MISSING_VENDOR_PRICE',
                "productDetails.finalPrice (the vendor's price for the stone) "
                "is required and must be greater than zero.")
        price_per_carat = _as_float(details.get('pricePerCarat'))

        # The vendor must sell the product, or the PO and the line's vendor
        # cannot resolve. Same step the wizard takes on submit.
        if vendor.id not in product.seller_ids.mapped('partner_id').ids:
            request.env['product.supplierinfo'].sudo().create({
                'product_tmpl_id': product.id,
                'partner_id': vendor.id,
                'price': _as_float(details.get('finalPriceMargin')),
            })

        outcome = original._replacement_price_outcome(product, gross_total)

        # from_website_api stops Odoo echoing this straight back at them:
        # they are the ones who asked, and the outcome travels in the
        # response below. Without it the push fires mid-request and the
        # website sees a status change for a replacement it has not yet
        # been told succeeded.
        new_line = original.with_context(
            from_website_api=True,
        )._create_replacement(
            product, vendor, gross_total, outcome,
            price_per_carat=price_per_carat,
            new_order_number=new_order_number,
            provenance=self._provenance(data, request_id),
        )

        self._log_to_chatter(original, new_line, data, outcome)
        _logger.info(
            "✅ [REPLACE OK] %s | %s (%s) → %s (%s) | %s",
            original.order_id.name,
            original.order_number, original.certificate or '-',
            new_order_number, product.certificate or '-',
            'pending Sales' if outcome['needs_sales'] else 'completed')

        return self._success_payload(original, new_line)

    # ------------------------------------------------------------------
    # The two order ids
    # ------------------------------------------------------------------
    # The website names the pair on the replacement itself -
    # replacesOrderUniqueId / replacedByOrderUniqueId - which is the same
    # link under a different name. Both spellings are accepted so their
    # payload works as they described it, and a payload carrying both is
    # checked for agreement rather than quietly preferring one.
    @staticmethod
    def _pick_id(primary, alias, label):
        primary = str(primary or '').strip()
        alias = str(alias or '').strip()
        if primary and alias and primary != alias:
            raise ReplacementRefused(
                'ID_CONFLICT',
                "%s is given twice and the two disagree (%s and %s)."
                % (label, primary, alias), 409)
        return primary or alias

    def _original_order_number(self, data):
        return self._pick_id(
            (data.get('originalStone') or {}).get('orderUniqueId'),
            data.get('replacesOrderUniqueId'),
            "The original stone's order id")

    def _new_order_number(self, data):
        return self._pick_id(
            (data.get('replacementStone') or {}).get('orderUniqueId'),
            data.get('replacedByOrderUniqueId'),
            "The replacement stone's order id")

    def _fallback_request_id(self, data):
        """No websiteReplacementId? The replacement line's own order id is
        unique per replacement, so it identifies the call just as well.
        Prefixed so it can never collide with an id they later start
        sending of their own."""
        new_id = self._new_order_number(data)
        return 'order:%s' % new_id if new_id else ''

    # ------------------------------------------------------------------
    # Finding the stone being replaced
    # ------------------------------------------------------------------
    def _find_original(self, data):
        invoice = str(data.get('invoiceNumber') or '').strip()
        order_number = self._original_order_number(data)
        stone = data.get('originalStone') or {}
        if not invoice:
            raise ReplacementRefused('MISSING_FIELD',
                                     "invoiceNumber is required.")
        if not order_number:
            raise ReplacementRefused(
                'MISSING_FIELD',
                "The original stone's order id is required, as "
                "originalStone.orderUniqueId or replacesOrderUniqueId.")

        order = request.env['sale.order'].sudo().search(
            [('sdk_augmont_number', '=', invoice)], limit=1)
        if not order:
            raise ReplacementRefused(
                'ORDER_NOT_FOUND',
                "No order found for invoiceNumber %s." % invoice, 404)

        # Scoped to the order, so the same order id on an unrelated invoice
        # cannot be picked up by accident.
        matches = request.env['sale.order.line'].sudo().search([
            ('order_id', '=', order.id),
            ('order_number', '=', order_number),
        ])
        if not matches:
            raise ReplacementRefused(
                'LINE_NOT_FOUND',
                "Order %s has no line with orderUniqueId %s."
                % (invoice, order_number), 404)
        if len(matches) > 1:
            # A line already replaced is not a candidate for replacing again,
            # so it does not make the id ambiguous.
            live = matches.filtered(lambda l: not l.is_replaced)
            if len(live) != 1:
                raise ReplacementRefused(
                    'ORDER_AMBIGUOUS',
                    "orderUniqueId %s is on %s live lines of order %s. "
                    "Refusing to guess which stone to replace."
                    % (order_number, len(live), invoice), 409)
            matches = live

        line = matches
        sent_cert = str(stone.get('certNumber') or '').strip()
        held_cert = (line.certificate or '').strip()
        if sent_cert and held_cert and sent_cert.upper() != held_cert.upper():
            raise ReplacementRefused(
                'CERT_MISMATCH',
                "originalStone.certNumber %s does not match the stone on "
                "orderUniqueId %s (%s). Refusing to replace a different stone "
                "from the one you named." % (sent_cert, order_number,
                                             held_cert), 409)

        if line.availability_status not in REPLACEABLE:
            raise ReplacementRefused(
                'NOT_REPLACEABLE',
                "orderUniqueId %s is '%s'. Only a stone that is %s can be "
                "replaced." % (
                    order_number, line.availability_status or 'blank',
                    ' or '.join(REPLACEABLE_LABELS[s] for s in REPLACEABLE)),
                409)
        return line

    # ------------------------------------------------------------------
    # Payload -> Odoo
    # ------------------------------------------------------------------
    def _spec_vals(self, details):
        """productDetails as product.template values.
        Same keys as /api/lgd/order/cart_add writes, so a replacement
        stone and an ordered stone are stored identically - the website
        sends one shape of stone, not two. """
        country_id = False
        country_name = details.get('country')
        if country_name:
            country = request.env['res.country'].sudo().search(
                [('name', '=', str(country_name).title())], limit=1)
            country_id = country.id if country else False

        cert_number = (details.get('certNumber') or '').strip() or False
        vals = {
            'website_product_id': details.get('id'),
            'name': details.get('title') or '[NEW]',
            'type': 'consu',
            'is_storable': True,
            'certificate': cert_number,
            'barcode': cert_number,
            'certificate_type': details.get('certType'),
            'labs': details.get('lab'),
            'stock_number': details.get('stockNum'),
            'lgd_stock_number': details.get('lgdStockNum'),
            'shapes': details.get('shape'),
            'weight_carat': details.get('weight'),
            'color': details.get('color'),
            'clarity': details.get('clarity'),
            'cut': details.get('cut'),
            'polish': details.get('polish'),
            'symmetry': details.get('symmetry'),
            'fluorescence_intensity': details.get(
                'fluorescenceIntensity') or 'None',
            'fluorescence_color': details.get('fluorescenceColor') or 'None',
            'shade': details.get('shade'),
            'treatments': details.get('treatment') or 'None',
            'measurements': details.get('measurements'),
            'length': details.get('length'),
            'width': details.get('width'),
            'depth': details.get('height'),
            'eye_clean': details.get('eyeClean'),
            'country_id': country_id,
            'image_augmont': details.get('diamondImage'),
            'video_360': details.get('diamondVideo360'),
            'price_per_carat': details.get('pricePerCarat'),
            'list_price': details.get('pricePerCaratMargin'),
            'standard_price': details.get('finalPrice'),
            'final_price': details.get('finalPrice'),
            'final_price_margin': details.get('finalPriceMargin'),
            'sdk_is_code_enabled': True,
        }
        stone_type = (details.get('stoneType') or '').strip().lower()
        if stone_type in ('natural', 'lab_grown'):
            vals['stone_type'] = stone_type
        elif (details.get('lab') or '').strip().upper() == 'IGI' or \
                'LG' in str(cert_number or '').upper():
            # The website's catalogue is lab-grown; the origin check below
            # only bites when the two genuinely disagree, so a missing
            # stoneType is read as lab_grown rather than left blank (blank
            # skips the check entirely, which is the one outcome nobody wants).
            vals['stone_type'] = 'lab_grown'
        return {k: v for k, v in vals.items() if v not in (None, '')}

    def _resolve_vendor(self, seller):
        name = (seller.get('companyName') or '').strip() or ' '.join(
            part for part in ((seller.get('firstName') or '').strip(),
                              (seller.get('lastName') or '').strip()) if part)
        if not name:
            raise ReplacementRefused(
                'MISSING_VENDOR',
                "replacementStone.seller needs a companyName (or a first and "
                "last name): the vendor purchase order cannot be raised "
                "without one.")
        Partner = request.env['res.partner'].sudo()
        vendor = Partner.search(
            [('name', '=', name), ('supplier_rank', '>', 0)], limit=1)
        if not vendor:
            vendor = Partner.create({
                'name': name,
                'first_name': (seller.get('firstName') or '').strip(),
                'last_name': (seller.get('lastName') or '').strip(),
                'supplier_rank': 1,
                'phone': seller.get('mobileNumber'),
                'email': seller.get('email'),
            })
            _logger.info("[REPLACE] Created vendor %s", name)
        return vendor

    def _provenance(self, data, request_id):
        """Who asked and why — the part of a replacement only the website
        holds. Stored on the NEW line, alongside the link back to the old
        one, so the pair reads as one event."""
        reason = data.get('reason') or {}
        requester = data.get('requestedBy') or {}
        return {
            'source': 'website',
            'lgd_replacement_request_id': request_id,
            'lgd_replacement_reason_code': reason.get('code'),
            'lgd_replacement_reason_label': reason.get('label'),
            'lgd_replacement_reason_note': data.get('reasonComment'),
            'lgd_replacement_requested_by': requester.get('name'),
            'lgd_replacement_requested_email': requester.get('email'),
            'lgd_replacement_requested_role': requester.get('role'),
            'lgd_replacement_requested_at': _parse_dt(data.get('requestedAt')),
        }

    # ------------------------------------------------------------------
    # Answering
    # ------------------------------------------------------------------
    def _success_payload(self, original, new_line, replayed=False):
        state = ('pending_sales_confirmation'
                 if new_line.replacement_pending_sales else 'completed')
        return {
            'status': 'success',
            'message': ('Replacement already processed'
                        if replayed else 'Replacement created'),
            'data': {
                'odooOrder': new_line.order_id.name,
                'invoiceNumber': new_line.order_id.sdk_augmont_number or '',
                'websiteReplacementId': new_line.lgd_replacement_request_id or '',
                'originalOrderUniqueId': original.order_number or '',
                'originalCertNumber': original.certificate or '',
                'originalNewStatus': 'Replaced',
                'newOrderUniqueId': new_line.order_number or '',
                'newCertNumber': new_line.certificate or '',
                'newLineStatus': 'Diamond Booked',
                'replacementState': state,
                'customerPrice': new_line.price_unit,
                'updatedAt': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            },
        }

    def _log_to_chatter(self, original, new_line, data, outcome):
        reason = data.get('reason') or {}
        requester = data.get('requestedBy') or {}
        body = Markup(
            "<p><b>🔁 Stone replaced at the customer's request (LGD website)"
            "</b></p><ul>"
            "<li><b>Original:</b> %s (order %s, cert %s)</li>"
            "<li><b>Replacement:</b> %s (order %s, cert %s)</li>"
            "<li><b>Reason:</b> %s</li>"
            "<li><b>Comment:</b> %s</li>"
            "<li><b>Requested by:</b> %s (%s) — %s</li>"
            "<li><b>Request id:</b> %s</li>"
            "<li><b>Outcome:</b> %s</li>"
            "</ul>"
        ) % (
            original.product_template_id.display_name or '-',
            original.order_number or '-', original.certificate or '-',
            new_line.product_template_id.display_name or '-',
            new_line.order_number or '-', new_line.certificate or '-',
            reason.get('label') or reason.get('code') or '-',
            data.get('reasonComment') or '-',
            requester.get('name') or '-', requester.get('email') or '-',
            requester.get('role') or '-',
            new_line.lgd_replacement_request_id or '-',
            ('Pending Sales confirmation — the replacement is dearer than the '
             'agreed threshold' if outcome['needs_sales']
             else 'Completed'),
        )
        original.order_id.message_post(
            body=body, subject="Stone Replacement (LGD website)",
            message_type='notification')

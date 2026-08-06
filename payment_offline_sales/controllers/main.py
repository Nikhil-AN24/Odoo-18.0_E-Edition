import logging

import requests

from odoo import http
from odoo.addons.odoo_payment_payu import const as payu_consts
from odoo.addons.odoo_payment_payu import utils as payu_utils
from odoo.http import request

_logger = logging.getLogger(__name__)


PAYU_PROD_ACTION_URL = 'https://info.payu.in/_payment'
PAYU_TEST_ACTION_URL = 'https://test.payu.in/_payment'
STRIPE_SESSION_URL = 'https://api.stripe.com/v1/checkout/sessions'


class OfflinePaymentController(http.Controller):

    @http.route(
        '/payment/offline/payu/initiate/<string:token>',
        type='http', auth='public', methods=['GET'], csrf=False, website=False,
    )
    def payu_initiate(self, token, **kwargs):
        if not token:
            return self._render_error("Invalid payment link.")

        order = request.env['custom.sale.order'].sudo().search(
            [('payment_link_token', '=', token)], limit=1,
        )
        if not order:
            _logger.warning("[OfflinePayU] No order found for token=%s", token)
            return self._render_error(
                "This payment link is invalid or has expired."
            )

        tx = request.env['payment.transaction'].sudo().search(
            [('custom_sale_order_id', '=', order.id),
             ('provider_code', '=', 'payu'),
             ('state', 'in', ('draft', 'pending'))],
            order='id desc', limit=1,
        )
        if not tx:
            _logger.warning(
                "[OfflinePayU] No open PayU transaction for order=%s token=%s",
                order.name, token,
            )
            return self._render_error(
                "No open payment found for this order. It may already be paid."
            )

        provider = tx.provider_id
        if not provider or provider.state not in ('enabled', 'test'):
            return self._render_error(
                "The payment provider is not active. Please contact support."
            )
        if not provider.payu_merchant_key or not provider.payu_merchant_salt:
            return self._render_error(
                "Payment provider credentials are missing. Please contact support."
            )

        base_url = request.env['ir.config_parameter'].sudo().get_param('web.base.url') or ''
        return_url = f"{base_url.rstrip('/')}/payment/payu/return"
        partner = order.partner_id

        payload = {
            'key': provider.payu_merchant_key,
            'txnid': tx.reference,
            'amount': f"{tx.amount:.2f}",
            'productinfo': f"Offline Order {order.name}",
            'firstname': (partner.name or '').split(' ')[0] or 'Customer',
            'phone': partner.phone or partner.mobile or '',
            'email': partner.email or '',
            'surl': return_url,
            'furl': return_url,
            'udf1': 'payment',
            'udf2': '',
            'udf3': '',
            'udf4': '',
            'udf5': '',
            'udf6': '',
            'udf7': '',
            'udf8': '',
            'udf9': '',
            'udf10': '',
            'salt': provider.payu_merchant_salt,
        }
        payload['hash'] = payu_utils.generate_payu_hash(
            payload, payu_consts.PAYU_HASH_SEQUENCE['PAYMENT'],
        )
        payload.pop('salt', None)

        action_url = PAYU_TEST_ACTION_URL if provider.state == 'test' else PAYU_PROD_ACTION_URL

        _logger.info(
            "[OfflinePayU] Initiating: order=%s txnid=%s amount=%s provider_state=%s",
            order.name, tx.reference, payload['amount'], provider.state,
        )

        return request.render(
            'payment_offline_sales.payu_redirect_form',
            {'payu_url': action_url, 'payload': payload},
        )

    # ------------------------------------------------------------------ Stripe
    # NOTE: these landing pages do NOT yet verify the payment with Stripe or flip
    # the order status. They are the minimal valid success_url / cancel_url that
    # the Checkout Session API requires. Real payment verification + status flip
    # ("return setup") is a later phase.

    @http.route(
        '/payment/offline/stripe/return/<string:token>',
        type='http', auth='public', methods=['GET'], csrf=False, website=False,
    )
    def stripe_return(self, token, **kwargs):
        order = request.env['custom.sale.order'].sudo().search(
            [('payment_link_token', '=', token)], limit=1,
        )
        order_name = order.name if order else ''
        paid = False
        if order:
            tx = request.env['payment.transaction'].sudo().search(
                [('custom_sale_order_id', '=', order.id), ('provider_code', '=', 'stripe')],
                order='id desc', limit=1,
            )
            paid = self._stripe_confirm_paid(tx)
            if paid and tx.state != 'done':
                tx._set_done()  # triggers the _set_done override -> flips order lines
        _logger.info("[OfflineStripe] Return token=%s order=%s paid=%s", token, order_name, paid)
        return request.render(
            'payment_offline_sales.stripe_result',
            {'success': paid, 'order_name': order_name},
        )

    @http.route(
        '/payment/offline/stripe/cancel/<string:token>',
        type='http', auth='public', methods=['GET'], csrf=False, website=False,
    )
    def stripe_cancel(self, token, **kwargs):
        order = request.env['custom.sale.order'].sudo().search(
            [('payment_link_token', '=', token)], limit=1,
        )
        order_name = order.name if order else ''
        if order:
            tx = request.env['payment.transaction'].sudo().search(
                [('custom_sale_order_id', '=', order.id), ('provider_code', '=', 'stripe'),
                 ('state', 'in', ('draft', 'pending'))],
                order='id desc', limit=1,
            )
            if tx:
                try:
                    tx._set_canceled()
                except Exception:
                    _logger.exception("[OfflineStripe] Failed to cancel tx %s", tx.reference)
        _logger.info("[OfflineStripe] Cancel landing for token=%s order=%s", token, order_name)
        return request.render(
            'payment_offline_sales.stripe_result',
            {'success': False, 'order_name': order_name},
        )

    def _stripe_confirm_paid(self, tx):
        """Retrieve the Stripe Checkout Session and return True if it is paid.
        Server-side verification - never trust the redirect alone."""
        if not tx or not tx.provider_reference:
            return False
        secret = tx.provider_id.stripe_secret_key
        if not secret:
            return False
        try:
            resp = requests.get(
                f"{STRIPE_SESSION_URL}/{tx.provider_reference}",
                headers={'Authorization': f'Bearer {secret}'},
                timeout=30,
            )
            data = resp.json()
        except Exception:
            _logger.exception("[OfflineStripe] Failed to retrieve session for tx %s", tx.reference)
            return False
        if resp.status_code != 200:
            _logger.warning("[OfflineStripe] Session fetch %s for tx %s: %s",
                            resp.status_code, tx.reference, data)
            return False
        return data.get('payment_status') == 'paid'

    def _render_error(self, message):
        return request.render(
            'payment_offline_sales.payu_redirect_error', {'message': message},
        )

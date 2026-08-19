import logging
import uuid

import requests
from markupsafe import Markup

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

PAYU_SUPPORTED_CURRENCY = 'INR'
STRIPE_CHECKOUT_URL = 'https://api.stripe.com/v1/checkout/sessions'
STRIPE_API_TIMEOUT = 30


class AcceptPaymentWizard(models.TransientModel):
    _name = 'custom.sale.order.accept.payment.wizard'
    _description = 'Accept Payment Wizard (Bank Transfer / Online)'

    custom_sale_order_id = fields.Many2one(
        'custom.sale.order', string='Offline Order', required=True, ondelete='cascade',
    )
    partner_id = fields.Many2one(related='custom_sale_order_id.partner_id', readonly=True)
    amount_total = fields.Monetary(related='custom_sale_order_id.amount_total', readonly=True)
    currency_id = fields.Many2one(related='custom_sale_order_id.currency_id', readonly=True)

    payment_method = fields.Selection(
        [('bank_transfer', 'Bank Transfer'), ('online_payment', 'Online Payment')],
        string='Payment Method', required=True, default='bank_transfer',
    )

    utr_number = fields.Char(string='UTR Number')

    routed_provider = fields.Char(
        string='Routed Provider', compute='_compute_routed_provider',
        help='Auto-detected payment provider based on customer country / phone.',
    )

    def _compute_routed_provider(self):
        for rec in self:
            partner = rec.custom_sale_order_id.partner_id
            country = (partner.country_id.code or '').upper() if partner else ''
            phone = (partner.phone or partner.mobile or '').replace(' ', '') if partner else ''
            is_india = country == 'IN' or phone.startswith('+91') or phone.startswith('91')
            rec.routed_provider = 'PayU (India)' if is_india else 'Stripe (International)'

    def action_confirm(self):
        self.ensure_one()
        order = self.custom_sale_order_id

        if self.payment_method == 'bank_transfer':
            return self._process_bank_transfer(order)
        elif self.payment_method == 'online_payment':
            return self._process_online_payment(order)
        raise UserError(_("Please select a Payment Method."))

    def _process_bank_transfer(self, order):
        if not self.utr_number or not self.utr_number.strip():
            raise UserError(_("UTR Number is required for Bank Transfer."))

        utr = self.utr_number.strip()

        order.write({'utr_number': utr})

        payment_pending_lines = order.order_line.filtered(
            lambda l: l.availability_status == 'payment_pending'
        )
        if payment_pending_lines:
            payment_pending_lines.write({'availability_status': 'payment_completed'})
            _logger.info(
                "[AcceptPayment] Order %s — flipped %d payment_pending lines → payment_completed",
                order.name, len(payment_pending_lines),
            )
        else:
            _logger.info(
                "[AcceptPayment] Order %s — no payment_pending lines to flip "
                "(current sdk_augmont_status=%s)",
                order.name, order.sdk_augmont_status,
            )

        order.message_post(body=Markup(_(
            "Payment confirmed via <b>Bank Transfer</b>.<br/>UTR Number: <b>%s</b>"
        )) % utr)

        return {'type': 'ir.actions.act_window_close'}

    def _process_online_payment(self, order):
        partner = order.partner_id
        country = (partner.country_id.code or '').upper() if partner else ''
        phone = (partner.phone or partner.mobile or '').replace(' ', '') if partner else ''
        is_india = country == 'IN' or phone.startswith('+91') or phone.startswith('91')

        if is_india:
            return self._send_payu_link(order)
        return self._send_stripe_link(order)

    def _send_payu_link(self, order):
        partner = order.partner_id
        if not partner.email:
            raise UserError(_("Customer email is missing — required to send the payment link."))
        if not (partner.phone or partner.mobile):
            raise UserError(_("Customer phone is missing — required by PayU."))
        if order.amount_total <= 0:
            raise UserError(_("Order total is zero — nothing to charge."))

        currency = order.currency_id or self.env.company.currency_id
        if currency.name != PAYU_SUPPORTED_CURRENCY:
            raise UserError(_(
                "PayU only supports %s. This order's currency is %s — please change "
                "the pricelist before sending the payment link."
            ) % (PAYU_SUPPORTED_CURRENCY, currency.name))

        provider = self.env['payment.provider'].sudo().search(
            [('code', '=', 'payu'), ('state', 'in', ('enabled', 'test'))],
            limit=1,
        )
        if not provider:
            raise UserError(_(
                "PayU is not active. Go to Invoicing → Configuration → Payment "
                "Providers → PayU and set state to Test Mode or Enabled."
            ))
        if not provider.payu_merchant_key or not provider.payu_merchant_salt:
            raise UserError(_(
                "PayU merchant credentials are missing. Configure Merchant Key "
                "and Salt on the PayU payment provider record first."
            ))

        payment_method = provider.payment_method_ids.filtered(lambda pm: pm.active)[:1]
        if not payment_method:
            raise UserError(_(
                "PayU provider has no active payment methods. Open the PayU "
                "provider record, go to the Configuration tab, and enable at "
                "least one method (Card / UPI / Netbanking)."
            ))

        token = uuid.uuid4().hex
        reference = f"OFF-{order.name}-{token[:8]}"

        tx = self.env['payment.transaction'].sudo().create({
            'provider_id': provider.id,
            'payment_method_id': payment_method.id,
            'reference': reference,
            'amount': order.amount_total,
            'currency_id': currency.id,
            'partner_id': partner.id,
            'custom_sale_order_id': order.id,
        })

        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        if not base_url:
            raise UserError(_(
                "System parameter 'web.base.url' is not set. Configure it in "
                "Settings → Technical → System Parameters before sending payment links."
            ))

        url = f"{base_url.rstrip('/')}/payment/offline/payu/initiate/{token}"

        order.write({
            'payment_link_url': url,
            'payment_link_token': token,
            'payment_link_sent': True,
            'payment_link_sent_date': fields.Datetime.now(),
            'payment_provider_used': 'payu',
        })

        template = self.env.ref(
            'payment_offline_sales.mail_template_offline_payment_link',
            raise_if_not_found=False,
        )
        mail_sent = False
        if template:
            try:
                template.send_mail(order.id, force_send=False)
                mail_sent = True
            except Exception:
                _logger.exception(
                    "[AcceptPayment] Failed to send payment-link email for order %s",
                    order.name,
                )
        else:
            _logger.warning(
                "[AcceptPayment] mail_template_offline_payment_link not found — skipping email"
            )

        mail_status = "sent" if mail_sent else "NOT sent (check Outgoing Mail Servers / logs)"
        order.message_post(body=Markup(_(
            "PayU payment link generated.<br/>"
            "Reference: <b>%s</b><br/>"
            "Email to %s: <b>%s</b><br/>"
            "Link: <a href='%s' target='_blank'>%s</a>"
        )) % (reference, partner.email, mail_status, url, url))

        _logger.info(
            "[AcceptPayment] Order %s — PayU link created. txnid=%s amount=%s email=%s mail=%s",
            order.name, reference, order.amount_total, partner.email, mail_status,
        )

        return {'type': 'ir.actions.act_window_close'}

    def _send_stripe_link(self, order):
        partner = order.partner_id
        if not partner.email:
            raise UserError(_("Customer email is missing - required to send the payment link."))
        if order.amount_total <= 0:
            raise UserError(_("Order total is zero - nothing to charge."))

        currency = order.currency_id or self.env.company.currency_id

        provider = self.env['payment.provider'].sudo().search(
            [('code', '=', 'stripe'), ('state', 'in', ('enabled', 'test'))],
            limit=1,
        )
        if not provider:
            raise UserError(_(
                "Stripe is not active. Go to Invoicing -> Configuration -> Payment "
                "Providers -> Stripe and set state to Test Mode or Enabled."
            ))
        secret_key = provider.stripe_secret_key
        if not secret_key:
            raise UserError(_(
                "Stripe Secret Key is missing. Configure it on the Stripe payment "
                "provider record (Credentials tab) first."
            ))

        payment_method = provider.payment_method_ids.filtered(lambda pm: pm.active)[:1]
        if not payment_method:
            raise UserError(_(
                "Stripe provider has no active payment methods. Enable at least one "
                "on the Stripe provider's Configuration tab."
            ))

        token = uuid.uuid4().hex
        reference = f"OFF-{order.name}-{token[:8]}"

        tx = self.env['payment.transaction'].sudo().create({
            'provider_id': provider.id,
            'payment_method_id': payment_method.id,
            'reference': reference,
            'amount': order.amount_total,
            'currency_id': currency.id,
            'partner_id': partner.id,
            'custom_sale_order_id': order.id,
        })

        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        if not base_url:
            raise UserError(_(
                "System parameter 'web.base.url' is not set. Configure it in "
                "Settings -> Technical -> System Parameters before sending payment links."
            ))
        base_url = base_url.rstrip('/')
        success_url = f"{base_url}/payment/offline/stripe/return/{token}"
        cancel_url = f"{base_url}/payment/offline/stripe/cancel/{token}"

        # Single line item = order total. Stripe amounts are in the smallest currency
        # unit (e.g. cents); one line keeps the Stripe total == order total and avoids
        # per-line tax/rounding drift.
        unit_amount = int(round(order.amount_total * 100))
        payload = {
            'mode': 'payment',
            'success_url': success_url,
            'cancel_url': cancel_url,
            'customer_email': partner.email,
            'client_reference_id': reference,
            'metadata[custom_sale_order_id]': order.id,
            'metadata[token]': token,
            'line_items[0][quantity]': 1,
            'line_items[0][price_data][currency]': (currency.name or 'USD').lower(),
            'line_items[0][price_data][unit_amount]': unit_amount,
            'line_items[0][price_data][product_data][name]': f"Offline Order {order.name}",
        }

        try:
            resp = requests.post(
                STRIPE_CHECKOUT_URL,
                data=payload,
                headers={'Authorization': f'Bearer {secret_key}'},
                timeout=STRIPE_API_TIMEOUT,
            )
            session = resp.json()
        except Exception as e:
            _logger.exception("[AcceptPayment] Stripe API call failed for order %s", order.name)
            raise UserError(_("Could not reach Stripe: %s") % e)

        if resp.status_code != 200 or not session.get('url'):
            err = (session.get('error') or {}).get('message') or resp.text
            _logger.error("[AcceptPayment] Stripe session error for %s: %s", order.name, err)
            raise UserError(_("Stripe rejected the payment session: %s") % err)

        checkout_url = session['url']
        session_id = session.get('id')
        if session_id and 'provider_reference' in tx._fields:
            tx.sudo().write({'provider_reference': session_id})

        order.write({
            'payment_link_url': checkout_url,
            'payment_link_token': token,
            'payment_link_sent': True,
            'payment_link_sent_date': fields.Datetime.now(),
            'payment_provider_used': 'stripe',
        })

        template = self.env.ref(
            'payment_offline_sales.mail_template_offline_payment_link',
            raise_if_not_found=False,
        )
        mail_sent = False
        if template:
            try:
                template.send_mail(order.id, force_send=False)
                mail_sent = True
            except Exception:
                _logger.exception(
                    "[AcceptPayment] Failed to send Stripe payment-link email for order %s",
                    order.name,
                )

        mail_status = "sent" if mail_sent else "NOT sent (check Outgoing Mail Servers / logs)"
        order.message_post(body=Markup(_(
            "Stripe payment link generated.<br/>"
            "Reference: <b>%s</b><br/>"
            "Email to %s: <b>%s</b><br/>"
            "Link: <a href='%s' target='_blank'>%s</a>"
        )) % (reference, partner.email, mail_status, checkout_url, checkout_url))

        _logger.info(
            "[AcceptPayment] Order %s — Stripe link created. ref=%s amount=%s email=%s mail=%s",
            order.name, reference, order.amount_total, partner.email, mail_status,
        )

        return {'type': 'ir.actions.act_window_close'}

import logging

from odoo import fields, models

_logger = logging.getLogger(__name__)


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    custom_sale_order_id = fields.Many2one(
        'custom.sale.order',
        string='Custom Sale Order',
        index=True,
        ondelete='set null',
        copy=False,
    )

    def _set_done(self, *args, **kwargs):
        """Override: when a transaction linked to a custom.sale.order reaches 'done'
        (PayU webhook or our Stripe return), flip that order's payment_pending lines
        to payment_completed. Single hook for both providers."""
        res = super()._set_done(*args, **kwargs)
        for tx in self.filtered('custom_sale_order_id'):
            try:
                tx.custom_sale_order_id.sudo()._mark_payment_completed(reference=tx.reference)
            except Exception:
                _logger.exception(
                    "[OfflinePayment] Failed to mark order %s payment completed for tx %s",
                    tx.custom_sale_order_id.name, tx.reference,
                )
        return res

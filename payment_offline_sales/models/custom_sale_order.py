import logging

from markupsafe import Markup

from odoo import _, fields, models

_logger = logging.getLogger(__name__)


class CustomSaleOrder(models.Model):
    _inherit = 'custom.sale.order'

    utr_number = fields.Char(string='UTR Number', copy=False, tracking=True)

    payment_link_url = fields.Char(string='Payment Link URL', copy=False, readonly=True)
    payment_link_token = fields.Char(string='Payment Link Token', copy=False, index=True)
    payment_link_sent = fields.Boolean(string='Payment Link Sent', default=False, copy=False, tracking=True)
    payment_link_sent_date = fields.Datetime(string='Payment Link Sent At', readonly=True, copy=False)
    payment_provider_used = fields.Selection(
        [('payu', 'PayU'), ('stripe', 'Stripe')],
        string='Payment Provider Used', readonly=True, copy=False,
    )

    def action_accept_payment(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Accept Payment'),
            'res_model': 'custom.sale.order.accept.payment.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_custom_sale_order_id': self.id,
            },
        }

    def _mark_payment_completed(self, reference=None):
        """Flip payment_pending order lines to payment_completed (idempotent).

        Called when a linked payment.transaction reaches 'done' (PayU webhook /
        Stripe return). Safe to call more than once (e.g. PayU webhook + return
        double-fire): only flips when payment_pending lines still exist, and only
        posts chatter when it actually flips. Order status recomputes via the
        existing line cascade.
        """
        for order in self:
            pending = order.order_line.filtered(
                lambda l: l.availability_status == 'payment_pending'
            )
            if not pending:
                _logger.info(
                    "[OfflinePayment] Order %s has no payment_pending lines - "
                    "skipping flip (ref=%s)", order.name, reference,
                )
                continue
            pending.write({'availability_status': 'payment_completed'})
            ref_txt = Markup("<br/>Reference: <b>%s</b>") % reference if reference else Markup("")
            order.message_post(body=Markup(
                "Payment <b>completed</b> - %d line(s) marked Payment Completed.%s"
            ) % (len(pending), ref_txt))
            _logger.info(
                "[OfflinePayment] Order %s - flipped %d payment_pending lines -> "
                "payment_completed (ref=%s)", order.name, len(pending), reference,
            )

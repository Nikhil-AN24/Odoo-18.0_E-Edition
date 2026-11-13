# -*- coding: utf-8 -*-
"""A buyer becomes Converted as soon as an order is placed for them."""
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)
LGD_ORDER_CONVERSION_CTX = 'lgd_order_conversion'


class ResPartner(models.Model):
    _inherit = 'res.partner'

    def _lgd_mark_converted_by_order(self, order=None):
        """Move these accounts to Converted, from whatever stage they are in."""
        converted = self.env.ref('contact_stage_bar.converted_stage',
                                 raise_if_not_found=False)
        if not converted:
            _logger.warning("No Converted stage in this database; buyers "
                            "were not moved after an order.")
            return
        todo = self.sudo().filtered(lambda p: p.stage_id != converted)
        if not todo:
            return
        for partner in todo:
            _logger.info("Partner %s: %s -> Converted (order %s placed).",
                         partner.id, partner.stage_id.name or 'none',
                         order.name if order else '?')
        todo.with_context(**{LGD_ORDER_CONVERSION_CTX: True}).write(
            {'stage_id': converted.id})


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def _lgd_convert_buyers(self):
        for order in self:
            if order.state == 'cancel' or not order.partner_id:
                continue
            order.partner_id.commercial_partner_id \
                ._lgd_mark_converted_by_order(order)

    @api.model_create_multi
    def create(self, vals_list):
        orders = super().create(vals_list)
        orders.filtered('is_website')._lgd_convert_buyers()
        return orders

    def _action_confirm(self):
        res = super()._action_confirm()
        self._lgd_convert_buyers()
        return res

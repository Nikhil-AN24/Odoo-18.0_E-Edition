# -*- coding: utf-8 -*-
from odoo import api, models


class CustomSaleOrder(models.Model):
    _inherit = 'custom.sale.order'

    def _amplitude_props(self):
        self.ensure_one()
        return {
            'partner': self.partner_id.display_name,
            'state': self.state,
            'sale_state': self.sale_state,
            'sdk_augmont_status': self.sdk_augmont_status,
            'amount_total': self.amount_total,
            'line_count': len(self.order_line),
        }

    def _amplitude_track(self, event_type):
        client = self.env['amplitude.client']
        for rec in self:
            client.track_business_event(rec, event_type, rec._amplitude_props())

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._amplitude_track('Offline Order Created')
        return records

    def action_quotation_send(self):
        res = super().action_quotation_send()
        self._amplitude_track('Offline Order Quotation Sent')
        return res

    def action_confirm_offline_order(self):
        res = super().action_confirm_offline_order()
        self._amplitude_track('Offline Order Confirmed')
        return res

    def action_confirm(self):
        res = super().action_confirm()
        self._amplitude_track('Offline Order Confirmed')
        return res

    def action_cancel_offline_order(self):
        res = super().action_cancel_offline_order()
        self._amplitude_track('Offline Order Cancelled')
        return res

    def action_cancel(self):
        res = super().action_cancel()
        self._amplitude_track('Offline Order Cancelled')
        return res

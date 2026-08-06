# -*- coding: utf-8 -*-
from odoo import api, models


class CustomerRfq(models.Model):
    _inherit = 'customer.rfq'

    def _amplitude_props(self):
        self.ensure_one()
        return {
            'partner': self.partner_id.display_name,
            'state': self.state,
            'availability_status': self.availability_status,
            'carat': self.carat,
            'color': self.color,
            'clarity': self.clarity,
            'price': self.price,
            'total_price': self.total_price,
        }

    def _amplitude_track(self, event_type):
        client = self.env['amplitude.client']
        for rec in self:
            client.track_business_event(rec, event_type, rec._amplitude_props())

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._amplitude_track('RFQ Created')
        return records

    def action_send_to_procurement(self):
        res = super().action_send_to_procurement()
        self._amplitude_track('RFQ Sent to Procurement')
        return res

    def action_send_back_to_sales(self):
        res = super().action_send_back_to_sales()
        self._amplitude_track('RFQ Priced (Back to Sales)')
        return res

    def action_create_offline_order(self):
        res = super().action_create_offline_order()
        self._amplitude_track('RFQ Converted to Offline Order')
        return res

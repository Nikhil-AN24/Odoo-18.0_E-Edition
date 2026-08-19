# -*- coding: utf-8 -*-
from odoo import api, models


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    def _amplitude_props(self):
        self.ensure_one()
        return {
            'vendor': self.partner_id.display_name,
            'state': self.state,
            'location': self.location,
            'order_number': self.order_number,
            'amount_total': self.amount_total,
        }

    def _amplitude_track(self, event_type):
        client = self.env['amplitude.client']
        for rec in self:
            client.track_business_event(rec, event_type, rec._amplitude_props())

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._amplitude_track('Purchase Order Created')
        return records

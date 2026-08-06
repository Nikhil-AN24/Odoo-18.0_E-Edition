# -*- coding: utf-8 -*-
from odoo import models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def _amplitude_props(self):
        self.ensure_one()
        return {
            'partner': self.partner_id.display_name,
            'state': self.state,
            'sdk_augmont_status': self.sdk_augmont_status,
            'sdk_augmont_number': self.sdk_augmont_number,
            'location': self.location,
            'amount_total': self.amount_total,
            'is_website': self.is_website,
        }

    def _amplitude_track(self, event_type):
        client = self.env['amplitude.client']
        for rec in self:
            client.track_business_event(rec, event_type, rec._amplitude_props())

    def action_confirm(self):
        res = super().action_confirm()
        self._amplitude_track('Sale Order Confirmed')
        return res

    def action_cancel(self):
        res = super().action_cancel()
        self._amplitude_track('Sale Order Cancelled')
        return res

    def action_mark_not_available(self):
        res = super().action_mark_not_available()
        self._amplitude_track('Sale Order Marked Not Available')
        return res


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    def write(self, vals):
        track = 'availability_status' in vals
        old = {}
        if track:
            old = {rec.id: rec.availability_status for rec in self}
        res = super().write(vals)
        if track:
            client = self.env['amplitude.client']
            for rec in self:
                if old.get(rec.id) == rec.availability_status:
                    continue
                client.track_business_event(rec, 'Line Availability Changed', {
                    'order': rec.order_id.name,
                    'sdk_augmont_number': rec.order_id.sdk_augmont_number,
                    'product': rec.product_id.display_name,
                    'old_status': old.get(rec.id),
                    'new_status': rec.availability_status,
                })
        return res

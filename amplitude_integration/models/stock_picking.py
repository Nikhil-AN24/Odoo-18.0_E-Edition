# -*- coding: utf-8 -*-
from odoo import models


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def _amplitude_props(self):
        self.ensure_one()
        return {
            'picking': self.name,
            'state': self.state,
            'location': self.location,
            'partner': self.partner_id.display_name,
            'tracking_number': self.tracking_number,
        }

    def _amplitude_track(self, event_type):
        client = self.env['amplitude.client']
        for rec in self:
            client.track_business_event(rec, event_type, rec._amplitude_props())

    def action_pack(self):
        res = super().action_pack()
        self._amplitude_track('Picking Packed')
        return res

    def action_delivered(self):
        res = super().action_delivered()
        self._amplitude_track('Delivery Completed')
        return res

    def action_order_completed(self):
        res = super().action_order_completed()
        self._amplitude_track('Order Completed')
        return res

    def button_validate(self):
        res = super().button_validate()
        # Only count an actually-validated picking, not a wizard popup action.
        for rec in self:
            if rec.state == 'done':
                rec._amplitude_track('Picking Validated')
        return res


class StockMove(models.Model):
    _inherit = 'stock.move'

    def _amplitude_track(self, event_type):
        client = self.env['amplitude.client']
        for rec in self:
            client.track_business_event(rec, event_type, {
                'product': rec.product_id.display_name,
                'picking': rec.picking_id.name,
                'qc_status': rec.qc_status,
            })

    def action_pass(self):
        res = super().action_pass()
        self._amplitude_track('QC Pass')
        return res

    def action_fail(self):
        res = super().action_fail()
        self._amplitude_track('QC Fail')
        return res

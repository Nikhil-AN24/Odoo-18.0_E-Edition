# -*- coding: utf-8 -*-
from odoo import api, fields, models


class CustomerRfq(models.Model):
    _inherit = 'customer.rfq'

    # Offline orders created from this RFQ. Defined here (not in contact_stage_bar)
    # because custom_sale_order loads after contact_stage_bar, so the
    # custom.sale.order comodel is available.
    offline_order_ids = fields.One2many(
        'custom.sale.order', 'customer_rfq_id', string='Offline Orders')
    offline_order_count = fields.Integer(
        string='Offline Order Count', compute='_compute_offline_order_count')

    @api.depends('offline_order_ids')
    def _compute_offline_order_count(self):
        for rec in self:
            rec.offline_order_count = len(rec.offline_order_ids)

    def action_view_offline_orders(self):
        self.ensure_one()
        action = {
            'type': 'ir.actions.act_window',
            'name': 'Offline Orders',
            'res_model': 'custom.sale.order',
            'context': {'create': False},
        }
        if len(self.offline_order_ids) == 1:
            action.update({
                'view_mode': 'form',
                'res_id': self.offline_order_ids.id,
            })
        else:
            action.update({
                'view_mode': 'list,form',
                'domain': [('customer_rfq_id', '=', self.id)],
            })
        return action

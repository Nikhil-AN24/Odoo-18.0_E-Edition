# -*- coding: utf-8 -*-
from odoo import models

_STAGE_FIELDS = ('stage_id', 'gst_stages', 'ein_status')


class ResPartner(models.Model):
    _inherit = 'res.partner'

    def write(self, vals):
        tracked = [f for f in _STAGE_FIELDS if f in vals]
        old = {}
        if tracked:
            old = {
                rec.id: {f: rec[f] for f in tracked}
                for rec in self
            }
        res = super().write(vals)
        if tracked:
            client = self.env['amplitude.client']
            for rec in self:
                changed = {
                    f: rec[f] for f in tracked
                    if old.get(rec.id, {}).get(f) != rec[f]
                }
                if not changed:
                    continue
                props = {
                    'partner': rec.display_name,
                    'stage': rec.stage_id.name if rec.stage_id else '',
                    'gst_stages': rec.gst_stages,
                    'ein_status': rec.ein_status,
                    'changed_fields': ', '.join(changed.keys()),
                }
                client.track_business_event(rec, 'Vendor Stage Changed', props)
        return res

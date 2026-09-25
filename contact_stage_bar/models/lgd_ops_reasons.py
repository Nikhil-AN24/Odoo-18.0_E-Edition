from odoo import fields, models

class LgdInwardRejectReason(models.Model):
    _name = 'lgd.inward.reject.reason'
    _description = 'Logistics Inward Reject Reason'
    _order = 'sequence, name'

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

class LgdQcFailReason(models.Model):
    _name = 'lgd.qc.fail.reason'
    _description = 'QC Fail Reason'
    _order = 'sequence, name'

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

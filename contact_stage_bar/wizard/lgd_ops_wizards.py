from odoo import _, api, fields, models
from odoo.exceptions import UserError


class LgdOpsWizardMixin(models.AbstractModel):
    _name = 'lgd.ops.wizard.mixin'
    _description = 'Logistics & QC wizard base'

    note = fields.Text(string='Note')

    @api.model
    def _lgd_default_lines(self):
        if self.env.context.get('active_model') != 'purchase.order.line':
            return self.env['purchase.order.line']
        return self.env['purchase.order.line'].browse(
            self.env.context.get('active_ids', []))

    def _lgd_lines(self):
        self.ensure_one()
        if not self.line_ids:
            raise UserError(_("Select at least one stone first."))
        return self.line_ids


class LgdInwardRejectWizard(models.TransientModel):
    _name = 'lgd.inward.reject.wizard'
    _inherit = 'lgd.ops.wizard.mixin'
    _description = 'Reject Stone at Inward'

    line_ids = fields.Many2many(
        'purchase.order.line', string='Stones',
        default=lambda self: self._lgd_default_lines())
    reason_id = fields.Many2one(
        'lgd.inward.reject.reason', string='Reason', required=True)

    def action_reject(self):
        self.ensure_one()
        self._lgd_lines()._lgd_action_reject(self.reason_id, self.note)
        return {'type': 'ir.actions.act_window_close'}


class LgdQcFailWizard(models.TransientModel):
    _name = 'lgd.qc.fail.wizard'
    _inherit = 'lgd.ops.wizard.mixin'
    _description = 'Fail Stone at QC'

    line_ids = fields.Many2many(
        'purchase.order.line', string='Stones',
        default=lambda self: self._lgd_default_lines())
    reason_id = fields.Many2one(
        'lgd.qc.fail.reason', string='Reason', required=True)

    def action_fail(self):
        self.ensure_one()
        self._lgd_lines()._lgd_action_fail(self.reason_id, self.note)
        return {'type': 'ir.actions.act_window_close'}


class LgdVendorReturnWizard(models.TransientModel):
    _name = 'lgd.vendor.return.wizard'
    _inherit = 'lgd.ops.wizard.mixin'
    _description = 'Mark Stone Returned to Vendor'

    line_ids = fields.Many2many(
        'purchase.order.line', string='Stones',
        default=lambda self: self._lgd_default_lines())
    method = fields.Selection([
        ('vendor_collected', 'Vendor collected'),
        ('we_delivered', 'We delivered'),
    ], string='How', required=True)
    returned_by_id = fields.Many2one(
        'res.users', string='Handled By', default=lambda self: self.env.user)
    proof = fields.Binary(string='Signed Slip')
    proof_filename = fields.Char(string='File Name')

    def action_mark_returned(self):
        self.ensure_one()
        lines = self._lgd_lines()
        attachment = self.env['ir.attachment']
        if self.proof:
            attachment = self.env['ir.attachment'].create({
                'name': self.proof_filename or _('Return proof'),
                'datas': self.proof,
                'res_model': 'purchase.order',
                'res_id': lines[:1].order_id.id,
            })
        lines._lgd_action_mark_returned(
            self.method, self.returned_by_id, self.note, attachment)
        return {'type': 'ir.actions.act_window_close'}

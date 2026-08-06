from odoo import models, fields, api
class ResUsers(models.Model):
    _inherit = 'res.users'

    show_reports_app = fields.Boolean(
        string='Show Reports App',
        compute='_compute_show_reports_app',
        inverse='_inverse_show_reports_app',
        help="Check this to grant access to the Reports module."
    )

    @api.depends('groups_id')
    def _compute_show_reports_app(self):
        group_id = self.env.ref('employee_reports.group_reports_user', raise_if_not_found=False)
        for user in self:
            if group_id and group_id in user.groups_id:
                user.show_reports_app = True
            else:
                user.show_reports_app = False

    def _inverse_show_reports_app(self):
        group_id = self.env.ref('employee_reports.group_reports_user', raise_if_not_found=False)
        if not group_id:
            return
        for user in self:
            if user.show_reports_app:
                user.sudo().write({'groups_id': [(4, group_id.id)]})
            else:
                user.sudo().write({'groups_id': [(3, group_id.id)]})

    def action_download_data(self):
        wizard = self.env['report.download.wizard'].create({
            'source_model': 'res.users',
        })

        return {
            'type': 'ir.actions.act_window',
            'name': 'Download Report',
            'res_model': 'report.download.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'view_id': self.env.ref(
                'employee_reports.view_report_download_wizard_form'
            ).id,
            'target': 'new',
        }
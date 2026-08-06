from odoo import models, fields, api

class ResPartner(models.Model):
    _inherit = 'res.partner'

    def action_download_data(self):
        report_type = self.env.context.get('report_type', 'assigned')

        wizard = self.env['report.download.wizard'].create({
            'source_model': 'res.partner',
            'report_type':  report_type,
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

from odoo import models, fields, api
import pytz


class CustomerRfq(models.Model):
    _inherit = 'customer.rfq'

    #  Computed Fields for Reporting                                     #
    sent_to_procurement_dt = fields.Datetime(
        string='Sent to Procurement',
        compute='_compute_stage_datetimes',
        store=True,
    )
    sent_back_to_sales_dt = fields.Datetime(
        string='Sent back to Sales',
        compute='_compute_stage_datetimes',
        store=True,
    )
    time_difference = fields.Char(
        string='Time Difference',
        compute='_compute_stage_datetimes',
        store=True,
    )

    @api.depends('state', 'message_ids')
    def _compute_stage_datetimes(self):
        for rec in self:
            proc_track = self.env['mail.tracking.value'].search([
                ('mail_message_id.model', '=', 'customer.rfq'),
                ('mail_message_id.res_id', '=', rec._origin.id or rec.id),
                ('new_value_char', 'ilike', 'Sent to Procurement')
            ], order='mail_message_id desc', limit=1)
            proc_dt = proc_track.mail_message_id.date if proc_track else False

            sales_track = self.env['mail.tracking.value'].search([
                ('mail_message_id.model', '=', 'customer.rfq'),
                ('mail_message_id.res_id', '=', rec._origin.id or rec.id),
                ('new_value_char', 'ilike', 'Sent back to Sales')
            ], order='mail_message_id desc', limit=1)
            sales_dt = sales_track.mail_message_id.date if sales_track else False

            rec.sent_to_procurement_dt = proc_dt
            rec.sent_back_to_sales_dt = sales_dt

            if proc_dt and sales_dt and sales_dt > proc_dt:
                diff = sales_dt - proc_dt
                days = diff.days
                seconds = diff.seconds
                hours = seconds // 3600
                minutes = (seconds % 3600) // 60
                
                parts = []
                if days > 0:
                    parts.append(f"{days} Days")
                if hours > 0:
                    parts.append(f"{hours} Hrs")
                if minutes > 0 or (days == 0 and hours == 0):
                    parts.append(f"{minutes} Mins")
                
                rec.time_difference = ", ".join(parts)
            else:
                rec.time_difference = ""

    #  Helper — convert UTC datetime to user's local timezone             #
    def _utc_to_user_tz(self, utc_dt):
        """
        Converts a UTC-naive datetime (as stored in the DB) to the
        current user's local timezone and returns a formatted string.
        Ensures CSV timestamps match what is shown in the chatterbox.
        """
        if not utc_dt:
            return ''
        user_tz_name = self.env.user.tz or 'UTC'
        try:
            user_tz = pytz.timezone(user_tz_name)
        except pytz.UnknownTimeZoneError:
            user_tz = pytz.utc
        utc_aware = pytz.utc.localize(utc_dt)
        local_dt  = utc_aware.astimezone(user_tz)
        return local_dt.strftime('%Y-%m-%d %H:%M:%S')

    #  Helper — extract chatter tracking datetimes                        #
    def _get_stage_datetime(self, stage_name):
        """
        Searches the chatter for the latest datetime at which this
        record's stage was set to `stage_name`, converted to user's tz.
        Uses mail_message_id (correct Odoo 18 field on mail.tracking.value).
        Returns 'YYYY-MM-DD HH:MM:SS' in user's timezone, or ''.
        """
        self.ensure_one()

        messages = self.env['mail.message'].search([
            ('model',  '=', 'customer.rfq'),
            ('res_id', '=', self.id),
        ])
        if not messages:
            return ''

        tracking = self.env['mail.tracking.value'].search([
            ('mail_message_id', 'in', messages.ids),
            ('new_value_char',  'ilike', stage_name),
        ], order='mail_message_id desc', limit=1)

        if not tracking:
            return ''

        return self._utc_to_user_tz(tracking.mail_message_id.date)

    #  Download action — opens wizard with date range selection           #
    def action_download_data(self):
        """
        Opens the Report Download Wizard so the user can select a
        date range (Start Date / End Date) before downloading the CSV.
        """
        wizard = self.env['report.download.wizard'].create({
            'source_model': 'customer.rfq',
            'report_type':  'procurement',
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
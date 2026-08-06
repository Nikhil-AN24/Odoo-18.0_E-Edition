from odoo import models, fields, api
from odoo.exceptions import ValidationError
from datetime import date, datetime
import base64
import csv
import io
import pytz

class ReportDownloadWizard(models.TransientModel):
    _name = 'report.download.wizard'
    _description = 'Report Download Wizard'

    date_from = fields.Date(
        string='Start Date',
        required=True,
        default=lambda self: date.today().replace(day=1),
    )
    date_to = fields.Date(
        string='End Date',
        required=True,
        default=fields.Date.today,
    )

    source_model = fields.Char(string='Source Model')

    report_type = fields.Selection(
        selection=[
            ('assigned',     'Assigned'),
            ('not_assigned', 'Not Assigned'),
            ('procurement',  'Procurement'),
        ],
        string='Report Type',
        default='assigned',
    )

    #  Validation     #
    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        for rec in self:
            if rec.date_from and rec.date_to and rec.date_from > rec.date_to:
                raise ValidationError("Start Date must be on or before End Date.")

    #  Helpers       #
    def _get_attachment(self, csv_bytes, filename):
        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'type': 'binary',
            'datas': base64.b64encode(csv_bytes).decode('utf-8'),
            'mimetype': 'text/csv',
        })
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/%d?download=true' % attachment.id,
            'target': 'self',
        }

    def _local_date_to_utc_range(self):
        """
        Converts user-selected date_from / date_to (local timezone) to
        UTC datetime boundaries for use in Odoo domain filters.
        date_from → 00:00:00 user tz → UTC
        date_to   → 23:59:59 user tz → UTC
        """
        user_tz_name = self.env.user.tz or 'UTC'
        try:
            user_tz = pytz.timezone(user_tz_name)
        except pytz.UnknownTimeZoneError:
            user_tz = pytz.utc

        dt_from_utc = user_tz.localize(
            datetime(self.date_from.year, self.date_from.month,
                     self.date_from.day, 0, 0, 0)
        ).astimezone(pytz.utc).replace(tzinfo=None)

        dt_to_utc = user_tz.localize(
            datetime(self.date_to.year, self.date_to.month,
                     self.date_to.day, 23, 59, 59)
        ).astimezone(pytz.utc).replace(tzinfo=None)

        return dt_from_utc, dt_to_utc

    def _utc_to_user_tz(self, utc_dt):
        """
        Converts a UTC-naive datetime to the user's local timezone.
        Returns 'YYYY-MM-DD HH:MM:SS' string matching the chatterbox display.
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

    def _get_stage_datetime(self, rfq, stage_name):
        """
        Returns the latest datetime (user tz) when rfq's stage was set
        to stage_name, by reading mail.tracking.value via mail_message_id.
        """
        messages = self.env['mail.message'].sudo().search([
            ('model',  '=', 'customer.rfq'),
            ('res_id', '=', rfq.id),
        ])
        if not messages:
            return ''
        tracking = self.env['mail.tracking.value'].sudo().search([
            ('mail_message_id', 'in', messages.ids),
            ('new_value_char',  'ilike', stage_name),
        ], order='mail_message_id desc', limit=1)
        if not tracking:
            return ''
        return self._utc_to_user_tz(tracking.mail_message_id.date)


    def action_confirm(self):
        if self.source_model == 'sale.order':
            return self._export_sale_orders()
        if self.source_model == 'res.partner':
            return self._export_res_partners()
        if self.source_model == 'customer.rfq':
            return self._export_customer_rfq()
        return self._export_res_users()


    def _export_sale_orders(self):
        dt_from_utc, dt_to_utc = self._local_date_to_utc_range()

        if self.report_type == 'not_assigned':
            type_domain = [('user_id', '=', False)]
            label = 'Not_Assigned'
        else:
            type_domain = [('user_id', '!=', False)]
            label = 'Assigned'

        domain = type_domain + [
            ('date_order', '>=', fields.Datetime.to_string(dt_from_utc)),
            ('date_order', '<=', fields.Datetime.to_string(dt_to_utc)),
             # Requirement: exclude Draft Orders from the exported dataset
            ('state', '!=', 'draft'),
        ]
        records = self.env['sale.order'].search(domain, order='date_order desc')

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            'Invoice Number',
            'Order Reference',
            'Customer',
            'Salesperson',
            'Order Date',
            'Order Status',
        ])
        for order in records:
            writer.writerow([
                order.sdk_augmont_number or '',
                order.name               or '',
                order.partner_id.name    if order.partner_id else '',
                order.user_id.name       if order.user_id    else '',
                str(order.date_order)    if order.date_order else '',
                dict(order._fields['state'].selection).get(order.state, order.state),
            ])

        csv_bytes = output.getvalue().encode('utf-8')
        output.close()
        filename = 'sales_report_%s_%s_to_%s.csv' % (label, self.date_from, self.date_to)
        return self._get_attachment(csv_bytes, filename)

    def _export_res_partners(self):
        dt_from_utc, dt_to_utc = self._local_date_to_utc_range()

        if self.report_type == 'not_assigned':
            type_domain = [('user_id', '=', False)]
            label = 'Not_Assigned'
        else:
            type_domain = [('user_id', '!=', False)]
            label = 'Assigned'

        domain = type_domain + [
            ('create_date', '>=', fields.Datetime.to_string(dt_from_utc)),
            ('create_date', '<=', fields.Datetime.to_string(dt_to_utc)),
        ]
        records = self.env['res.partner'].search(domain, order='create_date desc')

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            'Name',
            'Phone',
            'Email',
            'City',
            'Country',
            'Salesperson',
            'Stage',
        ])
        for partner in records:
            writer.writerow([
                partner.name                or '',
                partner.phone               or partner.mobile or '',
                partner.email               or '',
                partner.city                or '',
                partner.country_id.name     if partner.country_id else '',
                partner.user_id.name        if partner.user_id else '',
                partner.stage_id.name       if hasattr(partner, 'stage_id') and partner.stage_id else '',
            ])

        csv_bytes = output.getvalue().encode('utf-8')
        output.close()
        filename = 'leads_report_%s_%s_to_%s.csv' % (label, self.date_from, self.date_to)
        return self._get_attachment(csv_bytes, filename)

    def _export_customer_rfq(self):
        domain = [
            # exclude Draft records from the exported dataset
            ('state', '!=', 'draft'),
        ]
        records = self.env['customer.rfq'].search(domain, order='create_date desc')

        # Build selection label maps for selection fields
        rfq_model   = self.env['customer.rfq']
        avail_map   = dict(rfq_model._fields['availability_status'].selection) \
                      if hasattr(rfq_model._fields.get('availability_status', None), 'selection') \
                      else {}
        state_map   = dict(rfq_model._fields['state'].selection) \
                      if hasattr(rfq_model._fields.get('state', None), 'selection') \
                      else {}

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            'Reference',
            'Owner',
            'Customer Name',
            'Status',
            'CRFQ Created',
            'Sent to Procurement',
            'Sent back to Sales',
            'Time Difference',
        ])

        for rfq in records:
            # shape_ids is many2many — join all shape names with comma
            shapes = ', '.join(rfq.shape_ids.mapped('name')) if rfq.shape_ids else ''

            # selection fields — get the human-readable label
            availability = avail_map.get(rfq.availability_status, rfq.availability_status or '')
            status       = state_map.get(rfq.state, rfq.state or '')

            # Chatter-sourced datetime columns in user's local timezone
            crfq_created       = self._utc_to_user_tz(rfq.create_date)
            sent_to_proc       = self._utc_to_user_tz(rfq.sent_to_procurement_dt) if rfq.sent_to_procurement_dt else ''
            sent_back_to_sales = self._utc_to_user_tz(rfq.sent_back_to_sales_dt) if rfq.sent_back_to_sales_dt else ''
            time_difference    = rfq.time_difference or ''

            writer.writerow([
                rfq.name                              or '',
                rfq.owner_id.name if rfq.owner_id else '',
                rfq.partner_id.name if rfq.partner_id else '',
                status,
                crfq_created,
                sent_to_proc,
                sent_back_to_sales,
                time_difference,
            ])

        csv_bytes = output.getvalue().encode('utf-8')
        output.close()
        filename = 'procurement_rfq_%s_to_%s.csv' % (self.date_from, self.date_to)
        return self._get_attachment(csv_bytes, filename)

    def _export_res_users(self):
        records = self.env['res.users'].search([('share', '=', False)])

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Name', 'Login', 'Email'])
        for user in records:
            writer.writerow([user.name or '', user.login or '', user.email or ''])

        csv_bytes = output.getvalue().encode('utf-8')
        output.close()
        filename = 'report_%s_to_%s.csv' % (self.date_from, self.date_to)
        return self._get_attachment(csv_bytes, filename)
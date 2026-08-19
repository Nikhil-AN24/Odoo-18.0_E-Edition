# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class GoalDailyTracking(models.Model):
    _name = 'goal.daily.tracking'
    _description = 'Goal Daily Tracking'
    _inherit = ['mail.thread']
    _order = 'date desc, id desc'
    _rec_name = 'display_name'

    # Basic Information
    goal_id = fields.Many2one('employee.goal', string='Goal', required=True,
                              ondelete='cascade', tracking=True)
    employee_id = fields.Many2one('hr.employee', string='Employee', required=True,
                                  default=lambda self: self.env.user.employee_id)
    user_id = fields.Many2one('res.users', string='User',
                             related='employee_id.user_id', store=True)

    # Date
    date = fields.Date(string='Date', required=True, default=fields.Date.today,
                      tracking=True)

    # Work Done Requirement
    skip_work_done = fields.Selection([
        ('no', 'No - Work Done Required'),
        ('yes', 'Yes - Skip Work Done'),
    ], string='Skip Work Done Entry?', default='no', required=True)

    # Progress Update
    progress_percentage = fields.Float(string='Progress (%)', tracking=True)
    work_done = fields.Html(string='Work Done Today')

    # Time Tracking
    hours_spent = fields.Float(string='Hours Spent', tracking=True)

    # Status
    status = fields.Selection([
        ('on_track', 'On Track'),
        ('at_risk', 'At Risk'),
        ('blocked', 'Blocked'),
        ('completed', 'Completed'),
    ], string='Status', required=True, default='on_track', tracking=True)

    # Challenges & Blockers
    challenges = fields.Text(string='Challenges/Blockers')
    support_needed = fields.Text(string='Support Needed')

    # Next Steps
    next_steps = fields.Text(string='Next Steps')

    # Milestone Progress
    milestone_id = fields.Many2one('goal.milestone', string='Milestone Worked On',
                                   domain="[('goal_id', '=', goal_id)]")
    milestone_progress = fields.Float(string='Milestone Progress (%)')

    # Weekly Tracking
    weekly_tracking_id = fields.Many2one('goal.weekly.tracking', string='Weekly Tracking',
                                         ondelete='cascade')

    # Manager Feedback
    manager_feedback = fields.Text(string='Manager Feedback')
    manager_id = fields.Many2one('hr.employee', string='Feedback By')
    feedback_date = fields.Datetime(string='Feedback Date')

    # Attachments
    attachment_count = fields.Integer(string='Attachments', compute='_compute_attachment_count')

    # Display
    display_name = fields.Char(string='Display Name', compute='_compute_display_name')

    # Company
    company_id = fields.Many2one('res.company', string='Company',
                                 related='goal_id.company_id', store=True)

    _sql_constraints = [
        ('check_progress', 'CHECK(progress_percentage >= 0 AND progress_percentage <= 100)',
         'Progress must be between 0 and 100!'),
        ('unique_employee_goal_date', 'unique(employee_id, goal_id, date)',
         'Only one update per goal per day is allowed!'),
    ]

    @api.depends('goal_id', 'date')
    def _compute_display_name(self):
        for record in self:
            if record.goal_id and record.date:
                record.display_name = f"{record.goal_id.title} - {record.date}"
            else:
                record.display_name = _('Daily Update')

    def _compute_attachment_count(self):
        for tracking in self:
            tracking.attachment_count = self.env['ir.attachment'].search_count([
                ('res_model', '=', self._name),
                ('res_id', '=', tracking.id)
            ])

    @api.onchange('goal_id')
    def _onchange_goal_id(self):
        if self.goal_id:
            self.employee_id = self.goal_id.employee_id
            # Set progress to current goal progress
            self.progress_percentage = self.goal_id.progress

    @api.constrains('skip_work_done', 'work_done')
    def _check_work_done_required(self):
        for tracking in self:
            if tracking.skip_work_done == 'no' and not tracking.work_done:
                raise ValidationError(_('Work Done Today is required when Skip Work Done Entry is set to No!'))

    @api.constrains('date', 'goal_id')
    def _check_date_within_goal_period(self):
        for tracking in self:
            if tracking.goal_id:
                if tracking.date < tracking.goal_id.start_date:
                    raise ValidationError(
                        _('Update date cannot be before goal start date (%s)!') %
                        tracking.goal_id.start_date
                    )
                if tracking.goal_id.end_date and tracking.date > tracking.goal_id.end_date:
                    raise ValidationError(
                        _('Update date cannot be after goal end date (%s)!') %
                        tracking.goal_id.end_date
                    )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for record in records:
            # Update goal progress if manual progress is used
            if record.goal_id.use_manual_progress:
                record.goal_id.manual_progress = record.progress_percentage

            # Update milestone progress
            if record.milestone_id and record.milestone_progress:
                record.milestone_id.progress = record.milestone_progress

            # Send notification to manager if blocked or at risk
            if record.status in ['blocked', 'at_risk']:
                record._notify_manager()

        return records

    def write(self, vals):
        res = super().write(vals)
        for record in self:
            # Update goal progress if manual progress is used
            if 'progress_percentage' in vals and record.goal_id.use_manual_progress:
                record.goal_id.manual_progress = record.progress_percentage

            # Update milestone progress
            if 'milestone_progress' in vals and record.milestone_id:
                record.milestone_id.progress = record.milestone_progress

        return res

    # Actions
    def action_add_feedback(self):
        self.ensure_one()
        return {
            'name': _('Add Manager Feedback'),
            'type': 'ir.actions.act_window',
            'res_model': 'goal.daily.tracking',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
            'context': {'form_view_ref': 'employee_goals.view_goal_daily_tracking_feedback_form'}
        }

    def action_view_attachments(self):
        self.ensure_one()
        return {
            'name': _('Attachments'),
            'type': 'ir.actions.act_window',
            'res_model': 'ir.attachment',
            'view_mode': 'list,form',
            'domain': [('res_model', '=', self._name), ('res_id', '=', self.id)],
            'context': {
                'default_res_model': self._name,
                'default_res_id': self.id,
            }
        }

    # Notifications
    def _notify_manager(self):
        for tracking in self:
            if tracking.goal_id.manager_id and tracking.goal_id.manager_id.user_id:
                body = _('Employee %s has marked the goal "%s" as %s.\n\nWork Done:\n%s') % (
                    tracking.employee_id.name,
                    tracking.goal_id.title,
                    dict(tracking._fields['status'].selection).get(tracking.status),
                    tracking.work_done
                )

                if tracking.challenges:
                    body += _('\n\nChallenges/Blockers:\n%s') % tracking.challenges

                if tracking.support_needed:
                    body += _('\n\nSupport Needed:\n%s') % tracking.support_needed

                tracking.goal_id.message_post(
                    body=body,
                    partner_ids=[tracking.goal_id.manager_id.user_id.partner_id.id],
                    subject=_('Goal Update - Attention Required'),
                )

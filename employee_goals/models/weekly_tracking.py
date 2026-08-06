# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class GoalWeeklyTracking(models.Model):
    _name = 'goal.weekly.tracking'
    _description = 'Goal Weekly Tracking'
    _inherit = ['mail.thread']
    _order = 'week_start_date desc, id desc'
    _rec_name = 'display_name'

    # Basic Information
    milestone_id = fields.Many2one('goal.milestone', string='Milestone', required=True,
                                   ondelete='cascade', tracking=True)
    goal_id = fields.Many2one('employee.goal', string='Goal',
                              related='milestone_id.goal_id', store=True)
    employee_id = fields.Many2one('hr.employee', string='Employee',
                                  related='milestone_id.employee_id', store=True)

    # Week Information
    week_start_date = fields.Date(string='Week Start Date', required=True, tracking=True)
    week_end_date = fields.Date(string='Week End Date', required=True, tracking=True)
    week_number = fields.Integer(string='Week Number', compute='_compute_week_number', store=True)

    # Progress
    weekly_progress = fields.Float(string='Weekly Progress (%)', tracking=True)
    total_hours_spent = fields.Float(string='Total Hours Spent', compute='_compute_totals', store=True)

    # Summary
    weekly_summary = fields.Html(string='Weekly Summary')
    achievements = fields.Text(string='Key Achievements')
    challenges = fields.Text(string='Challenges')
    next_week_plan = fields.Text(string='Next Week Plan')

    # Daily Progress Links
    daily_tracking_ids = fields.One2many('goal.daily.tracking', 'weekly_tracking_id',
                                         string='Daily Progress')
    daily_count = fields.Integer(string='Daily Updates', compute='_compute_daily_count', store=True)

    # Status
    status = fields.Selection([
        ('on_track', 'On Track'),
        ('at_risk', 'At Risk'),
        ('blocked', 'Blocked'),
        ('completed', 'Completed'),
    ], string='Status', required=True, default='on_track', tracking=True)

    # Display
    display_name = fields.Char(string='Display Name', compute='_compute_display_name', store=True)

    # Company
    company_id = fields.Many2one('res.company', string='Company',
                                 related='milestone_id.company_id', store=True)

    _sql_constraints = [
        ('check_weekly_progress', 'CHECK(weekly_progress >= 0 AND weekly_progress <= 100)',
         'Weekly progress must be between 0 and 100!'),
        ('check_dates', 'CHECK(week_end_date >= week_start_date)',
         'Week end date must be after start date!'),
    ]

    @api.depends('milestone_id', 'week_start_date', 'week_end_date')
    def _compute_display_name(self):
        for record in self:
            if record.milestone_id and record.week_start_date and record.week_end_date:
                record.display_name = f"{record.milestone_id.name} - Week {record.week_start_date} to {record.week_end_date}"
            else:
                record.display_name = _('Weekly Tracking')

    @api.depends('week_start_date')
    def _compute_week_number(self):
        for record in self:
            if record.week_start_date:
                record.week_number = record.week_start_date.isocalendar()[1]
            else:
                record.week_number = 0

    @api.depends('daily_tracking_ids')
    def _compute_daily_count(self):
        for record in self:
            record.daily_count = len(record.daily_tracking_ids)

    @api.depends('daily_tracking_ids.hours_spent')
    def _compute_totals(self):
        for record in self:
            record.total_hours_spent = sum(record.daily_tracking_ids.mapped('hours_spent'))

    @api.constrains('week_start_date', 'week_end_date', 'milestone_id')
    def _check_dates_within_milestone(self):
        for record in self:
            if record.milestone_id and record.milestone_id.start_date and record.milestone_id.end_date:
                if record.week_start_date < record.milestone_id.start_date:
                    raise ValidationError(
                        _('Week start date cannot be before milestone start date (%s)!') %
                        record.milestone_id.start_date
                    )
                if record.week_end_date > record.milestone_id.end_date:
                    raise ValidationError(
                        _('Week end date cannot be after milestone end date (%s)!') %
                        record.milestone_id.end_date
                    )

    @api.model
    def create_weekly_tracking_from_daily(self, milestone_id, week_start, week_end):
        """Create or update weekly tracking from daily progress entries"""
        milestone = self.env['goal.milestone'].browse(milestone_id)

        # Search for existing weekly tracking
        weekly_tracking = self.search([
            ('milestone_id', '=', milestone_id),
            ('week_start_date', '=', week_start),
            ('week_end_date', '=', week_end),
        ], limit=1)

        # Get daily tracking entries for this week
        daily_entries = self.env['goal.daily.tracking'].search([
            ('milestone_id', '=', milestone_id),
            ('date', '>=', week_start),
            ('date', '<=', week_end),
        ])

        if daily_entries and not weekly_tracking:
            # Create new weekly tracking
            weekly_tracking = self.create({
                'milestone_id': milestone_id,
                'week_start_date': week_start,
                'week_end_date': week_end,
            })
            # Link daily entries to weekly tracking
            daily_entries.write({'weekly_tracking_id': weekly_tracking.id})

        return weekly_tracking

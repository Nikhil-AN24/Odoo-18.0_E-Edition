# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from datetime import timedelta


class GoalMilestone(models.Model):
    _name = 'goal.milestone'
    _description = 'Goal Milestone'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'sequence, target_date, id'
    _rec_name = 'name'

    # Basic Information
    name = fields.Char(string='Milestone Name', required=True, tracking=True)
    description = fields.Html(string='Description')
    sequence = fields.Integer(string='Sequence', default=10)

    # Goal Relation
    goal_id = fields.Many2one('employee.goal', string='Goal', required=True,
                              ondelete='cascade', tracking=True)
    goal_type_id = fields.Many2one('goal.type', string='Goal Type',
                                   related='goal_id.goal_type_id', store=True)
    employee_id = fields.Many2one('hr.employee', string='Employee',
                                  related='goal_id.employee_id', store=True)
    manager_id = fields.Many2one('hr.employee', string='Manager',
                                 related='goal_id.manager_id', store=True)

    # Timeline
    start_date = fields.Date(string='Start Date', tracking=True)
    end_date = fields.Date(string='End Date', tracking=True)
    target_date = fields.Date(string='Target Date', required=True, tracking=True)
    completion_date = fields.Date(string='Completion Date', tracking=True)
    days_to_complete = fields.Integer(string='Days to Complete',
                                      compute='_compute_days_to_complete', store=True)
    is_overdue = fields.Boolean(string='Overdue', compute='_compute_is_overdue', store=True)

    # Status & Progress
    state = fields.Selection([
        ('pending', 'Pending'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ], string='Status', default='pending', required=True, tracking=True)

    progress = fields.Float(string='Progress (%)', tracking=True)

    # Deliverables
    deliverable = fields.Text(string='Expected Deliverable')
    actual_deliverable = fields.Text(string='Actual Deliverable')

    # Approval
    requires_approval = fields.Boolean(string='Requires Approval', default=True)
    approved_by_id = fields.Many2one('hr.employee', string='Approved By', tracking=True)
    approved_date = fields.Datetime(string='Approval Date')
    approval_notes = fields.Text(string='Approval Notes')

    # Dependencies
    dependency_ids = fields.Many2many('goal.milestone', 'milestone_dependency_rel',
                                      'milestone_id', 'depends_on_id',
                                      string='Depends On')
    dependent_milestone_ids = fields.Many2many('goal.milestone', 'milestone_dependency_rel',
                                               'depends_on_id', 'milestone_id',
                                               string='Dependent Milestones')

    # Measurements
    target_value = fields.Float(string='Target Value')
    achieved_value = fields.Float(string='Achieved Value')
    unit_of_measure = fields.Char(string='Unit')

    # Weekly Tracking
    weekly_tracking_ids = fields.One2many('goal.weekly.tracking', 'milestone_id',
                                          string='Weekly Tracking')
    weekly_tracking_count = fields.Integer(string='Weekly Updates',
                                           compute='_compute_weekly_tracking_count', store=True)

    # Additional Info
    notes = fields.Text(string='Notes')
    attachment_count = fields.Integer(string='Attachments', compute='_compute_attachment_count')

    # Company
    company_id = fields.Many2one('res.company', string='Company',
                                 related='goal_id.company_id', store=True)

    active = fields.Boolean(string='Active', default=True)
    color = fields.Integer(string='Color Index')

    _sql_constraints = [
        ('check_progress', 'CHECK(progress >= 0 AND progress <= 100)',
         'Progress must be between 0 and 100!'),
    ]

    @api.depends('target_date', 'completion_date')
    def _compute_days_to_complete(self):
        for milestone in self:
            if milestone.completion_date and milestone.target_date:
                milestone.days_to_complete = (
                    milestone.completion_date - milestone.target_date
                ).days
            else:
                milestone.days_to_complete = 0

    @api.depends('target_date', 'state')
    def _compute_is_overdue(self):
        today = fields.Date.today()
        for milestone in self:
            if milestone.state != 'completed' and milestone.target_date:
                milestone.is_overdue = milestone.target_date < today
            else:
                milestone.is_overdue = False

    def _compute_attachment_count(self):
        for milestone in self:
            milestone.attachment_count = self.env['ir.attachment'].search_count([
                ('res_model', '=', self._name),
                ('res_id', '=', milestone.id)
            ])

    @api.depends('weekly_tracking_ids')
    def _compute_weekly_tracking_count(self):
        for milestone in self:
            milestone.weekly_tracking_count = len(milestone.weekly_tracking_ids)

    @api.constrains('start_date', 'end_date')
    def _check_dates(self):
        for milestone in self:
            if milestone.start_date and milestone.end_date:
                if milestone.start_date > milestone.end_date:
                    raise ValidationError(_('End date must be after start date!'))

    def action_generate_weekly_tracking(self):
        """Fetch daily progress entries and create weekly tracking records"""
        for milestone in self:
            if not milestone.start_date or not milestone.end_date:
                raise ValidationError(_('Please set Start Date and End Date first!'))

            # Get all daily progress entries for this milestone within the date range
            daily_entries = self.env['goal.daily.tracking'].search([
                ('milestone_id', '=', milestone.id),
                ('date', '>=', milestone.start_date),
                ('date', '<=', milestone.end_date),
            ], order='date')

            if not daily_entries:
                raise ValidationError(_('No daily progress entries found for this milestone within the specified date range!'))

            # Group daily entries by week
            weekly_groups = {}
            for entry in daily_entries:
                # Calculate week start (Monday) and week end (Sunday)
                week_start = entry.date - timedelta(days=entry.date.weekday())
                week_end = week_start + timedelta(days=6)

                # Ensure within milestone date range
                if week_start < milestone.start_date:
                    week_start = milestone.start_date
                if week_end > milestone.end_date:
                    week_end = milestone.end_date

                week_key = (week_start, week_end)
                if week_key not in weekly_groups:
                    weekly_groups[week_key] = []
                weekly_groups[week_key].append(entry)

            # Create or update weekly tracking records
            created_count = 0
            updated_count = 0
            for (week_start, week_end), entries in weekly_groups.items():
                # Check if weekly tracking already exists
                existing_weekly = self.env['goal.weekly.tracking'].search([
                    ('milestone_id', '=', milestone.id),
                    ('week_start_date', '=', week_start),
                    ('week_end_date', '=', week_end),
                ], limit=1)

                if not existing_weekly:
                    # Create new weekly tracking
                    existing_weekly = self.env['goal.weekly.tracking'].create({
                        'milestone_id': milestone.id,
                        'week_start_date': week_start,
                        'week_end_date': week_end,
                    })
                    created_count += 1

                # Link daily entries to weekly tracking
                for entry in entries:
                    if not entry.weekly_tracking_id:
                        entry.weekly_tracking_id = existing_weekly.id
                        updated_count += 1

            # Show notification
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Success'),
                    'message': _('Created %s weekly tracking records and linked %s daily progress entries.') % (created_count, updated_count),
                    'type': 'success',
                    'sticky': False,
                }
            }

    @api.constrains('dependency_ids')
    def _check_circular_dependency(self):
        for milestone in self:
            if milestone in milestone.dependency_ids:
                raise ValidationError(_('A milestone cannot depend on itself!'))

            # Check for circular dependencies
            visited = set()
            def check_circular(m):
                if m.id in visited:
                    return True
                visited.add(m.id)
                for dep in m.dependency_ids:
                    if check_circular(dep):
                        return True
                return False

            if check_circular(milestone):
                raise ValidationError(_('Circular dependency detected!'))

    # State Management
    def action_start(self):
        for milestone in self:
            # Check dependencies
            if milestone.dependency_ids:
                pending_deps = milestone.dependency_ids.filtered(
                    lambda m: m.state != 'completed'
                )
                if pending_deps:
                    raise ValidationError(
                        _('Cannot start milestone. Following dependencies must be completed first:\n%s') %
                        '\n'.join(pending_deps.mapped('name'))
                    )
            milestone.write({'state': 'in_progress'})
        return True

    def action_complete(self):
        for milestone in self:
            if milestone.progress < 100:
                milestone.progress = 100

            vals = {
                'state': 'completed',
                'completion_date': fields.Date.today(),
                'progress': 100,
            }

            if not milestone.requires_approval:
                vals['approved_by_id'] = self.env.user.employee_id.id
                vals['approved_date'] = fields.Datetime.now()

            milestone.write(vals)

            # Notify manager if approval required
            if milestone.requires_approval and milestone.manager_id:
                milestone._send_approval_request()

        return True

    def action_approve(self):
        for milestone in self:
            milestone.write({
                'approved_by_id': self.env.user.employee_id.id,
                'approved_date': fields.Datetime.now(),
            })
            milestone._send_approval_confirmation()
        return True

    def action_cancel(self):
        self.write({'state': 'cancelled'})
        return True

    def action_reset(self):
        self.write({
            'state': 'pending',
            'progress': 0,
            'completion_date': False,
            'approved_by_id': False,
            'approved_date': False,
        })
        return True

    # Attachments
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
    def _send_approval_request(self):
        for milestone in self:
            if milestone.manager_id and milestone.manager_id.user_id:
                milestone.message_post(
                    body=_('Milestone "%s" has been completed and requires your approval.') % milestone.name,
                    partner_ids=[milestone.manager_id.user_id.partner_id.id],
                    subject=_('Milestone Approval Required'),
                )

    def _send_approval_confirmation(self):
        for milestone in self:
            if milestone.employee_id.user_id:
                milestone.message_post(
                    body=_('Milestone "%s" has been approved by %s') % (
                        milestone.name, milestone.approved_by_id.name
                    ),
                    partner_ids=[milestone.employee_id.user_id.partner_id.id],
                    subject=_('Milestone Approved'),
                )

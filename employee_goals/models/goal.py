# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from datetime import datetime, timedelta


class EmployeeGoal(models.Model):
    _name = 'employee.goal'
    _description = 'Employee Goal'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc, id desc'
    _rec_name = 'title'

    # Basic Information
    title = fields.Char(string='Goal Title', required=True, tracking=True)
    description = fields.Html(string='Description')
    goal_type_id = fields.Many2one('goal.type', string='Goal Type', required=True, tracking=True)

    # Employee Information
    employee_id = fields.Many2one('hr.employee', string='Employee', required=True,
                                  default=lambda self: self.env.user.employee_id, tracking=True)
    user_id = fields.Many2one('res.users', string='User', related='employee_id.user_id', store=True)
    department_id = fields.Many2one('hr.department', string='Department',
                                    related='employee_id.department_id', store=True)
    job_id = fields.Many2one('hr.job', string='Job Position',
                             related='employee_id.job_id', store=True)
    manager_id = fields.Many2one('hr.employee', string='Manager',
                                 related='employee_id.parent_id', store=True)

    # Timeline
    start_date = fields.Date(string='Start Date', required=True,
                            default=fields.Date.today, tracking=True)
    end_date = fields.Date(string='Target End Date', required=True, tracking=True)
    actual_end_date = fields.Date(string='Actual End Date', tracking=True)
    duration_days = fields.Integer(string='Duration (Days)', compute='_compute_duration', store=True)
    days_remaining = fields.Integer(string='Days Remaining', compute='_compute_days_remaining', store=True)
    is_overdue = fields.Boolean(string='Overdue', compute='_compute_days_remaining', store=True)

    # Status & Progress
    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('approved', 'Approved'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
        ('on_hold', 'On Hold'),
    ], string='Status', default='draft', required=True, tracking=True)

    progress = fields.Float(string='Progress (%)', compute='_compute_progress', store=True)
    manual_progress = fields.Float(string='Manual Progress (%)', tracking=True)
    use_manual_progress = fields.Boolean(string='Use Manual Progress')

    # Milestones
    milestone_ids = fields.One2many('goal.milestone', 'goal_id', string='Milestones')
    milestone_count = fields.Integer(string='Milestone Count', compute='_compute_milestone_stats')
    completed_milestone_count = fields.Integer(string='Completed Milestones',
                                               compute='_compute_milestone_stats')

    # Daily Tracking
    tracking_ids = fields.One2many('goal.daily.tracking', 'goal_id', string='Daily Updates')
    last_update_date = fields.Date(string='Last Update', compute='_compute_last_update')
    total_updates = fields.Integer(string='Total Updates', compute='_compute_total_updates')

    # Priority & Impact
    priority = fields.Selection([
        ('0', 'Low'),
        ('1', 'Normal'),
        ('2', 'High'),
        ('3', 'Critical'),
    ], string='Priority', default='1', tracking=True)

    # Creator & Approval
    created_by_id = fields.Many2one('res.users', string='Created By',
                                    default=lambda self: self.env.user, readonly=True)
    is_self_created = fields.Boolean(string='Self Created', compute='_compute_is_self_created', store=True)
    approved_by_id = fields.Many2one('hr.employee', string='Approved By', tracking=True)
    approved_date = fields.Datetime(string='Approval Date')

    # KPI & Measurement
    target_value = fields.Float(string='Target Value')
    achieved_value = fields.Float(string='Achieved Value')
    unit_of_measure = fields.Char(string='Unit of Measure')

    # Additional Info
    notes = fields.Text(string='Notes')
    tags_ids = fields.Many2many('goal.tag', string='Tags')
    attachment_count = fields.Integer(string='Attachments', compute='_compute_attachment_count')

    # Company
    company_id = fields.Many2one('res.company', string='Company',
                                 default=lambda self: self.env.company)

    active = fields.Boolean(string='Active', default=True)
    color = fields.Integer(string='Color Index')

    _sql_constraints = [
        ('check_dates', 'CHECK(end_date >= start_date)',
         'End date must be greater than or equal to start date!'),
        ('check_progress', 'CHECK(manual_progress >= 0 AND manual_progress <= 100)',
         'Progress must be between 0 and 100!'),
    ]

    @api.depends('start_date', 'end_date')
    def _compute_duration(self):
        for goal in self:
            if goal.start_date and goal.end_date:
                goal.duration_days = (goal.end_date - goal.start_date).days + 1
            else:
                goal.duration_days = 0

    @api.depends('end_date', 'state')
    def _compute_days_remaining(self):
        today = fields.Date.today()
        for goal in self:
            if goal.state not in ['completed', 'cancelled'] and goal.end_date:
                remaining = (goal.end_date - today).days
                goal.days_remaining = remaining
                goal.is_overdue = remaining < 0
            else:
                goal.days_remaining = 0
                goal.is_overdue = False

    @api.depends('milestone_ids', 'milestone_ids.state', 'use_manual_progress', 'manual_progress')
    def _compute_progress(self):
        for goal in self:
            if goal.use_manual_progress:
                goal.progress = goal.manual_progress
            elif goal.milestone_ids:
                total = len(goal.milestone_ids)
                completed = len(goal.milestone_ids.filtered(lambda m: m.state == 'completed'))
                goal.progress = (completed / total * 100) if total > 0 else 0
            else:
                goal.progress = 0

    @api.depends('milestone_ids', 'milestone_ids.state')
    def _compute_milestone_stats(self):
        for goal in self:
            goal.milestone_count = len(goal.milestone_ids)
            goal.completed_milestone_count = len(
                goal.milestone_ids.filtered(lambda m: m.state == 'completed')
            )

    @api.depends('tracking_ids', 'tracking_ids.date')
    def _compute_last_update(self):
        for goal in self:
            if goal.tracking_ids:
                goal.last_update_date = max(goal.tracking_ids.mapped('date'))
            else:
                goal.last_update_date = False

    @api.depends('tracking_ids')
    def _compute_total_updates(self):
        for goal in self:
            goal.total_updates = len(goal.tracking_ids)

    @api.depends('created_by_id', 'employee_id')
    def _compute_is_self_created(self):
        for goal in self:
            goal.is_self_created = (
                goal.created_by_id.id == goal.employee_id.user_id.id
            )

    def _compute_attachment_count(self):
        for goal in self:
            goal.attachment_count = self.env['ir.attachment'].search_count([
                ('res_model', '=', self._name),
                ('res_id', '=', goal.id)
            ])

    @api.onchange('goal_type_id')
    def _onchange_goal_type(self):
        if self.goal_type_id and self.goal_type_id.auto_create_milestones:
            self.milestone_ids = [(5, 0, 0)]  # Clear existing milestones
            milestones = []
            count = self.goal_type_id.default_milestone_count or 3

            if self.start_date and self.end_date:
                duration = (self.end_date - self.start_date).days
                interval = duration / count

                for i in range(count):
                    target_date = self.start_date + timedelta(days=int(interval * (i + 1)))
                    milestones.append((0, 0, {
                        'name': f'Milestone {i + 1}',
                        'sequence': (i + 1) * 10,
                        'target_date': target_date,
                    }))
                self.milestone_ids = milestones

    @api.constrains('end_date', 'milestone_ids')
    def _check_milestone_dates(self):
        for goal in self:
            for milestone in goal.milestone_ids:
                if milestone.target_date > goal.end_date:
                    raise ValidationError(
                        _('Milestone "%s" target date cannot be after goal end date!') % milestone.name
                    )

    # State Management
    def action_submit(self):
        for goal in self:
            if goal.goal_type_id.requires_approval:
                goal.write({'state': 'submitted'})
                goal._send_approval_notification()
            else:
                goal.write({'state': 'approved'})
        return True

    def action_approve(self):
        for goal in self:
            goal.write({
                'state': 'approved',
                'approved_by_id': self.env.user.employee_id.id,
                'approved_date': fields.Datetime.now(),
            })
            goal._send_approval_confirmation()
        return True

    def action_start(self):
        self.write({'state': 'in_progress'})
        return True

    def action_complete(self):
        for goal in self:
            if goal.milestone_ids and any(m.state != 'completed' for m in goal.milestone_ids):
                raise ValidationError(_('All milestones must be completed before completing the goal!'))
            goal.write({
                'state': 'completed',
                'actual_end_date': fields.Date.today(),
                'progress': 100,
            })
        return True

    def action_cancel(self):
        self.write({'state': 'cancelled'})
        return True

    def action_hold(self):
        self.write({'state': 'on_hold'})
        return True

    def action_reset_to_draft(self):
        self.write({'state': 'draft'})
        return True

    # Daily Update
    def action_add_daily_update(self):
        self.ensure_one()
        return {
            'name': _('Add Daily Update'),
            'type': 'ir.actions.act_window',
            'res_model': 'goal.daily.tracking',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_goal_id': self.id,
                'default_employee_id': self.employee_id.id,
            }
        }

    # Milestones
    def action_view_milestones(self):
        self.ensure_one()
        return {
            'name': _('Milestones'),
            'type': 'ir.actions.act_window',
            'res_model': 'goal.milestone',
            'view_mode': 'list,form,kanban',
            'domain': [('goal_id', '=', self.id)],
            'context': {'default_goal_id': self.id}
        }

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
    def _send_approval_notification(self):
        for goal in self:
            if goal.manager_id and goal.manager_id.user_id:
                goal.message_post(
                    body=_('Goal "%s" has been submitted for approval by %s') % (
                        goal.title, goal.employee_id.name
                    ),
                    partner_ids=[goal.manager_id.user_id.partner_id.id],
                    subject=_('Goal Approval Request'),
                )

    def _send_approval_confirmation(self):
        for goal in self:
            if goal.employee_id.user_id:
                goal.message_post(
                    body=_('Your goal "%s" has been approved by %s') % (
                        goal.title, goal.approved_by_id.name
                    ),
                    partner_ids=[goal.employee_id.user_id.partner_id.id],
                    subject=_('Goal Approved'),
                )


class GoalTag(models.Model):
    _name = 'goal.tag'
    _description = 'Goal Tag'

    name = fields.Char(string='Tag Name', required=True, translate=True)
    color = fields.Integer(string='Color Index')

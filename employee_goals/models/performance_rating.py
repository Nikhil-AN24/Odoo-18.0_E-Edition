# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class PerformanceRating(models.Model):
    _name = 'performance.rating'
    _description = 'Performance Rating'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'rating_period desc, employee_id'
    _rec_name = 'display_name'

    # Employee Information
    employee_id = fields.Many2one('hr.employee', string='Employee', required=True,
                                  tracking=True)
    user_id = fields.Many2one('res.users', string='User',
                             related='employee_id.user_id', store=True)
    department_id = fields.Many2one('hr.department', string='Department',
                                   related='employee_id.department_id', store=True)
    job_id = fields.Many2one('hr.job', string='Job Position',
                            related='employee_id.job_id', store=True)
    manager_id = fields.Many2one('hr.employee', string='Manager',
                                related='employee_id.parent_id', store=True)

    # Rating Period
    rating_period = fields.Char(string='Rating Period', required=True, tracking=True,
                               help='e.g., 2024, Q1-2024, Jan-2024')
    period_start_date = fields.Date(string='Period Start Date', required=True)
    period_end_date = fields.Date(string='Period End Date', required=True)

    # Rating
    rating = fields.Selection([
        ('o', 'O - Outstanding'),
        ('a_plus', 'A+ - Excellent'),
        ('a', 'A - Very Good'),
        ('b_plus', 'B+ - Good'),
        ('b', 'B - Satisfactory'),
        ('c', 'C - Needs Improvement'),
        ('d', 'D - Unsatisfactory'),
    ], string='Performance Rating', required=True, tracking=True)

    rating_score = fields.Float(string='Rating Score', compute='_compute_rating_score',
                               store=True, tracking=True)

    # Goals Performance
    total_goals = fields.Integer(string='Total Goals', compute='_compute_goal_stats', store=True)
    completed_goals = fields.Integer(string='Completed Goals',
                                    compute='_compute_goal_stats', store=True)
    goal_completion_rate = fields.Float(string='Goal Completion Rate (%)',
                                       compute='_compute_goal_stats', store=True)
    average_goal_progress = fields.Float(string='Average Goal Progress (%)',
                                        compute='_compute_goal_stats', store=True)

    # Training Performance
    training_programs_completed = fields.Integer(string='Training Programs Completed',
                                                compute='_compute_training_stats', store=True)
    training_completion_rate = fields.Float(string='Training Completion Rate (%)',
                                           compute='_compute_training_stats', store=True)

    # Key Achievements
    achievements = fields.Html(string='Key Achievements')
    strengths = fields.Html(string='Strengths')
    areas_of_improvement = fields.Html(string='Areas of Improvement')

    # Comments
    employee_comments = fields.Html(string='Employee Comments')
    manager_comments = fields.Html(string='Manager Comments')
    hr_comments = fields.Html(string='HR Comments')

    # Recommendations
    promotion_recommended = fields.Boolean(string='Promotion Recommended')
    salary_increment_recommended = fields.Boolean(string='Salary Increment Recommended')
    increment_percentage = fields.Float(string='Recommended Increment (%)')
    training_recommendations = fields.Text(string='Training Recommendations')

    # Goals Reference
    goal_ids = fields.Many2many('employee.goal', string='Goals',
                               compute='_compute_goals', store=True)

    # Status
    state = fields.Selection([
        ('draft', 'Draft'),
        ('self_review', 'Self Review'),
        ('manager_review', 'Manager Review'),
        ('hr_review', 'HR Review'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ], string='Status', default='draft', required=True, tracking=True)

    # Approvals
    employee_signed = fields.Boolean(string='Employee Acknowledged')
    employee_signed_date = fields.Datetime(string='Employee Signed Date')
    manager_signed = fields.Boolean(string='Manager Approved')
    manager_signed_date = fields.Datetime(string='Manager Signed Date')
    hr_signed = fields.Boolean(string='HR Approved')
    hr_signed_date = fields.Datetime(string='HR Signed Date')

    # Display
    display_name = fields.Char(string='Display Name', compute='_compute_display_name')

    # Company
    company_id = fields.Many2one('res.company', string='Company',
                                default=lambda self: self.env.company)

    active = fields.Boolean(string='Active', default=True)

    _sql_constraints = [
        ('unique_employee_period', 'unique(employee_id, rating_period)',
         'Only one rating per employee per period is allowed!'),
        ('check_dates', 'CHECK(period_end_date >= period_start_date)',
         'End date must be greater than or equal to start date!'),
    ]

    @api.depends('employee_id', 'rating_period')
    def _compute_display_name(self):
        for record in self:
            if record.employee_id and record.rating_period:
                record.display_name = f"{record.employee_id.name} - {record.rating_period}"
            else:
                record.display_name = _('Performance Rating')

    @api.depends('rating')
    def _compute_rating_score(self):
        rating_scores = {
            'o': 100,
            'a_plus': 95,
            'a': 90,
            'b_plus': 85,
            'b': 80,
            'c': 70,
            'd': 60,
        }
        for record in self:
            record.rating_score = rating_scores.get(record.rating, 0)

    @api.depends('employee_id', 'period_start_date', 'period_end_date')
    def _compute_goals(self):
        for record in self:
            if record.employee_id and record.period_start_date and record.period_end_date:
                goals = self.env['employee.goal'].search([
                    ('employee_id', '=', record.employee_id.id),
                    ('start_date', '>=', record.period_start_date),
                    ('start_date', '<=', record.period_end_date),
                ])
                record.goal_ids = goals
            else:
                record.goal_ids = False

    @api.depends('goal_ids', 'goal_ids.state', 'goal_ids.progress')
    def _compute_goal_stats(self):
        for record in self:
            goals = record.goal_ids
            record.total_goals = len(goals)
            record.completed_goals = len(goals.filtered(lambda g: g.state == 'completed'))

            if record.total_goals > 0:
                record.goal_completion_rate = (record.completed_goals / record.total_goals) * 100
                record.average_goal_progress = sum(goals.mapped('progress')) / record.total_goals
            else:
                record.goal_completion_rate = 0
                record.average_goal_progress = 0

    @api.depends('goal_ids', 'goal_ids.goal_type_id')
    def _compute_training_stats(self):
        for record in self:
            training_goals = record.goal_ids.filtered(
                lambda g: g.goal_type_id.category == 'training'
            )
            completed_training = training_goals.filtered(lambda g: g.state == 'completed')

            record.training_programs_completed = len(completed_training)
            if training_goals:
                record.training_completion_rate = (
                    len(completed_training) / len(training_goals)
                ) * 100
            else:
                record.training_completion_rate = 0

    # State Management
    def action_submit_self_review(self):
        self.write({
            'state': 'self_review',
            'employee_signed': True,
            'employee_signed_date': fields.Datetime.now(),
        })
        self._notify_manager()
        return True

    def action_submit_manager_review(self):
        self.write({
            'state': 'manager_review',
            'manager_signed': True,
            'manager_signed_date': fields.Datetime.now(),
        })
        self._notify_hr()
        return True

    def action_submit_hr_review(self):
        self.write({
            'state': 'hr_review',
            'hr_signed': True,
            'hr_signed_date': fields.Datetime.now(),
        })
        return True

    def action_approve(self):
        self.write({'state': 'approved'})
        self._notify_employee_approval()
        return True

    def action_reject(self):
        self.write({'state': 'rejected'})
        return True

    def action_reset_to_draft(self):
        self.write({
            'state': 'draft',
            'employee_signed': False,
            'manager_signed': False,
            'hr_signed': False,
        })
        return True

    # Actions
    def action_view_goals(self):
        self.ensure_one()
        return {
            'name': _('Related Goals'),
            'type': 'ir.actions.act_window',
            'res_model': 'employee.goal',
            'view_mode': 'list,form,kanban',
            'domain': [('id', 'in', self.goal_ids.ids)],
            'context': {'create': False}
        }

    # Notifications
    def _notify_manager(self):
        for rating in self:
            if rating.manager_id and rating.manager_id.user_id:
                rating.message_post(
                    body=_('%s has completed self-review for %s. Please provide your feedback.') % (
                        rating.employee_id.name, rating.rating_period
                    ),
                    partner_ids=[rating.manager_id.user_id.partner_id.id],
                    subject=_('Performance Review - Manager Action Required'),
                )

    def _notify_hr(self):
        hr_group = self.env.ref('hr.group_hr_manager', raise_if_not_found=False)
        if hr_group:
            for rating in self:
                partner_ids = hr_group.users.mapped('partner_id').ids
                rating.message_post(
                    body=_('Performance review for %s (%s) is ready for HR review.') % (
                        rating.employee_id.name, rating.rating_period
                    ),
                    partner_ids=partner_ids,
                    subject=_('Performance Review - HR Action Required'),
                )

    def _notify_employee_approval(self):
        for rating in self:
            if rating.employee_id.user_id:
                rating.message_post(
                    body=_('Your performance rating for %s has been approved. Rating: %s') % (
                        rating.rating_period,
                        dict(rating._fields['rating'].selection).get(rating.rating)
                    ),
                    partner_ids=[rating.employee_id.user_id.partner_id.id],
                    subject=_('Performance Rating Approved'),
                )


class RatingScale(models.Model):
    _name = 'rating.scale'
    _description = 'Rating Scale'
    _order = 'score desc'

    name = fields.Char(string='Rating Name', required=True)
    code = fields.Char(string='Code', required=True)
    score = fields.Float(string='Score', required=True)
    description = fields.Text(string='Description')
    color = fields.Integer(string='Color Index')
    active = fields.Boolean(string='Active', default=True)

    _sql_constraints = [
        ('code_unique', 'unique(code)', 'Rating code must be unique!'),
    ]

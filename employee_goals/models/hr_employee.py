# -*- coding: utf-8 -*-
from odoo import models, fields, api, _


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    # Goal Statistics
    goal_count = fields.Integer(string='Total Goals', compute='_compute_goal_stats')
    active_goal_count = fields.Integer(string='Active Goals', compute='_compute_goal_stats')
    completed_goal_count = fields.Integer(string='Completed Goals', compute='_compute_goal_stats')
    goal_completion_rate = fields.Float(string='Goal Completion Rate (%)',
                                       compute='_compute_goal_stats')

    # Current Goals
    current_goal_ids = fields.One2many('employee.goal', 'employee_id',
                                       string='Goals',
                                       domain=[('state', 'in', ['approved', 'in_progress'])])

    # Performance Ratings
    performance_rating_ids = fields.One2many('performance.rating', 'employee_id',
                                            string='Performance Ratings')
    latest_rating = fields.Selection([
        ('o', 'O - Outstanding'),
        ('a_plus', 'A+ - Excellent'),
        ('a', 'A - Very Good'),
        ('b_plus', 'B+ - Good'),
        ('b', 'B - Satisfactory'),
        ('c', 'C - Needs Improvement'),
        ('d', 'D - Unsatisfactory'),
    ], string='Latest Rating', compute='_compute_latest_rating', store=True)

    previous_rating = fields.Selection([
        ('o', 'O - Outstanding'),
        ('a_plus', 'A+ - Excellent'),
        ('a', 'A - Very Good'),
        ('b_plus', 'B+ - Good'),
        ('b', 'B - Satisfactory'),
        ('c', 'C - Needs Improvement'),
        ('d', 'D - Unsatisfactory'),
    ], string='Previous Rating', compute='_compute_latest_rating', store=True)

    # LMI Status
    lmi_status = fields.Boolean(string='LMI (Leadership Management Index)', default=False)

    # Employment Status
    employment_status = fields.Selection([
        ('a', 'Status A'),
        ('b', 'Status B'),
        ('c', 'Status C'),
        ('d', 'Status D'),
    ], string='Employment Status')

    @api.depends('current_goal_ids', 'current_goal_ids.state')
    def _compute_goal_stats(self):
        for employee in self:
            all_goals = self.env['employee.goal'].search([('employee_id', '=', employee.id)])
            active_goals = all_goals.filtered(lambda g: g.state in ['approved', 'in_progress'])
            completed_goals = all_goals.filtered(lambda g: g.state == 'completed')

            employee.goal_count = len(all_goals)
            employee.active_goal_count = len(active_goals)
            employee.completed_goal_count = len(completed_goals)

            if employee.goal_count > 0:
                employee.goal_completion_rate = (
                    employee.completed_goal_count / employee.goal_count
                ) * 100
            else:
                employee.goal_completion_rate = 0

    @api.depends('performance_rating_ids', 'performance_rating_ids.rating')
    def _compute_latest_rating(self):
        for employee in self:
            ratings = employee.performance_rating_ids.filtered(
                lambda r: r.state == 'approved'
            ).sorted(key=lambda r: r.period_end_date, reverse=True)

            if ratings:
                employee.latest_rating = ratings[0].rating
                if len(ratings) > 1:
                    employee.previous_rating = ratings[1].rating
                else:
                    employee.previous_rating = False
            else:
                employee.latest_rating = False
                employee.previous_rating = False

    def action_view_goals(self):
        self.ensure_one()
        return {
            'name': _('Goals'),
            'type': 'ir.actions.act_window',
            'res_model': 'employee.goal',
            'view_mode': 'list,form,kanban',
            'domain': [('employee_id', '=', self.id)],
            'context': {'default_employee_id': self.id}
        }

    def action_view_performance_ratings(self):
        self.ensure_one()
        return {
            'name': _('Performance Ratings'),
            'type': 'ir.actions.act_window',
            'res_model': 'performance.rating',
            'view_mode': 'list,form',
            'domain': [('employee_id', '=', self.id)],
            'context': {'default_employee_id': self.id}
        }

    def action_create_goal(self):
        self.ensure_one()
        return {
            'name': _('Create Goal'),
            'type': 'ir.actions.act_window',
            'res_model': 'employee.goal',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_employee_id': self.id}
        }

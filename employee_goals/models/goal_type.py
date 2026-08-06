# -*- coding: utf-8 -*-
from odoo import models, fields, api


class GoalType(models.Model):
    _name = 'goal.type'
    _description = 'Goal Type'
    _order = 'sequence, name'

    name = fields.Char(string='Goal Type Name', required=True, translate=True)
    code = fields.Char(string='Code', required=True)
    description = fields.Text(string='Description', translate=True)
    sequence = fields.Integer(string='Sequence', default=10)
    active = fields.Boolean(string='Active', default=True)
    color = fields.Integer(string='Color Index')

    # Category
    category = fields.Selection([
        ('training', 'Training & Development'),
        ('performance', 'Performance Based'),
        ('custom', 'Custom Goals'),
        ('organizational', 'Organizational Goals'),
    ], string='Category', required=True, default='custom')

    # Configuration
    requires_approval = fields.Boolean(string='Requires Manager Approval', default=True)
    auto_create_milestones = fields.Boolean(string='Auto Create Milestones', default=False)
    default_milestone_count = fields.Integer(string='Default Milestone Count', default=3)

    # Statistics
    goal_count = fields.Integer(string='Number of Goals', compute='_compute_goal_count')

    _sql_constraints = [
        ('code_unique', 'unique(code)', 'Goal Type Code must be unique!')
    ]

    @api.depends('name')
    def _compute_goal_count(self):
        for record in self:
            record.goal_count = self.env['employee.goal'].search_count([('goal_type_id', '=', record.id)])

    def action_view_goals(self):
        self.ensure_one()
        return {
            'name': f'{self.name} Goals',
            'type': 'ir.actions.act_window',
            'res_model': 'employee.goal',
            'view_mode': 'list,form,kanban',
            'domain': [('goal_type_id', '=', self.id)],
            'context': {'default_goal_type_id': self.id}
        }

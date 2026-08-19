# -*- coding: utf-8 -*-
{
    'name': 'Employee Goals & Performance Management',
    'version': '18.0.1.0.0',
    'category': 'Human Resources',
    'summary': 'Goal Setting, Milestone Tracking, Performance Rating Management',
    'description': """
        Employee Goals & Performance Management System
        ==============================================
        * Create and manage employee goals
        * Track milestones and daily progress
        * Performance rating system
        * Training program tracking
        * Comprehensive reporting and analytics
        * Employee self-service goal creation
    """,
    'author': 'Your Company',
    'website': 'https://www.yourcompany.com',
    'depends': ['base', 'hr', 'mail'],
    'data': [
        # Security
        'security/goal_security.xml',
        'security/ir.model.access.csv',

        # Data
        'data/goal_type_data.xml',
        'data/rating_data.xml',

        # Views
        'views/goal_type_views.xml',
        'views/goal_views.xml',
        'views/milestone_views.xml',
        'views/daily_tracking_views.xml',
        'views/weekly_tracking_views.xml',
        'views/performance_rating_views.xml',
        'views/employee_views.xml',

        # Reports
        'reports/report_templates.xml',
        'reports/goal_report.xml',

        # Menu
        'views/menu_views.xml',
    ],
    'demo': [],
    'installable': True,
    'application': True,
    'auto_install': False,
    'license': 'LGPL-3',
}

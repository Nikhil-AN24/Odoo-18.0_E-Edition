{
    'name': 'Employee Reports',
    'version': '18.0.1.0.46',
    'category': 'Extra Tools',
    'summary': 'Custom Reports Module with Role Based Access',
    'description': 'With the date filter we can download the dataset reports with specific user access control.',
    "author": "Anirath",
    "website": "https://www.augmont.com/",
    'depends': ['base', 'sale', 'contact_stage_bar'],
    'data': [
        'security/security_data.xml',
        'security/ir.model.access.csv',
        'views/res_users_view.xml',
        'views/report_download_wizard_view.xml',
        'views/reports_menu.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'employee_reports/static/src/css/hide_action_menu.css',
        ],
    },
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}

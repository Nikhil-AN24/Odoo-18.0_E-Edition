{
    'name': 'Offline Sale Order',
    'version': '18.0.1.0.0',
    'category': 'Sales',
    'summary': 'Offline Sale Order Module with Auto SO Creation',
    'description': """
    Offline Sale Order module that:
    - Mimics standard Sale Order functionality
    - Creates actual Sale Orders when confirmed
    """,
    "author": "Anirath",
    "website": "https://www.augmont.com/",
    'depends': ['sale_management', 'stock', 'contact_stage_bar', 'mail'],
    'data': [
    'security/ir.model.access.csv',
    'views/custom_sale_order_views.xml',
    'views/menu_views.xml',
    'views/cancel_wizard.xml',
    'views/custom_sale_order_add_product_wizard.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
    'license': 'LGPL-3',
}


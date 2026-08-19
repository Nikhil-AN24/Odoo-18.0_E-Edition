{
    'name': 'Offline Sales Payment Links',
    'version': '18.0.1.0',
    'category': 'Sales',
    'summary': 'Generate PayU/Stripe payment links for offline sale orders.',
    'author': 'SDK Infinity',
    'depends': ['payment', 'mail', 'custom_sale_order', 'odoo_payment_payu'],
    'data': [
        'security/ir.model.access.csv',
        'data/mail_template_data.xml',
        'wizard/accept_payment_views.xml',
        'views/custom_sale_order_views.xml',
        'views/payu_redirect_template.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}

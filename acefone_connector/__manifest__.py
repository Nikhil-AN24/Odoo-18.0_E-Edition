{
    'name': 'Acefone Click to Call',
    'version': '18.0.1.0.2',
    'category': 'Sales/CRM',
    'summary': 'Click to Call integration with Acefone Cloud Telephony',
    'description': """
Acefone Click to Call
======================
Adds a "Call" button on Contacts, CRM Leads/Opportunities and Sales Orders.
Clicking it triggers Acefone's Click-to-Call API:
1. Your registered Acefone Agent Number (your phone/extension) rings first.
2. Once you pick up, Acefone automatically dials the customer and bridges the call.

Works for both Indian and international customer numbers (Acefone handles routing).

Setup required:
- Settings > General Settings > Acefone section: enter API Base URL, Email, Password.
- Each user must set their own "Acefone Agent Number" under their Preferences.
""",
    'author': "Anirath",
    'website': "https://www.augmont.com/",
    'license': 'LGPL-3',
    'depends': ['base', 'mail', 'crm', 'sale'],
    'data': [
        'security/ir.model.access.csv',
        'views/res_config_settings_views.xml',
        'views/res_users_views.xml',
        'views/res_partner_views.xml',
        'views/crm_lead_views.xml',
        # 'views/sale_order_views.xml',  # TEMP DISABLED: blocked by unrelated
        # "translated_product_name" view error in another custom module.
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}

{
    'name': 'Amplitude Analytics Integration',
    'version': '18.0.1.0.0',
    'category': 'Tools',
    'summary': 'Track user activity, navigation, heatmaps, session replay '
               'and Augmont business workflows in Amplitude Analytics.',
    'description': """
Amplitude Analytics Integration
===============================
Two-sided integration with Amplitude:

* Frontend (Browser SDK + Autocapture + Session Replay) loaded into the
  Odoo backend assets -> heatmaps, click tracking, navigation, session replay.
* Backend (HTTP V2 API) -> Augmont business workflow events
  (RFQ, offline order, sale order, QC, stock, purchase, vendor).

A configuration screen exposes the API key, server URL and per-feature
toggles. Only Augmont models that actually exist are instrumented.
""",
    'author': 'SDK Infinity',
    'website': 'https://www.augmont.com/',
    'depends': [
        'web',
        'mail',
        'contact_stage_bar',
        'custom_sale_order',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/amplitude_config_data.xml',
        'views/amplitude_config_views.xml',
        'views/menu_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'amplitude_integration/static/src/js/amplitude_service.js',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}

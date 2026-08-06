# Copyright 2025 Dixmit
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Voip OCA",
    "summary": "Provides the use of Voip",
    "version": "18.0",
    "author": "Dixmit, Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/connector-telephony",
    "license": "AGPL-3",
    "category": "Productivity/VOIP",
    "excludes": ["voip"],
    "depends": ["mail", "contact_stage_bar"],
    "maintainers": ["etobella"],
    "data": [
        "security/ir.model.access.csv",
        "views/res_users.xml",
        "views/voip_call.xml",
        "views/voip_pbx.xml",
        "views/menus.xml",
        "views/res_config_settings.xml",
    ],
    "demo": ["demo/demo_data.xml"],
    "assets": {
        "web.assets_backend": [
            "voip_oca/static/src/**/*",
            # 'voip_oca/static/src/js/phone_call_button.js',
            # 'voip_oca/static/src/xml/phone_field_call_button.xml',
        ],
        "voip_oca.agent_assets": [
            "voip_oca/static/lib/*.js",
        ],
        "web.assets_unit_tests": [
            "voip_oca/static/tests/**/*",
        ],
    },

}

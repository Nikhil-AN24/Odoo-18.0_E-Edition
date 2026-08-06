# -*- coding: utf-8 -*-
#############################################################################
#
#    Techvaria Solutions Pvt. Ltd.
#
#    Copyright (C) 2025-Techvaria Solutions(<https://techvaria.com>)
#    Author: Techvaria Solutions Pvt. Ltd.(info@techvaria.com)
#
#    You can modify it under the terms of the GNU AFFERO
#    GENERAL PUBLIC LICENSE (AGPL v3), Version 3.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU AFFERO GENERAL PUBLIC LICENSE (AGPL v3) for more details.
#
#    You should have received a copy of the GNU AFFERO GENERAL PUBLIC LICENSE
#    (AGPL v3) along with this program.
#    If not, see <http://www.gnu.org/licenses/>.
#
#############################################################################
{
    'name': 'eCommerce Product 360 View',
    'version': '18.0.1.0.0',
    'category': 'eCommerce',
    'author': 'Techvaria',
    'company': 'Techvaria',
    'maintainer': 'Techvaria',
    'website': 'https://techvaria.com',
    'summary': 'Interactive 360° product viewer for eCommerce.',
    'description':
        "Enhance your Odoo eCommerce experience by allowing customers to interact with products in a full 360-degree view. Upload multiple product images in one go and deliver an immersive shopping experience that builds confidence and drives conversions.",
    'depends': [
        'website_sale', 'sale_management',
    ],
    'data': [
        'views/product_template_views.xml',
        'views/website_sale_templates.xml',
    ],
    'assets': {
        'web.assets_frontend': [
            'ts_ecommerce_product_360_view/static/src/**/*',
        ],
    },
    'images': [
        'static/description/screen.jpg',
    ],
    'license': 'LGPL-3',
    'installable': True,
    'auto_install': False,
    'application': False,
}

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

from odoo import fields, models


class ProductTemplate(models.Model):
    """ This class extends the 'product.template' model in Odoo.
        It adds functionality to upload 360 images."""
    _inherit = "product.template"

    show_360_images = fields.Boolean(
        string='Show 360 Images',
    )

    product_images_ids = fields.Many2many(
        'product.image', 'product_template_images_rel',
        'product_template_id', 'image_id',
        string="Product Images",
    )

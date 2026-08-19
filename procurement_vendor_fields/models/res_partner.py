# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.addons.contact_stage_bar.models.purchase_order import (
    DISCOUNT_DIAMOND_GRADE_SELECTION,
    PAYMENT_DIAMOND_GRADE_SELECTION,
)

# Payment term dropdown — separate options 1 day … 90 days (client wants each
# day as its own option). Key = day count (string), label = "N day(s)".
_PAYMENT_TERM_SELECTION = [
    (str(day), '1 day' if day == 1 else '%d days' % day)
    for day in range(1, 91)
]


class ResPartner(models.Model):
    _inherit = 'res.partner'

    vendor_discount = fields.Selection(
        selection=[
            ('less_1', 'Less 1'),
            ('less_2', 'Less 2'),
            ('less_3', 'Less 3'),
            ('less_4', 'Less 4'),
            ('less_5', 'Less 5'),
            ('less_6', 'Less 6'),
        ],
        string='Discount',
    )
    certified_payment_term = fields.Selection(
        selection=_PAYMENT_TERM_SELECTION,
        string='Certified Payment Term',
    )
    non_certified_payment_term = fields.Selection(
        selection=_PAYMENT_TERM_SELECTION,
        string='Non-Certified Payment Term',
    )

    # KYC document (PDF) upload for vendors.
    kyc_document = fields.Binary(string='KYC Document', attachment=True)
    kyc_document_filename = fields.Char(string='KYC Document Filename')

    # ── Melee / Diamonds Discount & Payment Terms (mirror of purchase.order) ──
    discount_type = fields.Selection(
        [('melee', 'Melee'), ('diamonds', 'Diamonds')], string='Discount', prefetch=False)
    discount_melee_ids = fields.Many2many(
        'purchase.melee.value', 'res_partner_discount_melee_rel',
        'partner_id', 'value_id', string='Discount Melee Values', prefetch=False)
    discount_diamond_type = fields.Selection(
        [('labgrown', 'Labgrown'), ('natural', 'Natural')], string='Diamond Type', prefetch=False)
    discount_diamond_grade = fields.Selection(
        DISCOUNT_DIAMOND_GRADE_SELECTION, string='Diamond Grade', prefetch=False)

    augmont_payment_terms = fields.Selection(
        [('melee', 'Melee'), ('diamonds', 'Diamonds')], string='Payment Terms', prefetch=False)
    payment_melee_ids = fields.Many2many(
        'purchase.melee.value', 'res_partner_payment_melee_rel',
        'partner_id', 'value_id', string='Payment Melee Values', prefetch=False)
    payment_diamond_type = fields.Selection(
        [('labgrown', 'Labgrown'), ('natural', 'Natural')], string='Diamond Type', prefetch=False)
    payment_diamond_grade = fields.Selection(
        PAYMENT_DIAMOND_GRADE_SELECTION, string='Diamond Grade', prefetch=False)

    # Clear stale sub-selections when a parent Discount/Payment changes.
    @api.onchange('discount_type')
    def _onchange_discount_type_clear(self):
        for rec in self:
            if rec.discount_type != 'melee':
                rec.discount_melee_ids = [(5, 0, 0)]
            if rec.discount_type != 'diamonds':
                rec.discount_diamond_type = False
                rec.discount_diamond_grade = False

    @api.onchange('discount_diamond_type')
    def _onchange_discount_diamond_type_clear(self):
        for rec in self:
            if not rec.discount_diamond_type:
                rec.discount_diamond_grade = False

    @api.onchange('augmont_payment_terms')
    def _onchange_augmont_payment_terms_clear(self):
        for rec in self:
            if rec.augmont_payment_terms != 'melee':
                rec.payment_melee_ids = [(5, 0, 0)]
            if rec.augmont_payment_terms != 'diamonds':
                rec.payment_diamond_type = False
                rec.payment_diamond_grade = False

    @api.onchange('payment_diamond_type')
    def _onchange_payment_diamond_type_clear(self):
        for rec in self:
            if not rec.payment_diamond_type:
                rec.payment_diamond_grade = False

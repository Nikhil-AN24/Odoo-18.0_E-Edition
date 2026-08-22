# -*- coding: utf-8 -*-
from odoo import fields, models

# Payment term dropdown — separate options 1 day … 90 days (client wants each
# day as its own option). Key = day count (string), label = "N day(s)".
_PAYMENT_TERM_SELECTION = [
    (str(day), '1 day' if day == 1 else '%d days' % day)
    for day in range(1, 91)
]


class ResPartner(models.Model):
    _inherit = 'res.partner'

    # ── Supplier identity (columns from the "seller - seller" import sheet) ──
    # `name` already holds company_name and `vat` the gstin, so only the two
    # extra company names below are new.
    trading_name = fields.Char(string='Trading Name', tracking=True)
    kyc_company_name = fields.Char(string='KYC Company Name', tracking=True)

    # ── Discount / Payment terms matrix (Lab Grown|Natural x Cert|Non Cert) ──
    # The import sheet carries plain numbers (discount 0,1,2… / terms 0,7,10,
    # 15,30 days), so these are Float/Integer rather than the older "Less N"
    # selections above, which stay untouched for existing vendor records.
    lg_cert_discount = fields.Float(
        string='Lab Grown > Cert > Discount', digits=(16, 2), tracking=True)
    lg_non_cert_discount = fields.Float(
        string='Lab Grown > Non Cert > Discount', digits=(16, 2), tracking=True)
    natural_cert_discount = fields.Float(
        string='Natural > Cert > Discount', digits=(16, 2), tracking=True)
    natural_non_cert_discount = fields.Float(
        string='Natural > Non Cert > Discount', digits=(16, 2), tracking=True)

    lg_cert_payment_days = fields.Integer(
        string='Lab Grown > Cert > Payment terms (Days)', tracking=True)
    lg_non_cert_payment_days = fields.Integer(
        string='Lab Grown > Non Cert > Payment terms (Days)', tracking=True)
    natural_cert_payment_days = fields.Integer(
        string='Natural > Cert > Payment terms (Days)', tracking=True)
    natural_non_cert_payment_days = fields.Integer(
        string='Natural > Non Cert > Payment terms (Days)', tracking=True)

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

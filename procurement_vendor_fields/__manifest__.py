{
    'name': 'Procurement Vendor Fields',
    'version': '18.0.1.1.2',
    'category': 'Purchases',
    'summary': 'Vendor-only partner fields plus a dedicated supplier form for '
               'Procurement > Suppliers > Vendors.',
    'description': """
Procurement Vendor Fields
=========================
Adds three selection fields to the partner form, shown only for vendors
(supplier_rank > 0), placed right after the Graduation Rate field:

* Discount (Less 1 … Less 6)
* Certified Payment Term (1 day … 90 days)
* Non-Certified Payment Term (1 day … 90 days)

Also provides a dedicated supplier form (res.partner, mode="primary") wired to
the Procurement > Suppliers > Vendors action, so supplier records are captured
on their own form instead of the buyer form used by Sales > Accounts. Its
fields mirror the supplier import sheet:

* Identity: first/last name, email, mobile number, company name, trading name,
  KYC company name, billing address, shipping address
* GSTIN / GSTIN status, EIN / EIN status
* Discount and Payment terms (days) per Lab Grown|Natural x Cert|Non Cert
""",
    'author': 'SDK Infinity',
    'website': 'https://www.augmont.com/',
    'depends': ['contact_stage_bar'],
    'data': [
        'views/res_partner_views.xml',
        'views/res_partner_supplier_form.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}

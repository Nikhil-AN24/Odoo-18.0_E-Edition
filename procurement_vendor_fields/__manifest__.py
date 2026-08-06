{
    'name': 'Procurement Vendor Fields',
    'version': '18.0.1.0.0', 
    'category': 'Purchases',
    'summary': 'Adds Discount and certified / non-certified payment term '
               'selection fields to the vendor form.',
    'description': """
Procurement Vendor Fields
=========================
Adds three selection fields to the partner form, shown only for vendors
(supplier_rank > 0), placed right after the Graduation Rate field:

* Discount (Less 1 … Less 6)
* Certified Payment Term (1 day … 90 days)
* Non-Certified Payment Term (1 day … 90 days)
""",
    'author': 'SDK Infinity',
    'website': 'https://www.augmont.com/',
    'depends': ['contact_stage_bar'],
    'data': [
        'views/res_partner_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}

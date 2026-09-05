from odoo import models, _,api ,fields
from markupsafe import Markup
from datetime import datetime, timedelta
import datetime


# Vendor-master matrix field names, keyed by (stone_type, certification).
# See procurement_vendor_fields/models/res_partner.py for the 4x4 matrix.
_VENDOR_TERMS_FIELD_MAP = {
    ('lab_grown', 'certified'):     ('lg_cert_discount', 'lg_cert_payment_days'),
    ('lab_grown', 'non_certified'): ('lg_non_cert_discount', 'lg_non_cert_payment_days'),
    ('natural', 'certified'):       ('natural_cert_discount', 'natural_cert_payment_days'),
    ('natural', 'non_certified'):   ('natural_non_cert_discount', 'natural_non_cert_payment_days'),
}


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'


    product_display = fields.Many2one(
        comodel_name='product.product',
        related='product_id',
        string='Product',
        store=False,
        readonly=True,
    )
    
    weight = fields.Float(string="Weight")
    bank_rate = fields.Float(string="Bank Rate", digits=(16, 2))

    shapes = fields.Char(string="Shape", related='product_id.product_tmpl_id.shapes')
    color = fields.Char(string="Colour", related='product_id.product_tmpl_id.color')
    cut = fields.Char(string="Cut", related='product_id.product_tmpl_id.cut')
    clarity = fields.Char(string="Clarity", related='product_id.product_tmpl_id.clarity')
    carat_weight = fields.Char(string="Carat", related='product_id.product_tmpl_id.weight_carat')

    # "Discount" column — Selection field, allows 1% to 10% only
    custom_discount = fields.Selection(
        selection=[
            ('1', '1%'), ('2', '2%'), ('3', '3%'), ('4', '4%'), ('5', '5%'),
            ('6', '6%'), ('7', '7%'), ('8', '8%'), ('9', '9%'), ('10', '10%'),
        ],
        string='Discount',
    )

    # "Rate" column — Monetary field with $ symbol by default
    rate_usd = fields.Monetary(
        string='Rate',
        currency_field='currency_id',
    )

    # Bank Rate * Unit Price = Rupees Rate
    rupees_rate = fields.Float(
        string='Rupees Rate',
        compute='_compute_rupees_rate',
        digits=(16, 2),
        store=True,
    )

    @api.depends('bank_rate', 'price_unit')
    def _compute_rupees_rate(self):
        for line in self:
            line.rupees_rate = line.bank_rate * line.price_unit

    # ── Vendor discount / payment terms (PRD §5.5/§5.6) ─────────────────
    # Procurement classifies the stone; discount % and payment days are then
    # derived from the vendor master's 4x4 matrix and snapshotted onto the
    # line — never typed, never re-read live from the vendor afterwards.
    stone_type = fields.Selection(
        [('lab_grown', 'Lab Grown'), ('natural', 'Natural')],
        string='Stone Type',
    )
    stone_certification_type = fields.Selection(
        [('certified', 'Certified'), ('non_certified', 'Non-Certified')],
        string='Certification',
    )
    discount_percent = fields.Float(
        string='Vendor Discount %', digits=(16, 2),
        readonly=True, copy=False,
        help="Derived automatically from the vendor's discount matrix. "
             "Procurement never types this value directly.",
    )
    payment_days = fields.Integer(
        string='Payment Terms (Days)',
        readonly=True, copy=False,
        help="Derived automatically from the vendor's payment-terms matrix.",
    )
    expected_net = fields.Monetary(
        string='Expected Net', currency_field='currency_id',
        compute='_compute_expected_net', store=True, readonly=True,
        help="Gross x (1 - discount%). What Accounting should expect to pay.",
    )

    @api.depends('price_unit', 'product_qty', 'discount_percent')
    def _compute_expected_net(self):
        for line in self:
            gross = line.price_unit * line.product_qty
            line.expected_net = gross * (1 - (line.discount_percent or 0.0) / 100.0)

    def _get_vendor_terms(self, partner):
        """Look up (discount %, payment days) on the vendor master for this
        line's stone classification. Missing vendor terms resolve to
        (0.0, 0) rather than raising, per PRD REQ-5.4.3."""
        self.ensure_one()
        key = (self.stone_type, self.stone_certification_type)
        field_names = _VENDOR_TERMS_FIELD_MAP.get(key)
        if not partner or not field_names:
            return 0.0, 0
        discount_field, days_field = field_names
        return partner[discount_field] or 0.0, partner[days_field] or 0

    @api.onchange('stone_type', 'stone_certification_type')
    def _onchange_stone_classification_fill_vendor_terms(self):
        for line in self:
            if not line.stone_type or not line.stone_certification_type:
                continue
            discount, days = line._get_vendor_terms(line.order_id.partner_id)
            line.discount_percent = discount
            line.payment_days = days


    @api.depends('weight', 'rupees_rate', 'product_qty', 'price_unit', 'taxes_id', 'discount')
    def _compute_amount(self):
        super(PurchaseOrderLine, self)._compute_amount()
        
        for line in self:
            if line.weight or line.rupees_rate:
                custom_total = line.weight * line.rupees_rate
                line.price_subtotal = custom_total

                if line.taxes_id:
                    taxes = line.taxes_id.compute_all(
                        custom_total, 
                        line.order_id.currency_id, 
                        1.0, 
                        product=line.product_id, 
                        partner=line.order_id.partner_id
                    )
                    line.price_tax = taxes['total_included'] - taxes['total_excluded']
                    line.price_total = taxes['total_included']
                else:
                    line.price_total = custom_total
                    line.price_tax = 0.0

    def _prepare_base_line_for_taxes_computation(self, **kwargs):
        res = super(PurchaseOrderLine, self)._prepare_base_line_for_taxes_computation(**kwargs)
        if self.weight or self.rupees_rate:
            custom_total = self.weight * self.rupees_rate
            res.update({
                'price_unit': custom_total,
                'quantity': 1.0,
                'discount': 0.0,
                'price_subtotal': custom_total,
            })
        return res

class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'
    location = fields.Selection([('mumbai', 'India'), ('surat', 'USA')], string='Location')     
    vendor_street = fields.Char(related='partner_id.street', string="Street")
    vendor_street_2 = fields.Char(related='partner_id.street2', string="Street 2")
    vendor_city = fields.Char(related='partner_id.city', string="City")
    vendor_state = fields.Many2one(related='partner_id.state_id', string="State")
    vendor_zip = fields.Char(related='partner_id.zip', string="ZIP")
    vendor_country = fields.Many2one(related='partner_id.country_id', string="Country")
    phone_number = fields.Char(related='partner_id.phone', string="Phone")
    email = fields.Char(related='partner_id.email', string="Email")
    order_number = fields.Char(string="Order Number")
    total_weight = fields.Float(string="Total Weight")

    @api.onchange('partner_id')
    def _onchange_partner_id_fill_vendor_defaults(self):

        for order in self:
            if not order.partner_id:
                continue
            partner = order.partner_id

            # Location: India -> mumbai, anything else -> surat.
            order.location = 'mumbai' if partner.country_id.code == 'IN' else 'surat'

            # Re-derive discount/payment terms for lines already classified
            # by stone type + certification (PRD REQ-5.6.1). Terms are
            # snapshotted onto the line at this point, not referenced live —
            # renegotiating the vendor later does not affect this PO.
            for line in order.order_line:
                line._onchange_stone_classification_fill_vendor_terms()

    procurement_reference = fields.Char(
        string="Procurement Reference",
        compute="_compute_procurement_reference",
        store=False
    )


    @api.depends('origin')
    def _compute_procurement_reference(self):
        """
        Get the sdk_augmont_number from related Sale Order.
        Handles comma-separated origins when multiple SOs are merged into one PO.
        """
        for rec in self:
            sdk_number = False
            if rec.origin:
                # Origin can be comma-separated when multiple SOs merged into one PO
                origin_names = [o.strip() for o in rec.origin.split(',') if o.strip()]
                sale_orders = self.env['sale.order'].search([('name', 'in', origin_names)])
                if sale_orders:
                    sdk_numbers = [so.sdk_augmont_number for so in sale_orders if so.sdk_augmont_number]
                    sdk_number = ', '.join(sdk_numbers) if sdk_numbers else False
            rec.procurement_reference = sdk_number
    
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('origin'):
                # Origin can be comma-separated when multiple SOs merged into one PO
                origin_names = [o.strip() for o in vals['origin'].split(',') if o.strip()]
                sale_order = self.env['sale.order'].search([('name', 'in', origin_names)], limit=1)
                if sale_order:
                    vals.update({
                        'date_order': sale_order.date_order,
                        'location': sale_order.location,
                        'order_number': sale_order.sdk_augmont_number,
                    })
        return super().create(vals_list)


    def write(self, vals):
        for po in self:
            if vals.get('origin') or po.origin:
                origin = vals.get('origin', po.origin)
                # Origin can be comma-separated when multiple SOs merged into one PO
                origin_names = [o.strip() for o in origin.split(',') if o.strip()]
                sale_order = self.env['sale.order'].search([('name', 'in', origin_names)], limit=1)
                if sale_order:
                    vals.update({
                        'date_order': sale_order.date_order,
                        'location': sale_order.location,
                    })
                    
                    
            if 'state' in vals and vals['state'] == 'done':
                for order in self:
                    activities = self.env['mail.activity'].search([
                        ('res_model', '=', 'purchase.order'),
                        ('res_id', '=', order.id),
                        ('activity_type_id', '=', self.env.ref('mail.mail_activity_data_todo').id),
                    ])
                    for act in activities:
                        act.action_done()
                        
        return super().write(vals)
    
    
    def button_confirm(self):
        import datetime as _dt
        import logging as _log
        _po_logger = _log.getLogger(__name__)
        for order in self:
            _po_logger.info(
                "=" * 70 + "\n"
                "📦 [PO CONFIRM] button_confirm called | %s | PO: %s | Origin(SO): %s | "
                "Vendor: %s | Lines: %d",
                _dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                order.name,
                order.origin or 'N/A',
                order.partner_id.name if order.partner_id else 'N/A',
                len(order.order_line),
            )
        
        res = super(PurchaseOrder, self).button_confirm()


        for order in self:
            # Collect products
            product_names = ", ".join(order.order_line.mapped("product_id.display_name"))
            subject = _("Purchase Order %s created — follow up on material inward process") % order.name
            body_html = Markup("""
                <p>Dear Logistics Team,</p>
                <p>The Purchase Order <strong>%s</strong> has been confirmed.</p>
                <p>Please follow up on the material inward process.</p>
                <p><strong>Products:</strong> %s</p>
            """ % (order.name, product_names))


            email_from = self.env.user.partner_id.email


            # Get Logistics group
            group = self.env.ref("__export__.res_groups_83_454cce4b", raise_if_not_found=False)


            recipients = []
            partners = []
            if group:
                recipients = group.users.mapped("partner_id.email")
                recipients = [email for email in recipients if email]
                partners = group.users.mapped("partner_id")


            # Create email
            if recipients:
                mail_values = {
                    'subject': subject,
                    'body_html': body_html,
                    'email_from': email_from,
                    'email_to': ",".join(recipients),
                }
                self.env['mail.mail'].create(mail_values).send()

            # Attach notifications and activities to the created pickings
            activity_type = self.env.ref("mail.mail_activity_data_todo")

            for picking in order.picking_ids:  # loop through related receipts
                # Post to chatter
                picking.message_post(
                    subject=subject,
                    body=body_html,
                    message_type='notification'
                )

                # Create activity for each partner in group
                for partner in partners:
                    self.env['mail.activity'].create({
                        'res_model_id': self.env['ir.model']._get_id('stock.picking'),
                        'res_id': picking.id,
                        'activity_type_id': activity_type.id,
                        'summary': "Follow up on material inward process",
                        'note': body_html,
                        'user_id': partner.user_ids[:1].id if partner.user_ids else False,
                        'date_deadline': fields.Date.today(),
                    })

        return res
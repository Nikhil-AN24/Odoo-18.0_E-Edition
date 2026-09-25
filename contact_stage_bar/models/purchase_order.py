from odoo import models, _,api ,fields
from odoo.exceptions import UserError
from markupsafe import Markup, escape
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


    rate_usd = fields.Monetary(
        string='Procurement Price/carat',
        currency_field='source_currency_id',
    )

    source_currency_id = fields.Many2one(
        'res.currency', string='Rate Currency',
        compute='_compute_source_currency_id', store=True, readonly=False)
    # Fixed INR currency for the amounts that must ALWAYS show ₹ (Expected Net,
    # and via the header, GST / Total) irrespective of the rate's own currency.
    inr_currency_id = fields.Many2one(
        'res.currency', string='INR Currency',
        compute='_compute_inr_currency_id')

    @api.depends('sale_line_id.currency_id', 'currency_id')
    def _compute_source_currency_id(self):
        for line in self:
            line.source_currency_id = line.sale_line_id.currency_id or line.currency_id

    def _compute_inr_currency_id(self):
        inr = self.env.ref('base.INR', raise_if_not_found=False) \
            or self.env['res.currency'].search([('name', '=', 'INR')], limit=1)
        for line in self:
            line.inr_currency_id = inr

    # True only for the roles allowed to edit the PO pricing columns
    # (Vendor Discount %, Payment Terms, Per ct. Rate, Discount). For everyone
    # else the whole Products grid is view-only. Defaults to False, so an
    # un-privileged (or unresolved) user always gets the read-only columns.
    can_edit_po_pricing = fields.Boolean(compute='_compute_can_edit_po_pricing')
    # Plain LGD Procurement may additionally edit "Per ct. Rate" (only).
    can_edit_rate = fields.Boolean(compute='_compute_can_edit_po_pricing')
    currency_name = fields.Char(related='currency_id.name')

    @api.depends_context('uid')
    def _compute_can_edit_po_pricing(self):
        user = self.env.user
        priv = (
            user.has_group('base.group_system')
            or user.has_group('contact_stage_bar.group_lgd_superadmin')
            or user.has_group('contact_stage_bar.group_lgd_procurement_manager')
            or user.has_group('contact_stage_bar.group_lgd_accounting')
        )
        can_rate = priv or user.has_group('contact_stage_bar.group_lgd_procurement')
        for line in self:
            line.can_edit_po_pricing = priv
            line.can_edit_rate = can_rate

    def action_open_line_info(self):
        """(i) button on the PO Products list. Show the SAME General Information
        / Purchase details Procurement sees on the Sale Order"""
        self.ensure_one()
        popup = self.env.ref(
            'custom_sale_order.view_po_line_info_popup', raise_if_not_found=False)
        if self.sale_line_id and popup:
            return {
                'type': 'ir.actions.act_window',
                'name': _('Product'),
                'res_model': 'sale.order.line',
                'res_id': self.sale_line_id.id,
                'view_mode': 'form',
                'views': [(popup.id, 'form')],
                'target': 'new',
            }
        return {
            'type': 'ir.actions.act_window',
            'name': _('Line Information'),
            'res_model': 'purchase.order.line',
            'res_id': self.id,
            'view_mode': 'form',
            'views': [(self.env.ref(
                'contact_stage_bar.view_purchase_order_line_info_form').id, 'form')],
            'target': 'new',
        }

    # Rupees Rate — entered by Procurement; drives the line total (with Weight).
    rupees_rate = fields.Float(
        string='Rupees Rate',
        digits=(16, 2),
    )

    # ── Vendor discount / payment terms ─────────────────
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
    per_ct_discounted_rate = fields.Monetary(
        string='Per ct. Discounted Rate', currency_field='source_currency_id',
        compute='_compute_per_ct_discounted_rate', store=True, readonly=True,
        help="Procurement Price/carat less the vendor discount %. "
             "e.g. 100 - 1% = 99. Shown in the SO/RFQ currency.",
    )
    # Numeric carat weight of the stone (parsed on the product), used to turn the
    # per-carat discounted rate into the line's expected net.
    carat_value = fields.Float(
        string='Carat', related='product_id.product_tmpl_id.carat_value')
    expected_net = fields.Monetary(
        string='Expected Net', currency_field='inr_currency_id',
        compute='_compute_expected_net', store=True, readonly=True,
        help="Per ct. Discounted Rate x Carat. Always shown in INR.",
    )

    @api.depends('rate_usd', 'discount_percent')
    def _compute_per_ct_discounted_rate(self):
        for line in self:
            line.per_ct_discounted_rate = (line.rate_usd or 0.0) * (
                1 - (line.discount_percent or 0.0) / 100.0)

    @api.depends('per_ct_discounted_rate', 'carat_value',
                 'order_id.currency_id', 'order_id.bank_rate')
    def _compute_expected_net(self):
        """Expected Net = Per ct. Discounted Rate × Carat, in INR.
        When the PO Currency is USD and a Bank Rate is entered, the per-carat
        figures are in USD, so multiply by the Bank Rate to land in INR:
        Expected Net = Per ct. Discounted Rate × Carat × Bank Rate. The Bank
        Rate is applied HERE only — never to the Procurement Price/carat."""
        for line in self:
            order = line.order_id
            net = line.per_ct_discounted_rate * (line.carat_value or 0.0)
            is_usd = bool(order.currency_id and order.currency_id.name == 'USD')
            if is_usd and order.bank_rate:
                net *= order.bank_rate
            line.expected_net = net

    def _get_vendor_terms(self, partner):
        """Look up (discount %, payment days) on the vendor master for this
        line's stone classification. Missing vendor terms resolve to
        (0.0, 0) rather than raising."""
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

    # Price / monetary columns whose manual edits are logged on the PO chatter.
    _TRACKED_MONETARY_FIELDS = {
        'price_unit': 'Unit Price',
        'rate_usd': 'Procurement Price/carat',
        'rupees_rate': 'Rupees Rate',
        'weight': 'Weight',
        'product_qty': 'Quantity',
        'custom_discount': 'Discount',
        'discount_percent': 'Vendor Discount %',
        'payment_days': 'Payment Terms (Days)',
    }

    @staticmethod
    def _fmt_tracked_value(value):
        if isinstance(value, float):
            return '%.2f' % value
        return '' if value in (False, None) else str(value)

    def write(self, vals):
        """Log any price/monetary column change to the parent PO's chatter, so a
        manual edit by anyone is recorded (who + old → new)."""
        tracked = [f for f in self._TRACKED_MONETARY_FIELDS if f in vals]
        old = ({line.id: {f: line[f] for f in tracked} for line in self}
               if tracked else {})
        res = super().write(vals)
        if tracked:
            for line in self:
                rows = []
                for f in tracked:
                    before = old.get(line.id, {}).get(f)
                    after = line[f]
                    if before != after:
                        rows.append((self._TRACKED_MONETARY_FIELDS[f], before, after))
                if rows and line.order_id:
                    product = escape(line.product_id.display_name or line.name or _('Line'))
                    items = Markup('').join(
                        Markup('<li>%s: %s → %s</li>') % (
                            lbl, self._fmt_tracked_value(b), self._fmt_tracked_value(a))
                        for lbl, b, a in rows
                    )
                    # _lgd_log, not message_post: an unconfigured mail
                    # sender would otherwise raise here and roll back the very
                    # edit we are trying to record, making the pricing columns
                    # look permanently read-only.
                    line.order_id._lgd_log(
                        Markup('<p><b>%s</b> — price/amount changed:</p><ul>%s</ul>')
                        % (product, items))
        return res


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    # Header-level Bank Rate — shown under Currency, only when the PO Currency
    # is USD. It feeds the Expected Net conversion (per-carat USD × Bank Rate →
    # INR) and NOTHING else; it never rewrites the Procurement Price/carat.
    # currency_name drives the USD-only visibility.
    currency_name = fields.Char(related='currency_id.name')
    bank_rate = fields.Float(string="Bank Rate", digits=(16, 2), tracking=True)

    # Header mirror of the line-level flag. The Products grid (order_line) stays
    # editable ONLY for the privileged roles (Admin / LGD Procurement Manager /
    # LGD SuperAdmin), regardless of PO state; everyone else sees it view-only.
    can_edit_po_pricing = fields.Boolean(compute='_compute_can_edit_po_pricing')
    can_edit_rate = fields.Boolean(compute='_compute_can_edit_po_pricing')

    @api.depends_context('uid')
    def _compute_can_edit_po_pricing(self):
        user = self.env.user
        priv = (
            user.has_group('base.group_system')
            or user.has_group('contact_stage_bar.group_lgd_superadmin')
            or user.has_group('contact_stage_bar.group_lgd_procurement_manager')
            or user.has_group('contact_stage_bar.group_lgd_accounting')
        )
        can_rate = priv or user.has_group('contact_stage_bar.group_lgd_procurement')
        for order in self:
            order.can_edit_po_pricing = priv
            order.can_edit_rate = can_rate

    # Log currency and amount changes on the PO chatter.
    currency_id = fields.Many2one(tracking=True)
    amount_untaxed = fields.Monetary(tracking=True)
    amount_tax = fields.Monetary(tracking=True)
    amount_total = fields.Monetary(tracking=True)

    # Fixed INR currency so the footer totals below always show ₹, regardless of
    # the currency the per-carat rate is displayed in.
    inr_currency_id = fields.Many2one(
        'res.currency', string='INR Currency',
        compute='_compute_inr_currency_id')

    def _compute_inr_currency_id(self):
        inr = self.env.ref('base.INR', raise_if_not_found=False) \
            or self.env['res.currency'].search([('name', '=', 'INR')], limit=1)
        for order in self:
            order.inr_currency_id = inr

    # PO totals shown to Procurement: "Expected Net" (sum of each line's
    # net-of-vendor-discount cost) = untaxed, a flat 1.5% GST, and the Total.
    # These replace the standard tax_totals widget in the form footer, and are
    # mandatorily in INR (inr_currency_id) irrespective of the rate's currency.
    expected_net_total = fields.Monetary(
        string="Expected Net", compute='_compute_expected_net_totals',
        currency_field='inr_currency_id')
    gst_amount = fields.Monetary(
        string="GST +1.5%", compute='_compute_expected_net_totals',
        currency_field='inr_currency_id')
    grand_total = fields.Monetary(
        string="Total", compute='_compute_expected_net_totals',
        currency_field='inr_currency_id')

    @api.depends('order_line.expected_net')
    def _compute_expected_net_totals(self):
        for order in self:
            net = sum(order.order_line.mapped('expected_net'))
            order.expected_net_total = net
            order.gst_amount = net * 0.015
            order.grand_total = net * 1.015
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
            # by stone type + certification. Terms are
            # snapshotted onto the line at this point, not referenced live —
            # renegotiating the vendor later does not affect this PO.
            for line in order.order_line:
                line._onchange_stone_classification_fill_vendor_terms()

    @api.depends('partner_id')
    def _compute_currency_id(self):
        """Default vendor POs to INR. Augmont trades in INR even though the
        company currency is USD, so fall back to INR (not the company currency)
        when the vendor has no purchase currency of its own. A vendor's own
        purchase currency still wins if one is set, and the field stays
        user-editable while the PO is open."""
        inr = self.env.ref('base.INR', raise_if_not_found=False)
        for order in self:
            company = order.company_id or self.env.company
            vendor_cur = order.partner_id.with_company(company).property_purchase_currency_id \
                if order.partner_id else False
            order.currency_id = vendor_cur or inr or company.currency_id

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
        missing_rate = self.filtered(
            lambda o: o.currency_id.name == 'USD' and not o.bank_rate)
        if missing_rate:
            raise UserError(_(
                "Enter the Bank Rate before confirming a USD purchase order:\n%s"
            ) % "\n".join("- %s" % o.name for o in missing_rate))

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
from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
import logging

_logger = logging.getLogger(__name__)

class CustomerRfqShape(models.Model):
    _name = 'customer.rfq.shape'
    _description = 'Customer RFQ Shape Option'
    _order = 'sequence, name'

    name = fields.Char(string='Shape Name', required=True, translate=True)
    sequence = fields.Integer(string='Sequence', default=10)

    _sql_constraints = [
        ('name_unique', 'UNIQUE(name)', 'Shape name must be unique.'),
    ]

class CustomerRfq(models.Model):
    _name = 'customer.rfq'
    _description = 'Customer RFQ'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(
        string='Reference',
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: _('New'),
    )

    partner_id = fields.Many2one(
        'res.partner', string='Customer Name', tracking=True, required=True,
    )

    # Owner: defaults to whoever creates the record, but unlike create_uid
    # this is a normal editable field so the record can be reassigned.
    owner_id = fields.Many2one(
        'res.users', string='Owner', tracking=True,
        default=lambda self: self.env.uid,
    )

    customer_phone = fields.Char(
        string='Customer Phone',
        related='partner_id.phone',
        readonly=False,
        tracking=True,
    )
    customer_email = fields.Char(
        string='Customer Email',
        related='partner_id.email',
        readonly=False,
        tracking=True,
    )
    customer_address = fields.Char(
        string='Customer Address',
        related='partner_id.street',
        readonly=False,
        tracking=True,
    )

    stone_type = fields.Selection(
        [('natural', 'Natural'), ('lab_grown', 'Lab grown')],
        string='Type of Stone',
        tracking=True,
    )
    stone_certification_type = fields.Selection(
        [('certified', 'Certified'), ('non_certified', 'Non-Certified')],
        string='Certification',
        tracking=True,
    )

    shape_ids = fields.Many2many(
        'customer.rfq.shape',
        'customer_rfq_shape_rel',   
        'rfq_id',                   
        'shape_id',                
        string='Shape',
        tracking=True,
    )

    carat = fields.Float(string='Carat', tracking=True)
    color = fields.Char(string='Color', tracking=True)
    clarity = fields.Char(string='Clarity', tracking=True)
    size = fields.Char(string='Size', tracking=True)
    quantity = fields.Float(string='Quantity', tracking=True)

    currency_id = fields.Many2one('res.currency', string='Currency', default=lambda self: self.env.ref('base.USD').id)
    price = fields.Float(string='Price/carat', tracking=True)
    is_price_visible_for_user = fields.Boolean(
        string='Price Visible',
        compute='_compute_is_price_visible_for_user',
    )
    is_price_readonly = fields.Boolean(
        string='Price Readonly',
        compute='_compute_is_price_readonly',
    )
    notes = fields.Text(string='Notes')

    # Procurement (or Admin) can optionally select applicable taxes here.
    tax_ids = fields.Many2many(
        comodel_name='account.tax',
        relation='customer_rfq_tax_rel',
        column1='rfq_id',
        column2='tax_id',
        string='Taxes',
        domain=[('type_tax_use', 'in', ['sale', 'all'])],
        help='Select the taxes that apply to this RFQ.  '
             'Tax amounts are calculated on the base price (Price/carat × Carat).',
    )

    # ── Computed pricing fields ───────────────────────────────────────────────
    # base_price         = Price/carat × Carat
    # margin_percentage  = Looked up from the Carat–Margin Table (lgd.margin)
    # margin_amount      = base_price × (margin_percentage / 100)
    # tax_amount         = (base_price + margin_amount) × combined tax rate
    # total_price        = base_price + margin_amount + tax_amount
    #
    # All four are stored=False (computed on-the-fly) so they always reflect
    # the latest Price/carat, Carat, Margin Table, and Tax configuration.

    base_price = fields.Monetary(
        string='Base Price',
        currency_field='currency_id',
        compute='_compute_pricing_totals',
        store=False,
        help='Price/carat × Carat',
    )

    margin_percentage = fields.Float(
        string='Margin %',
        digits=(5, 2),
        compute='_compute_pricing_totals',
        store=False,
        help="Margin percentage fetched from the Carat–Margin Table for this record's Carat value.",
    )

    margin_amount = fields.Monetary(
        string='Margin Amount',
        currency_field='currency_id',
        compute='_compute_pricing_totals',
        store=False,
        help='Base Price × Margin %',
    )

    tax_amount = fields.Monetary(
        string='Tax Amount',
        currency_field='currency_id',
        compute='_compute_pricing_totals',
        store=False,
        help='Tax computed on (Base Price + Margin Amount).',
    )

    total_price = fields.Monetary(
        string='Total',
        currency_field='currency_id',
        compute='_compute_pricing_totals',
        store=False,
        help='(Price/carat × Carat)  +  Margin Amount  +  Tax Amount',
    )

    @staticmethod
    def _carat_to_band(carat):
        """Return the lgd.margin.line carat_range selection key for *carat*.

        Bands mirror LgdMarginLine.CARAT_RANGE_SELECTION:
            1.00 – 1.49  → '1_to_1_49'
            1.50 – 1.99  → '1_5_to_1_99'
            2.00 – 2.49  → '2_to_2_49'
            2.50 – 2.99  → '2_5_to_2_99'
            3.00 – 3.99  → '3_to_3_99'
            4.00 – 4.99  → '4_to_4_99'
            5.00 – 5.99  → '5_to_5_99'
            6.00 – 6.99  → '6_to_6_99'
            7.00+        → '7_plus'

        Returns None when the carat value falls outside all defined bands.
        """
        if carat < 1.0:
            return None
        if carat < 1.5:
            return '1_to_1_49'
        if carat < 2.0:
            return '1_5_to_1_99'
        if carat < 2.5:
            return '2_to_2_49'
        if carat < 3.0:
            return '2_5_to_2_99'
        if carat < 4.0:
            return '3_to_3_99'
        if carat < 5.0:
            return '4_to_4_99'
        if carat < 6.0:
            return '5_to_5_99'
        if carat < 7.0:
            return '6_to_6_99'
        return '7_plus'

    @api.depends('price', 'carat', 'tax_ids')
    def _compute_pricing_totals(self):
        """Compute the full pricing breakdown for each RFQ record.
        Formula
        -------
            base_price        = price  (Price/carat)  ×  carat
            margin_percentage = looked up from lgd.margin singleton via carat band
            margin_amount     = base_price × (margin_percentage / 100)
            taxable_amount    = base_price + margin_amount
            tax_amount        = taxable_amount × combined-tax-rate
                                (uses account.tax.compute_all for accuracy)
            total_price       = taxable_amount + tax_amount

        The Carat–Margin Table (lgd.margin) is fetched once per batch via sudo()
        so that users who lack read access to lgd.margin (e.g. pure Sales users)
        still see the correct margin figure after Procurement sends the RFQ back.
        """
        # ── Fetch the singleton Margin Config once for the whole batch ──────
        MarginLine = self.env['lgd.margin.line'].sudo()
        margin_singleton = self.env['lgd.margin'].sudo().search([], limit=1)
        margin_singleton_id = margin_singleton.id if margin_singleton else False

        for rec in self:
            # ── 1. Base price ────────────────────────────────────────────────
            rec_base_price = rec.price * rec.carat

            # ── 2. Margin percentage from Carat–Margin Table ─────────────────
            band = self._carat_to_band(rec.carat) if rec.carat else None
            rec_margin_pct = 0.0
            if band and margin_singleton_id:
                margin_line = MarginLine.search([
                    ('margin_id',   '=', margin_singleton_id),
                    ('carat_range', '=', band),
                ], limit=1)
                rec_margin_pct = margin_line.percentage if margin_line else 0.0

            # ── 3. Margin amount ─────────────────────────────────────────────
            rec_margin_amount = rec_base_price * (rec_margin_pct / 100.0)

            # ── 4. Tax amount (using Odoo's compute_all for multi-tax support) ─
            taxable_amount = rec_base_price + rec_margin_amount
            rec_tax_amount = 0.0
            if rec.tax_ids and taxable_amount:
                # compute_all returns a dict with 'taxes' list & 'total_included'
                tax_result = rec.tax_ids.compute_all(
                    price_unit=taxable_amount,
                    currency=rec.currency_id,
                    quantity=1.0,
                    partner=rec.partner_id or False,
                )
                # Sum the individual tax amounts from all tax lines
                rec_tax_amount = sum(t['amount'] for t in tax_result.get('taxes', []))

            # ── 5. Assign back ───────────────────────────────────────────────
            rec.base_price        = rec_base_price
            rec.margin_percentage = rec_margin_pct
            rec.margin_amount     = rec_margin_amount
            rec.tax_amount        = rec_tax_amount
            rec.total_price       = taxable_amount + rec_tax_amount

    @api.depends_context('uid')
    def _compute_is_price_visible_for_user(self):
        """Visibility rules:
        - Procurement or Admin: always visible
        - Sales: visible only once Procurement has sent the RFQ back (state == 'sent_back_to_sales')
        """
        user = self.env.user
        is_procurement = user.has_group('contact_stage_bar.group_lgd_procurement')
        is_admin = user.has_group('base.group_system')
        for rec in self:
            if is_procurement or is_admin:
                rec.is_price_visible_for_user = True
            else:
                rec.is_price_visible_for_user = (rec.state == 'sent_back_to_sales')

    @api.depends_context('uid')
    def _compute_is_price_readonly(self):
        """Price is editable for Procurement and Admin at any time, regardless of
        which menu they used to open the record.  Sales always sees it readonly."""
        user = self.env.user
        is_procurement = user.has_group('contact_stage_bar.group_lgd_procurement')
        is_admin = user.has_group('base.group_system')
        # Procurement and Admin can always edit the price; Sales cannot.
        editable = is_procurement or is_admin
        for rec in self:
            rec.is_price_readonly = not editable

    availability_status = fields.Selection(
        [
            ('diamond_booked', 'Diamond Booked'),
            ('confirmed', 'Confirmed'),
            ('not_available', 'Not available'),
            ('cancelled', 'Cancelled'),
            ('in_qc_process', 'In QC process'),
            ('qc_fail', 'QC Fail'),
            ('payment_pending', 'Payment Pending'),
            ('payment_completed', 'Payment Completed'),
            ('dispatched', 'Dispatched'),
            ('return_of_order', 'Return of Order'),
            ('re_dispatched', 'Re - Dispatched'),
            ('delivered', 'Delivered'),
            ('order_completed', 'Order Completed'),
        ],
        string='Availability',
        copy=False,
        required=True,
        default='diamond_booked',
        tracking=True,
    )

    state = fields.Selection(
        [
            ('draft', 'Draft'),
            ('sent_to_procurement', 'Sent to Procurement'),
            ('sent_back_to_sales', 'Sent back to Sales'),
            ('offline_order_created', 'Offline Order Created'),
        ],
        string='Status',
        default='draft',
        required=True,
        tracking=True,
    )

    # Set by Procurement to mark whether the stone needs certification.
    stone_certification = fields.Selection(
        [('yes', 'Yes'), ('no', 'No')],
        string='Stone Certification',
        tracking=True,
    )


    @api.constrains('shape_ids')
    def _check_shape_required(self):
        for rec in self:
            if not rec.shape_ids:
                raise ValidationError(_(
                    "The 'Shape' field is mandatory. "
                    "Please select at least one shape before saving."
                ))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('customer.rfq') or _('New')
        return super(CustomerRfq, self.sudo()).create(vals_list)

    def write(self, vals):
        return super(CustomerRfq, self.sudo()).write(vals)

    def action_send_to_procurement(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("Only draft Customer RFQs can be sent to Procurement."))
            rec.sudo().write({'state': 'sent_to_procurement'})
            rec.sudo().message_post(body=_("Sent to Procurement team."))

    def action_send_back_to_sales(self):
        for rec in self:
            if rec.state != 'sent_to_procurement':
                raise UserError(_("Only RFQs sent to Procurement can be sent back to Sales."))
            # Enforce that Procurement must enter a price before sending back.
            if not rec.price or rec.price <= 0:
                raise UserError(_(
                    "Please enter a Price before sending back to Sales. "
                    "Sales cannot see pricing until a valid amount is set."
                ))
            rec.state = 'sent_back_to_sales'
            rec.message_post(body=_(
                "Sent back to Sales team with price: %s | Total: %s"
            ) % (rec.price, rec.total_price))

    def action_create_offline_order(self):
        self.ensure_one()
        if self.state == 'offline_order_created':
            raise UserError(_("An Offline Order has already been created for this RFQ."))
        if self.state != 'sent_back_to_sales':
            raise UserError(_("Offline Order can be created only after Procurement sends the RFQ back."))
        if not self.partner_id:
            raise UserError(_("Customer is required to create an Offline Order."))

        description = self.name
        if self.size:
            description = _("%s - Size: %s") % (self.name, self.size)

        order = self.env['custom.sale.order'].create({
            'partner_id': self.partner_id.id,
            'state': 'draft',
            'customer_rfq_id': self.id,
            'order_line': [(0, 0, {
                'name': description,
                'shapes': ', '.join(self.shape_ids.mapped('name')),
                'color': self.color,
                'clarity': self.clarity,
                'carat_weight': str(self.carat) if self.carat else False,
                'product_uom_qty': 1,
                'price_unit': self.total_price if self.total_price else self.price,
            })],
        })
        self.message_post(body=_("Offline Order %s created.") % order.name)
        # Advance the RFQ so the "Create Offline Order" button hides and a second
        # offline order can't be created from the same RFQ.
        self.state = 'offline_order_created'

        return {
            'type': 'ir.actions.act_window',
            'name': _('Offline Order'),
            'res_model': 'custom.sale.order',
            'res_id': order.id,
            'view_mode': 'form',
            'target': 'current',
        }
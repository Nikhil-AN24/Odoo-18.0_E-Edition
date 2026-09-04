from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from markupsafe import Markup
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


class CustomerRfqCancelReason(models.Model):
    _name = 'customer.rfq.cancel.reason'
    _description = 'Customer RFQ Cancellation Reason'
    _order = 'sequence, name'

    name = fields.Char(string='Reason', required=True, translate=True)
    sequence = fields.Integer(string='Sequence', default=10)
    active = fields.Boolean(string='Active', default=True)

    _sql_constraints = [
        ('name_unique', 'UNIQUE(name)', 'Cancellation reason must be unique.'),
    ]


class CustomerRfqSizeLine(models.Model):
    _name = 'customer.rfq.size.line'
    _description = 'Customer RFQ Size Line'
    _order = 'sequence, id'

    rfq_id = fields.Many2one('customer.rfq', string='RFQ',
                             ondelete='cascade', required=True)
    sequence = fields.Integer(default=10)
    quantity = fields.Float(string='Quantity', required=True)
    length_mm = fields.Float(string='Length (mm)', required=True)
    width_mm = fields.Float(string='Width (mm)', required=True)
    depth_mm = fields.Float(string='Depth (mm)')


class CustomerRfqCancelWizard(models.TransientModel):
    _name = 'customer.rfq.cancel.wizard'
    _description = 'Cancel Customer RFQ'

    rfq_id = fields.Many2one('customer.rfq', string='Customer RFQ', required=True, ondelete='cascade')
    reason_ids = fields.Many2many(
        'customer.rfq.cancel.reason', string='Cancellation Reason', required=True,
        help='Pick every reason that applies.')
    comment = fields.Text(
        string='Comment',
        help='Describe what happened, for whoever reviews lost quotes later.')

    def action_confirm_cancel(self):
        self.ensure_one()
        self.rfq_id._apply_cancellation(self.reason_ids, self.comment)
        return {'type': 'ir.actions.act_window_close'}


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
        'res.partner', string='Company Name', tracking=True, required=True,
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
    lab_type = fields.Selection(
        [('igi', 'IGI'), ('gia', 'GIA'), ('other', 'Other')],
        string='Lab Type',
        tracking=True,
    )
    lab_type_other = fields.Char(
        string='Other Lab',
        tracking=True,
        help="Free-text lab name when Lab Type is 'Other'.",
    )

    growth_method = fields.Selection(
        [('hpht', 'HPHT'), ('cvd', 'CVD')],
        string='Growth Method', tracking=True,
        help='Only relevant for Lab-grown stones (HPHT or CVD).',
    )
    colour_mode = fields.Selection(
        [('white', 'White'), ('fancy', 'Fancy')],
        string='Colour Mode', default='white', tracking=True,
    )
    # ── Colour ──────────────────────────────────────────────────────────
    # colour_white covers both Natural (all 6) and Lab grown (first 3 —
    # controlled via view invisible=). Old records with 'def'/'gh'/'ij'
    # still round-trip through Odoo as unlabelled but valid strings; new
    # writes go through the tokens below.
    colour_white = fields.Selection(
        [('def', 'DEF'), ('fg', 'FG'), ('gh', 'GH'),
         ('ij', 'IJ'), ('kl', 'KL'), ('mn', 'MN')],
        string='Colour Range', tracking=True,
    )
    # Fancy colour: replaces the old pill grid with three dependent dropdowns.
    colour_fancy = fields.Selection(
        [('yellow', 'Yellow'), ('pink', 'Pink'), ('blue', 'Blue'),
         ('red', 'Red'), ('green', 'Green'), ('purple', 'Purple'),
         ('orange', 'Orange'), ('violet', 'Violet'), ('grey', 'Grey'),
         ('black', 'Black'), ('brown', 'Brown'), ('champagne', 'Champagne'),
         ('cognac', 'Cognac'), ('chameleon', 'Chameleon'), ('white', 'White'),
         ('salt_and_pepper', 'Salt and Pepper'), ('other', 'Other')],
        string='Fancy Colour', tracking=True,
    )
    fancy_intensity = fields.Selection(
        [('faint', 'Faint'), ('very_light', 'Very Light'), ('light', 'Light'),
         ('fancy_light', 'Fancy Light'), ('fancy', 'Fancy'),
         ('fancy_dark', 'Fancy Dark'), ('fancy_intense', 'Fancy Intense'),
         ('fancy_vivid', 'Fancy Vivid'), ('fancy_deep', 'Fancy Deep')],
        string='Fancy Intensity', tracking=True,
    )
    fancy_overtone = fields.Selection(
        [('none', 'None'), ('yellow', 'Yellow'), ('yellowish', 'Yellowish'),
         ('pink', 'Pink'), ('pinkish', 'Pinkish'), ('blue', 'Blue'),
         ('blueish', 'Blueish'), ('red', 'Red'), ('reddish', 'Reddish'),
         ('green', 'Green'), ('greenish', 'Greenish'), ('purple', 'Purple'),
         ('purplish', 'Purplish'), ('orange', 'Orange'), ('orangey', 'Orangey'),
         ('violet', 'Violet'), ('grey', 'Grey'), ('greyish', 'Greyish'),
         ('black', 'Black'), ('brown', 'Brown'), ('brownish', 'Brownish'),
         ('champagne', 'Champagne'), ('cognac', 'Cognac'),
         ('chameleon', 'Chameleon'), ('white', 'White'), ('other', 'Other')],
        string='Fancy Overtone', tracking=True,
    )

    # ── Clarity ─────────────────────────────────────────────────────────
    # Certified: full IGI scale. Natural adds FL/I3 vs Lab grown's VVS/VS/I2.
    clarity_grade = fields.Selection(
        [('FL', 'FL'), ('IF', 'IF'), ('VVS', 'VVS'), ('VVS1', 'VVS1'),
         ('VVS2', 'VVS2'), ('VS', 'VS'), ('VS1', 'VS1'), ('VS2', 'VS2'),
         ('SI1', 'SI1'), ('SI2', 'SI2'), ('I1', 'I1'), ('I2', 'I2'),
         ('I3', 'I3')],
        string='Clarity', tracking=True,
    )
    # Non-Certified: coarser grouped grades. Shown in place of clarity_grade
    # when stone_certification_type == 'non_certified'.
    clarity_non_cert = fields.Selection(
        [('VVS', 'VVS'), ('VVS-VS', 'VVS-VS'), ('VS', 'VS'),
         ('VS-SI', 'VS-SI'), ('SI', 'SI'), ('I1', 'I1')],
        string='Clarity (Non-Cert)', tracking=True,
    )

    # ── Cut / Polish / Symmetry (Natural mode) ──────────────────────────
    # Preset shortcuts sit above the three individual grade selectors.
    cut_preset = fields.Selection(
        [('3ex', '3EX'), ('ex_cut', 'EX Cut'),
         ('3vg_plus', '3VG+'), ('heart_and_arrow', 'Heart and Arrow')],
        string='Cut Preset', tracking=True,
    )
    cut_grade = fields.Selection(
        [('8x', '8x'), ('ideal', 'Ideal'), ('excellent', 'Excellent'),
         ('very_good', 'Very Good'), ('good', 'Good'),
         ('fair', 'Fair'), ('poor', 'Poor')],
        string='Cut', tracking=True,
    )
    polish_grade = fields.Selection(
        [('excellent', 'Excellent'), ('very_good', 'Very Good'),
         ('good', 'Good'), ('fair', 'Fair'), ('poor', 'Poor')],
        string='Polish', tracking=True,
    )
    symmetry_grade = fields.Selection(
        [('excellent', 'Excellent'), ('very_good', 'Very Good'),
         ('good', 'Good'), ('fair', 'Fair'), ('poor', 'Poor')],
        string='Symmetry', tracking=True,
    )

    # ── Fluorescence (Natural mode) ─────────────────────────────────────
    fluorescence_intensity_sel = fields.Selection(
        [('none', 'None'), ('faint', 'Faint'), ('medium', 'Medium'),
         ('strong', 'Strong'), ('very_strong', 'Very Strong')],
        string='Fluorescence Intensity', tracking=True,
    )
    fluorescence_colour_sel = fields.Selection(
        [('blue', 'Blue'), ('yellow', 'Yellow'), ('red', 'Red'),
         ('green', 'Green'), ('purple', 'Purple'), ('orange', 'Orange')],
        string='Fluorescence Colour', tracking=True,
    )

    # ── Carat range (Natural mode) ──────────────────────────────────────
    carat_min = fields.Float(string='Carat Min', tracking=True)
    carat_max = fields.Float(string='Carat Max', tracking=True)
    unit_type = fields.Selection(
        [('carats', 'Carats'), ('pieces', 'Pieces')],
        string='Unit', default='carats', tracking=True,
    )
    size_line_ids = fields.One2many(
        'customer.rfq.size.line', 'rfq_id', string='Sizes',
    )
    reference_image = fields.Binary(
        string='Reference Image', attachment=True,
        help='Optional example image of the requested stone.',
    )
    reference_image_filename = fields.Char(string='Reference Image Filename')

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
    # No tracking: a tracked change writes "Procurement Price/carat 0.00 -> 1.00"
    # into the chatter, which Sales can read. The cost would leak there regardless of how the field itself is restricted on the form.
    price = fields.Float(string='Procurement Price/carat')
    is_price_visible_for_user = fields.Boolean(
        string='Price Visible',
        compute='_compute_is_price_visible_for_user',
    )
    is_price_readonly = fields.Boolean(
        string='Price Readonly',
        compute='_compute_is_price_readonly',
    )
    notes = fields.Text(string='Notes')

    # Not used in pricing and not on the form any more. Kept as a column so the RFQs that already carry a tax do not lose it; tax belongs on the sale
    # order, where account.tax handles customer and fiscal position properly.
    tax_ids = fields.Many2many(
        comodel_name='account.tax',
        relation='customer_rfq_tax_rel',
        column1='rfq_id',
        column2='tax_id',
        string='Taxes',
        domain=[('type_tax_use', 'in', ['sale', 'all'])],
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
        string='Sales Price/carat',
        currency_field='currency_id',
        compute='_compute_pricing_totals',
        store=False,
        help='What Sales quotes the customer: '
             '(Procurement Price/carat × Carat) + Margin. No tax.',
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

    @api.depends('price', 'carat', 'tax_ids',
                 'size_line_ids', 'size_line_ids.quantity')
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
        default_margin_pct = margin_singleton.default_margin_percentage if margin_singleton else 0.0

        for rec in self:
            effective_carat = 0.0
            if rec.size_line_ids:
                effective_carat = sum(rec.size_line_ids.mapped('quantity')) or 0.0
            if not effective_carat and rec.carat:
                try:
                    effective_carat = float(rec.carat)
                except (TypeError, ValueError):
                    effective_carat = 0.0

            # ── 1. Base price ────────────────────────────────────────────────
            rec_base_price = rec.price * effective_carat

            # ── 2. Margin percentage ─────────────────────────────────────────
            band = self._carat_to_band(effective_carat) if effective_carat else None
            rec_margin_pct = default_margin_pct
            if band and margin_singleton_id:
                margin_line = MarginLine.search([
                    ('margin_id',   '=', margin_singleton_id),
                    ('carat_range', '=', band),
                ], limit=1)
                if margin_line:
                    rec_margin_pct = margin_line.percentage

            # ── 3. Margin amount ─────────────────────────────────────────────
            rec_margin_amount = rec_base_price * (rec_margin_pct / 100.0)

            # ── 4. Tax ───────────────────────────────────────────────────────
            # Deliberately excluded from the RFQ price: the quote is cost plus
            # margin only. Tax is applied downstream on the sale order, where
            # account.tax resolves it against the customer's fiscal position.
            # tax_amount stays on the model, always 0.00, so nothing that reads
            # it breaks.
            taxable_amount = rec_base_price + rec_margin_amount
            rec_tax_amount = 0.0

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

    @api.depends('state')
    @api.depends_context('uid')
    def _compute_is_price_readonly(self):
        """Price is editable for Procurement and Admin only while the RFQ is
        in 'sent_to_procurement' — the one state where Procurement is meant to
        enter it (see action_send_back_to_sales). Once sent back to Sales the
        price is locked for everyone, so a later edit can't silently drift
        from the value Sales already saw. Sales always sees it readonly."""
        user = self.env.user
        is_procurement = user.has_group('contact_stage_bar.group_lgd_procurement')
        is_admin = user.has_group('base.group_system')
        for rec in self:
            editable = (is_procurement or is_admin) and rec.state == 'sent_to_procurement'
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
            ('cancel', 'Cancelled'),
        ],
        string='Status',
        default='draft',
        required=True,
        tracking=True,
    )

    # ── Cancellation ──────────────────────────────────────────────────────────
    cancel_reason_ids = fields.Many2many(
        'customer.rfq.cancel.reason',
        'customer_rfq_cancel_reason_rel', 'rfq_id', 'reason_id',
        string='Cancellation Reason', readonly=True, copy=False, tracking=True,
    )
    cancel_comment = fields.Text(string='Cancellation Comment', readonly=True, copy=False)
    cancelled_by_id = fields.Many2one('res.users', string='Cancelled By', readonly=True, copy=False)
    cancelled_on = fields.Datetime(string='Cancelled On', readonly=True, copy=False)

    # Set by Procurement to mark whether the stone needs certification.
    stone_certification = fields.Selection(
        [('yes', 'Yes'), ('no', 'No')],
        string='Stone Certification',
        tracking=True,
    )


    @api.constrains('shape_ids')
    def _check_single_shape(self):
        """A Customer RFQ describes allows exactly one Shape."""
        for rec in self:
            if len(rec.shape_ids) > 1:
                raise ValidationError(_(
                    "Please select only one Shape (got %d).") % len(rec.shape_ids))

    @api.constrains(
        'shape_ids', 'stone_type', 'stone_certification_type', 'growth_method',
        'colour_mode', 'colour_white', 'colour_fancy',
        'clarity_grade', 'clarity_non_cert', 'cut_grade', 'size_line_ids',
    )
    def _check_required_specs(self):
        """Guided-intake required fields. Legacy Char fields (color, clarity,
        carat, size, quantity) are no longer required — the redesigned form
        uses the new Selection fields + size_line_ids one2many."""
        for rec in self:
            missing = []
            if not rec.shape_ids:
                missing.append(_('Shape'))
            if not rec.stone_type:
                missing.append(_('Type of Stone'))
            if rec.stone_type == 'lab_grown' and not rec.growth_method:
                missing.append(_('Growth Method'))
            if not rec.stone_certification_type:
                missing.append(_('Certification'))
            if rec.colour_mode == 'white' and not rec.colour_white:
                missing.append(_('Colour range'))
            if rec.colour_mode == 'fancy' and not rec.colour_fancy:
                missing.append(_('Fancy Colour'))
            # Certified → clarity_grade required; Non-Certified → clarity_non_cert.
            if rec.stone_certification_type == 'certified' and not rec.clarity_grade:
                missing.append(_('Clarity'))
            if rec.stone_certification_type == 'non_certified' and not rec.clarity_non_cert:
                missing.append(_('Clarity'))
            if not rec.cut_grade:
                missing.append(_('Cut'))
            if not rec.size_line_ids:
                missing.append(_('At least one Size row'))
            if missing:
                raise ValidationError(_(
                    "These fields are mandatory on a Customer RFQ: %s."
                ) % ', '.join(missing))

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
            if rec.state == 'cancel':
                raise UserError(_("This Customer RFQ is cancelled."))
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
            # No figures in the body, for the same reason price is not tracked:
            # the chatter is readable by Sales.
            rec.message_post(body=_("Sent back to Sales team."))

    def _create_requested_stone(self, unit_price):
        vals = {
            'sale_ok': True,
            'shapes': ', '.join(self.shape_ids.mapped('name')),
            'color': self.color,
            'clarity': self.clarity,
            'weight_carat': str(self.carat) if self.carat else False,
            'list_price': unit_price,
            'name': self.name,
        }

        product = self.env['product.template'].sudo().with_context(
            default_order_id=False).create(vals)
        product.action_combine_sdk_fields()
        if not product.name:
            product.name = self.name
        return product

    def action_open_cancel_wizard(self):
        """Open the reason prompt. Cancelling always goes through it, so an RFQ can never end up cancelled with no explanation recorded."""
        self.ensure_one()
        if self.state == 'cancel':
            raise UserError(_("This Customer RFQ is already cancelled."))
        if self.state == 'offline_order_created':
            raise UserError(_(
                "An Offline Order was already created from this RFQ. "
                "Cancel the order instead."
            ))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Cancel Customer RFQ'),
            'res_model': 'customer.rfq.cancel.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_rfq_id': self.id},
        }

    def _apply_cancellation(self, reasons, comment):
        """Record the cancellation. Called by the wizard, not from the UI."""
        self.ensure_one()
        if self.state == 'cancel':
            raise UserError(_("This Customer RFQ is already cancelled."))
        if self.state == 'offline_order_created':
            raise UserError(_(
                "An Offline Order was already created from this RFQ. "
                "Cancel the order instead."
            ))
        if not reasons:
            raise UserError(_("Select at least one cancellation reason."))
        self.sudo().write({
            'state': 'cancel',
            'cancel_reason_ids': [(6, 0, reasons.ids)],
            'cancel_comment': comment or False,
            'cancelled_by_id': self.env.uid,
            'cancelled_on': fields.Datetime.now(),
        })
        body = _("Cancelled - %s") % ', '.join(reasons.mapped('name'))
        if comment:
            body += Markup("<br/>") + comment
        self.sudo().message_post(body=body)
        return True

    # Safety threshold — if a single size-line quantity is above this we ask
    # the user to confirm before spawning that many lines (guards against
    # typos like "1000" that would create an unmanageable sale order).
    _OFFLINE_ORDER_QTY_CONFIRM_THRESHOLD = 50

    def action_create_offline_order(self):
        self.ensure_one()
        if self.state == 'offline_order_created':
            raise UserError(_("An Offline Order has already been created for this RFQ."))
        if self.state != 'sent_back_to_sales':
            raise UserError(_("Offline Order can be created only after Procurement sends the RFQ back."))
        if not self.partner_id:
            raise UserError(_("Customer is required to create an Offline Order."))

        unit_price = self.total_price if self.total_price else self.price
        product = self._create_requested_stone(unit_price)

        base_vals = {
            'product_template_id': product.id,
            'product_id':          product.product_variant_id.id,
            'product_uom':         product.uom_id.id,
            'shapes':              ', '.join(self.shape_ids.mapped('name')),
            'color':               self.color,
            'clarity':             self.clarity,
            'carat_weight':        str(self.carat) if self.carat else False,
            'price_unit':          unit_price,
        }

        line_vals = self._build_offline_order_lines(base_vals)
        if not line_vals:
            raise UserError(_(
                "Cannot create an Offline Order: the RFQ has no size lines "
                "and no legacy Quantity set."
            ))

        order = self.env['custom.sale.order'].create({
            'partner_id':      self.partner_id.id,
            'state':           'draft',
            'customer_rfq_id': self.id,
            'order_line':      [(0, 0, vals) for vals in line_vals],
        })

        self.message_post(body=_(
            "Offline Order %(order)s created with %(count)d line(s)."
        ) % {'order': order.name, 'count': len(line_vals)})

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

    def _build_offline_order_lines(self, base_vals):
        """Expand every size_line row into N per-stone dicts.

        Rules:
        - int(round-down) for pieces, int(round-nearest) for carats.
        - Big-Qty guard: raise UserError if any single row asks for > threshold
          stones, so a typo doesn't silently generate 1000+ lines.
        - Falls back to a single row for legacy RFQs (no size_line_ids); uses
          the legacy `quantity` field as the count.
        - Traces every generated line back to its source size_line via
          rfq_size_line_id.
        """
        line_vals = []

        if self.size_line_ids:
            unit_is_pieces = (self.unit_type or 'carats') == 'pieces'
            for size_line in self.size_line_ids:
                raw_qty = size_line.quantity or 0.0
                count = int(raw_qty) if unit_is_pieces else int(round(raw_qty))
                if count <= 0:
                    continue
                if count > self._OFFLINE_ORDER_QTY_CONFIRM_THRESHOLD:
                    raise UserError(_(
                        "One of the size lines requests %(count)d stones "
                        "(quantity %(qty)s). This is above the safety "
                        "threshold of %(threshold)d — please split the row "
                        "if this is intentional."
                    ) % {
                        'count': count,
                        'qty': raw_qty,
                        'threshold': self._OFFLINE_ORDER_QTY_CONFIRM_THRESHOLD,
                    })
                # Compose the per-stone description with dimensions from
                # the size-line row.
                dims = " × ".join(
                    f"{v:g}" for v in (size_line.length_mm, size_line.width_mm,
                                       size_line.depth_mm) if v
                )
                desc = self.name
                if dims:
                    desc = _("%s (%s mm)") % (self.name, dims)
                for _n in range(count):
                    line_vals.append({
                        **base_vals,
                        'name': desc,
                        'product_uom_qty': 1.0,
                        'rfq_size_line_id': size_line.id,
                    })
            return line_vals

        # Legacy fallback: no guided-intake size lines. Use legacy quantity + size.
        legacy_qty = int(self.quantity) if self.quantity else 1
        if legacy_qty > self._OFFLINE_ORDER_QTY_CONFIRM_THRESHOLD:
            raise UserError(_(
                "The RFQ quantity (%(qty)d) is above the safety threshold "
                "of %(threshold)d — please split the RFQ if this is intentional."
            ) % {
                'qty': legacy_qty,
                'threshold': self._OFFLINE_ORDER_QTY_CONFIRM_THRESHOLD,
            })
        legacy_desc = self.name
        if self.size:
            legacy_desc = _("%s - Size: %s") % (self.name, self.size)
        for _n in range(max(1, legacy_qty)):
            line_vals.append({
                **base_vals,
                'name': legacy_desc,
                'product_uom_qty': 1.0,
            })
        return line_vals
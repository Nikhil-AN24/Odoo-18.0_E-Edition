from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from markupsafe import Markup
import json
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

class CustomerRfqGrade(models.Model):
    _name = 'customer.rfq.grade'
    _description = 'Customer RFQ Cut/Polish/Symmetry Grade'
    _order = 'category, sequence, id'

    name = fields.Char(string='Label', required=True, translate=True)
    code = fields.Char(string='Code', required=True,
                       help='Machine key used by preset auto-fill.')
    category = fields.Selection(
        [('cut', 'Cut'), ('polish', 'Polish'), ('symmetry', 'Symmetry'),
         ('fluorescence', 'Fluorescence')],
        string='Category', required=True,
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ('code_category_unique', 'UNIQUE(category, code)',
         'Grade code must be unique per category.'),
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

    carat_per_piece = fields.Float(
        string='Carat/Piece',
        help='Carat weight of one stone on this row. Used in Pieces mode to '
             'turn the piece count into a carat weight the Price/carat can '
             'be extended against.',
    )
    length_mm = fields.Float(string='Length (mm)')
    width_mm = fields.Float(string='Width (mm)')
    depth_mm = fields.Float(string='Depth (mm)')
    description = fields.Char(string='Description')
    costing = fields.Float(string='Costing')
    # Per-row Sales Price/Carat surfaced on the Sales side once Procurement sends the RFQ back.
    # Formula: costing × (1 + margin_percentage / 100).
    sales_price_per_carat = fields.Float(
        string='Sales Price/Carat',
        compute='_compute_sales_price_per_carat',
        store=False,
    )

    @api.depends('costing', 'rfq_id.margin_percentage')
    def _compute_sales_price_per_carat(self):
        for line in self:
            pct = line.rfq_id.margin_percentage or 0.0
            line.sales_price_per_carat = (line.costing or 0.0) * (1.0 + pct / 100.0)

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
        'res.partner', string='Buyer Name', tracking=True, required=True,
    )

    partner_domain = fields.Char(compute='_compute_partner_domain')

    @api.depends('owner_id')
    @api.depends_context('uid')
    def _compute_partner_domain(self):
        user = self.env.user
        sees_all = (
            user.has_group('contact_stage_bar.group_lgd_sales_manager')
            or user.has_group('contact_stage_bar.group_lgd_superadmin')
            or user.has_group('base.group_system')
        )
        dom = [('partner_kind', '=', 'buyer')]
        if user.has_group('contact_stage_bar.group_lgd_sales') and not sees_all:
            dom.append(('user_id', '=', user.id))

        gated_stage_ids = self.env['res.partner']._lgd_gated_stage_ids()
        if gated_stage_ids:
            dom.append(('stage_id', 'in', gated_stage_ids))
        dom_str = json.dumps(dom)
        for rec in self:
            rec.partner_domain = dom_str

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
        string='Growth Method', tracking=True, default='hpht',
        help='Only relevant for Lab-grown stones (HPHT or CVD).',
    )
    colour_mode = fields.Selection(
        [('white', 'White'), ('fancy', 'Fancy')],
        string='Colour Mode', default='white', tracking=True,
    )
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
        [('3ex', '3EX'), ('ex_cut', 'EX Cut'), ('3vg_plus', '3VG+')],
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

    # Multi-select variants used by the redesigned pill UI. 
    # Preset onchange populates them from cut_preset; the user can still tick / untick any individual pill afterwards.
    cut_grade_ids = fields.Many2many(
        'customer.rfq.grade', 'customer_rfq_cut_grade_rel',
        'rfq_id', 'grade_id', string='Cut',
        domain=[('category', '=', 'cut')], tracking=True,
    )
    polish_grade_ids = fields.Many2many(
        'customer.rfq.grade', 'customer_rfq_polish_grade_rel',
        'rfq_id', 'grade_id', string='Polish',
        domain=[('category', '=', 'polish')], tracking=True,
    )
    symmetry_grade_ids = fields.Many2many(
        'customer.rfq.grade', 'customer_rfq_symmetry_grade_rel',
        'rfq_id', 'grade_id', string='Symmetry',
        domain=[('category', '=', 'symmetry')], tracking=True,
    )
    fluorescence_grade_ids = fields.Many2many(
        'customer.rfq.grade', 'customer_rfq_fluorescence_grade_rel',
        'rfq_id', 'grade_id', string='Fluorescence',
        domain=[('category', '=', 'fluorescence')], tracking=True,
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

    carat = fields.Float(string='Carat / Stone', tracking=True)
    # Natural + Non-Certified (melee) is traded as a parcel: a sieve Size plus a
    # total carat weight, with no per-stone weight and no piece count. This is
    # the quantity the Sales Price/carat is extended against on that path.
    total_carat = fields.Float(
        string='Total Carat', tracking=True,
        help='Total carat weight of the parcel. Used on Non-Certified '
             'Natural RFQs, which are quoted by parcel weight rather than '
             'per stone.',
    )
    color = fields.Char(string='Color', tracking=True)
    clarity = fields.Char(string='Clarity', tracking=True)
    size = fields.Char(string='Size', tracking=True)
    quantity = fields.Integer(string='Quantity', tracking=True)
    cut = fields.Char(string='Cut', tracking=True)        # Free-text Cut for Lab-grown + Non-Certified stones (no grading lab).

    currency_id = fields.Many2one('res.currency', string='Currency', default=lambda self: self.env.ref('base.USD').id)
    # Two-pill toggle Procurement uses to pick INR vs USD for the price
    procurement_currency = fields.Selection(
        [('inr', '₹'), ('usd', '$')],
        string='Currency', default='usd', tracking=True,
    )

    @api.onchange('procurement_currency')
    def _onchange_procurement_currency(self):
        for rec in self:
            if rec.procurement_currency == 'inr':
                rec.currency_id = self.env.ref('base.INR', raise_if_not_found=False)
            elif rec.procurement_currency == 'usd':
                rec.currency_id = self.env.ref('base.USD', raise_if_not_found=False)
    price = fields.Float(string='Procurement Price/carat')
    different_prices = fields.Boolean(string='Different prices', default=False)
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
        string='Sales Order Value',
        currency_field='currency_id',
        compute='_compute_pricing_totals',
        store=False,
        help='Per-stone extended amount (money), not a rate: the Order '
             'Total divided across the stones the Offline Order will '
             'generate. This is the price_unit of every generated line.',
    )

    # Genuine per-carat RATE that Sales quotes — a rate in, a rate out, carat
    # weight never enters this number (it only selects the margin band).
    sale_rate_per_carat = fields.Monetary(
        string='Sales Price/carat',
        currency_field='currency_id',
        compute='_compute_pricing_totals',
        store=False,
        help='Genuine per-carat rate: Procurement Price/carat + Margin. '
             'Carat weight never enters this value — multiplying it by Carat '
             'gives the per-stone price (see Sales Price/Stone).',
    )

    # Per-stone price = Procurement sent Rate × carat/stone. 
    sale_price_per_stone = fields.Monetary(
        string='Sales Price/Stone',
        currency_field='currency_id',
        compute='_compute_pricing_totals',
        store=False,
        help='Per-stone price: the Order Total divided across the stones. '
             'On the legacy path this is Sales Price/carat × Carat/Stone.',
    )

    # Full quote value = per-stone price × number of stones. Equals exactly the sum of the lines the Offline Order will create.
    order_total = fields.Monetary(
        string='Order Total',
        currency_field='currency_id',
        compute='_compute_pricing_totals',
        store=False,
        help='Full quote value = Sales Price/Stone × number of stones '
             '(the total the Offline Order will sum to).',
    )

    def _is_melee_parcel(self):
        """True for Natural + Non-Certified RFQs — the sieve-Size path.
        Mirrors the view conditions on `size` and `total_carat`: these records
        carry no Carat/Stone and no Quantity, so their weight is a whole-parcel
        figure that must not be multiplied by a stone count. """
        self.ensure_one()
        return (self.stone_certification_type == 'non_certified'
                and self.stone_type != 'lab_grown')

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

    @api.onchange('price')
    def _onchange_price_mirror_to_costing(self):
        """Mirror the flat Procurement Price/carat into every size line's
        Costing. Only runs when Different prices is OFF — with it ON, per-row
        costing is user-managed."""
        for rec in self:
            if rec.different_prices:
                continue
            for sl in rec.size_line_ids:
                sl.costing = rec.price

    @api.onchange('different_prices')
    def _onchange_different_prices_clear_flat(self):
        """Ticking Different prices clears the flat Procurement Price/carat
        (they are mutually exclusive). Un-ticking blanks all per-row Costings
        so the user can enter a fresh flat price."""
        for rec in self:
            if rec.different_prices:
                rec.price = 0.0
            else:
                for sl in rec.size_line_ids:
                    sl.costing = 0.0

    @api.onchange('size_line_ids')
    def _onchange_size_lines_seed_costing(self):
        """New size line inherits the current flat price when Different prices
        is OFF, so the Costing column stays in sync as rows are added."""
        for rec in self:
            if rec.different_prices or not rec.price:
                continue
            for sl in rec.size_line_ids:
                if not sl.costing:
                    sl.costing = rec.price

    def _rfq_stone_count(self):
        """Number of stones this RFQ will spawn on the Offline Order.

        Mirrors _build_offline_order_lines exactly so that Order Total equals
        the sum of the generated per-stone lines (each priced at total_price):
          - size lines present → sum of per-row counts
            (int for pieces, round for carats),
          - legacy fallback     → the header Quantity (min 1).
        """
        self.ensure_one()
        if self.size_line_ids:
            unit_is_pieces = (self.unit_type or 'carats') == 'pieces'
            total = 0
            for sl in self.size_line_ids:
                raw = sl.quantity or 0.0
                count = int(raw) if unit_is_pieces else int(round(raw))
                if count > 0:
                    total += count
            return total
        return max(1, int(self.quantity)) if self.quantity else 1

    @api.depends('price', 'carat', 'total_carat', 'tax_ids', 'different_prices',
                 'quantity', 'unit_type',
                 'stone_type', 'stone_certification_type',
                 'size_line_ids', 'size_line_ids.quantity',
                 'size_line_ids.carat_per_piece',
                 'size_line_ids.costing')
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
            order_total       = rate × total carat weight of the whole quote
            total_price       = order_total / stone count  (per-stone money)

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
                if (rec.unit_type or 'carats') == 'pieces':
                    # `quantity` is a piece count here, so the order's carat
                    # weight is pieces x the weight of one stone on that row.
                    # Summing the piece counts (as this used to) priced every
                    # stone as if it weighed exactly 1.00 ct.
                    effective_carat = sum(
                        (sl.quantity or 0.0) * (sl.carat_per_piece or 0.0)
                        for sl in rec.size_line_ids
                    )
                else:
                    effective_carat = sum(
                        rec.size_line_ids.mapped('quantity')) or 0.0
            if not effective_carat and rec.carat:
                try:
                    effective_carat = float(rec.carat)
                except (TypeError, ValueError):
                    effective_carat = 0.0
            if not effective_carat and rec.total_carat:
                effective_carat = rec.total_carat

            # ── 1. Base price ────────────────────────────────────────────────
            # Different prices ON → sum of (per-row costing × per-row quantity).
            # OFF → the flat Procurement Price/carat × effective carat weight.
            if rec.different_prices and rec.size_line_ids:
                rec_base_price = sum(
                    (sl.costing or 0.0) * (sl.quantity or 0.0)
                    for sl in rec.size_line_ids
                )
            else:
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

            # ── 5. Rate vs money ─────────────────────────────────────────────
            # RULE: carat weight must never appear in a formula that produces a
            # rate. The rate is the extended amount divided back out by the same
            # effective carat weight, so (rate × effective_carat) == total again.
            # This yields the correct blended rate for both the flat and the
            # Different-prices paths. Per-stone money is rate × carat/stone.
            rec_total = taxable_amount + rec_tax_amount
            rec_rate = (rec_total / effective_carat) if effective_carat else rec.price

            # ── 6. Assign back ───────────────────────────────────────────────
            rec.base_price           = rec_base_price
            rec.margin_percentage    = rec_margin_pct
            rec.margin_amount        = rec_margin_amount
            rec.tax_amount           = rec_tax_amount
            rec.sale_rate_per_carat  = rec_rate

            # Order Total = Sales Price/carat × the TOTAL carat weight of the
            # whole quote. effective_carat already IS that total on the guided
            # intake path (the size-line rows are the order's carat weight),
            # but it is only ONE stone's weight on the legacy path (Carat/Stone
            # × Quantity), so the two paths have to be extended differently.
            # Multiplying rec_total by the stone count on BOTH paths double
            # counted the guided-intake quote by the number of stones.
            stone_count = rec._rfq_stone_count()
            if rec.size_line_ids:
                total_order_carat = effective_carat
            elif rec._is_melee_parcel():
                # Parcel weight is the order's total weight, sold as a single
                # line — extending it by a stone count would double count it
                # exactly as the guided-intake path used to.
                total_order_carat = effective_carat
                stone_count = 1
            else:
                total_order_carat = effective_carat * stone_count
            rec.order_total = rec_rate * total_order_carat

            # Per-stone money is the quote divided across the stones the
            # Offline Order will generate. This is the price_unit of every
            # generated line, so order_total == sum(lines) by construction on
            # both paths. On the legacy path it reduces to rate × Carat/Stone,
            # which is what it has always been.
            rec.total_price = (
                rec.order_total / stone_count) if stone_count else rec_total
            rec.sale_price_per_stone = rec.total_price

    @api.depends('state')
    @api.depends_context('uid')
    def _compute_is_price_visible_for_user(self):
        """Visibility rules:
        - Procurement or Admin: always visible
        - Sales: visible once Procurement has sent the RFQ back and stays
          visible through the Offline Order Created stage
          (state in ('sent_back_to_sales', 'offline_order_created'))
        """
        user = self.env.user
        is_procurement = user.has_group('contact_stage_bar.group_lgd_procurement')
        is_admin = user.has_group('base.group_system')
        for rec in self:
            if is_procurement or is_admin:
                rec.is_price_visible_for_user = True
            else:
                rec.is_price_visible_for_user = (
                    rec.state in ('sent_back_to_sales', 'offline_order_created')
                )

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


    # Preset → (cut codes, polish codes, symmetry codes) mapping. 
    _CUT_PRESET_GRADES = {
        '3ex': (['8x', 'ideal', 'excellent'], ['excellent'], ['excellent']),
        'ex_cut': (['excellent'], ['excellent', 'very_good'], ['excellent', 'very_good']),
        '3vg_plus': (['excellent', 'very_good'], ['excellent', 'very_good'], ['excellent', 'very_good']),
    }

    @api.onchange('cut_preset')
    def _onchange_cut_preset(self):
        """Fill the three grade pills from the preset — and empty them again
        when the preset is cleared.

        Odoo's selection_badge already deselects when you click the badge that
        is active (BadgeSelectionField.onChange sets the field to False for any
        field that is not required, and this one is not). What was missing is
        the other half: clearing the preset used to return early and leave the
        pills ticked, so the screen still showed 3EX's grades with no badge
        selected and no way to undo them. Clearing the preset now clears what
        it filled in.
        """
        Grade = self.env['customer.rfq.grade']
        if not self.cut_preset:
            self.cut_grade_ids = Grade.browse()
            self.polish_grade_ids = Grade.browse()
            self.symmetry_grade_ids = Grade.browse()
            return
        mapping = self._CUT_PRESET_GRADES.get(self.cut_preset)
        if not mapping:
            return
        cut_codes, polish_codes, sym_codes = mapping
        self.cut_grade_ids = Grade.search(
            [('category', '=', 'cut'), ('code', 'in', cut_codes)])
        self.polish_grade_ids = Grade.search(
            [('category', '=', 'polish'), ('code', 'in', polish_codes)])
        self.symmetry_grade_ids = Grade.search(
            [('category', '=', 'symmetry'), ('code', 'in', sym_codes)])

    @api.constrains('quantity', 'stone_certification_type')
    def _check_quantity_positive_integer(self):
        for rec in self:
            if rec.stone_certification_type == 'non_certified':
                continue
            if rec.quantity <= 0:
                raise ValidationError(_(
                    "Quantity must be a whole number greater than zero."))

    @api.constrains('carat')
    def _check_carat_positive(self):
        """A stone cannot weigh nothing or less than nothing."""
        for rec in self:
            if rec.stone_certification_type == 'non_certified':
                continue
            if rec.carat is not False and rec.carat <= 0:
                raise ValidationError(_(
                    "Carat / Stone must be greater than zero."))

    @api.constrains('color')
    def _check_color_is_not_numeric(self):
        """Colour is a grade or a fancy-colour name — D, G, Fancy Yellow —
        never a number. Digits here are almost always a value typed into the
        wrong field, and they travel all the way to the stone specs."""
        for rec in self:
            value = (rec.color or '').strip()
            if value and any(character.isdigit() for character in value):
                raise ValidationError(_(
                    "Colour cannot contain numbers — use a grade such as D or "
                    "G, or a fancy colour name. You entered: %s") % value)

    @api.constrains('shape_ids')
    def _check_single_shape(self):
        """A Customer RFQ describes allows exactly one Shape."""
        for rec in self:
            if len(rec.shape_ids) > 1:
                raise ValidationError(_(
                    "Please select only one Shape (got %d).") % len(rec.shape_ids))

    @api.constrains('shape_ids', 'stone_type', 'stone_certification_type',
                    'color', 'clarity', 'size', 'quantity')
    def _check_required_specs(self):
        for rec in self:
            missing = []
            if not rec.shape_ids:
                missing.append(_('Shape'))
            if not rec.stone_type:
                missing.append(_('Type of Stone'))
            if not rec.stone_certification_type:
                missing.append(_('Certification'))
            if not rec.color:
                missing.append(_('Color'))
            if not rec.clarity:
                missing.append(_('Clarity'))
            if (rec.stone_certification_type == 'non_certified'
                    and rec.stone_type != 'lab_grown'
                    and not rec.size):
                missing.append(_('Size'))
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
        result = super(CustomerRfq, self.sudo()).write(vals)

        if 'price' in vals or 'different_prices' in vals:
            for rec in self:
                if rec.different_prices:
                    continue
                if rec.size_line_ids and rec.price:
                    rec.size_line_ids.sudo().write({'costing': rec.price})
        return result

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
            if rec.different_prices:
                if not rec.size_line_ids:
                    raise UserError(_(
                        "Different prices is on but there are no size lines to price."
                    ))
                unpriced = [sl for sl in rec.size_line_ids
                            if not sl.costing or sl.costing <= 0]
                if unpriced:
                    raise UserError(_(
                        "Different prices is on — every Costing must be filled "
                        "before sending back to Sales. %d row(s) still at 0."
                    ) % len(unpriced))
            else:
                if not rec.price or rec.price <= 0:
                    raise UserError(_(
                        "Please enter a Price before sending back to Sales. "
                        "Sales cannot see pricing until a valid amount is set."
                    ))
            rec.state = 'sent_back_to_sales'
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
        # Non-Certified RFQs override the auto-composed name with a
        # deliberately generic label so the SO line reads
        # "Non-Certified - <shape>" rather than an IGI-style trade name.
        if self.stone_certification_type == 'non_certified':
            shapes = ', '.join(self.shape_ids.mapped('name'))
            product.name = _("Non-Certified - %s") % (shapes or self.name)
            # Marker used by product.template form to hide the Certificate
            # Number field and the "Fetch from IGI" button for 'Non-Certified'.
            product.certificate_type = 'Non-Certified'
            product.is_non_certified_source = True
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
        # A quote with no carat weight and no piece count cannot be priced:
        # Order Total is 0.00 and unit_price below would silently fall back to
        # the raw Procurement Price/carat — dropping the margin and quoting a
        # single unit instead of the real quantity. Fail loudly rather than create a mispriced order.
        if not self.order_total:
            raise UserError(_(
                "Order Total is 0.00, so this RFQ cannot be turned into an "
                "Offline Order.\n\n"
                "There is no carat weight or piece count to extend the Sales "
                "Price/carat against. Natural / Non-Certified RFQs currently "
                "capture only a sieve Size and have no quantity field — the "
                "quantity has to be recorded before an Offline Order can be "
                "priced."
            ))

        unit_price = self.total_price if self.total_price else self.price
        product = self._create_requested_stone(unit_price)

        base_vals = {
            'product_template_id': product.id,
            'product_id':          product.product_variant_id.id,
            'product_uom':         product.uom_id.id,
            'shapes':              ', '.join(self.shape_ids.mapped('name')),
            'color':               self.color,
            'clarity':             self.clarity,
            'carat_weight':        (str(self.carat) if self.carat
                                    else (str(self.total_carat)
                                          if self.total_carat else False)),
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

        order.action_confirm_offline_order()

        self.message_post(body=_(
            "Offline Order %(order)s created with %(count)d line(s) "
            "and confirmed (Sale Order %(so)s)."
        ) % {
            'order': order.name,
            'count': len(line_vals),
            'so': order.sale_order_id.name or '-',
        })

        # Advance the RFQ so the "Create Offline Order" button hides and a second
        # offline order can't be created from the same RFQ.
        self.state = 'offline_order_created'

        # Stay on the Customer RFQ instead of opening the (already created and
        # confirmed) offline order. Returning nothing makes the web client reload
        # this record, so the RFQ refreshes to the "Offline Order Created" stage;
        # the Sale Order / Offline Orders smart buttons remain for navigation.
        return True

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

                desc = self.name
                if size_line.description:
                    desc = _("%s - %s") % (self.name, size_line.description)
                else:
                    dims = " × ".join(
                        f"{v:g}" for v in (size_line.length_mm, size_line.width_mm,
                                           size_line.depth_mm) if v
                    )
                    if dims:
                        desc = _("%s (%s mm)") % (self.name, dims)

                row_vals = dict(base_vals)
                if unit_is_pieces:
                    stone_carat = size_line.carat_per_piece or 0.0
                else:
                    # Carats mode: the row's weight split across the stones the rounding produced for that row.
                    stone_carat = (size_line.quantity or 0.0) / count
                if stone_carat:

                    row_vals['carat_weight'] = ('%.3f' % stone_carat).rstrip(
                        '0').rstrip('.')
                    row_vals['price_unit'] = (
                        self.sale_rate_per_carat or 0.0) * stone_carat
                for _n in range(count):
                    line_vals.append({
                        **row_vals,
                        'name': desc,
                        'product_uom_qty': 1.0,
                        'rfq_size_line_id': size_line.id,
                    })
            return line_vals

        # Melee parcel: one line for the whole parcel, priced at the parcel
        # total. Matches stone_count = 1 in _compute_pricing_totals, so
        # Order Total == sum(lines) even if a stale Quantity survived a certification switch.
        if self._is_melee_parcel():
            legacy_qty = 1
        else:
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
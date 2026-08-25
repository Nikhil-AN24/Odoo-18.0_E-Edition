from odoo import models, fields, api

class ResPartner(models.Model):
    _inherit = "res.partner"

    partner_kind = fields.Selection(
        selection=[('buyer', 'Buyer'), ('supplier', 'Supplier')],
        string="Account Type",
        index=True,
        tracking=True,
        help="Determines which menu the record appears under. Set "
             "automatically from the menu the record is created in.",
    )

    ein_number = fields.Char(string="EIN Number")
    gst_stages = fields.Selection([
        ('pending', 'Pending'),
        ('verified', 'Verified'),       
        ('not_verified', 'Not Verified'),
        ('manual_verified', 'Manual Verified'),
        ('failed', 'Failed')
    ], string='GST Status', tracking=True)

    ein_status = fields.Selection([
        ('pending', 'Pending'),
        ('verified', 'Verified'),       
        ('not_verified', 'Not Verified'),
        ('manual_verified', 'Manual Verified'),
        ('failed', 'Failed')
    ], string='EIN Status', tracking=True)
    first_name = fields.Char(string="First Name")
    last_name = fields.Char(string="Last Name")
    contact_person_name = fields.Char(string="Contact Person Name", tracking=True)
    job_position = fields.Char(string="Job Position")
    billing_address = fields.Text(string="Billing Address")
    shipping_address_differs = fields.Boolean(
        string="The Shipping Address does not match the Billing Address.",
        default=False
    )
    shipping_address = fields.Text(string="Shipping Address",
        compute="_compute_shipping_address", store=True)

    shipping_street = fields.Char(string="Shipping Street")
    shipping_street2 = fields.Char(string="Shipping Street 2")
    shipping_city = fields.Char(string="Shipping City")
    shipping_zip = fields.Char(string="Shipping ZIP")
    shipping_state_id = fields.Many2one(
        "res.country.state", string="Shipping State",
        domain="[('country_id', '=?', shipping_country_id)]")
    shipping_country_id = fields.Many2one("res.country", string="Shipping Country")

    # Shipping field -> billing field it mirrors while the flag is off.
    _SHIPPING_ADDRESS_FIELDS = {
        'shipping_street': 'street',
        'shipping_street2': 'street2',
        'shipping_city': 'city',
        'shipping_zip': 'zip',
        'shipping_state_id': 'state_id',
        'shipping_country_id': 'country_id',
    }

    # Base/localization fields extended here only to enable chatter tracking
    name = fields.Char(tracking=True)
    phone = fields.Char(tracking=True)
    email = fields.Char(tracking=True)
    mobile = fields.Char(tracking=True)
    street = fields.Char(tracking=True)
    street2 = fields.Char(tracking=True)
    city = fields.Char(tracking=True)
    state_id = fields.Many2one(tracking=True)
    zip = fields.Char(tracking=True)
    country_id = fields.Many2one(tracking=True)
    vat = fields.Char(tracking=True)  # GSTIN
    l10n_in_pan = fields.Char(tracking=True)  # PAN

    def _sync_shipping_from_billing(self):
        for partner in self:
            partner.update({ship: partner[bill]
                            for ship, bill in self._SHIPPING_ADDRESS_FIELDS.items()})

    def _clear_shipping_address(self):
        self.update(dict.fromkeys(self._SHIPPING_ADDRESS_FIELDS, False))

    @api.onchange('shipping_address_differs')
    def _onchange_shipping_address_differs(self):
        for partner in self:
            if partner.shipping_address_differs:
                partner._clear_shipping_address()
            else:
                partner._sync_shipping_from_billing()

    @api.onchange('street', 'street2', 'city', 'zip', 'state_id', 'country_id')
    def _onchange_billing_address(self):
        for partner in self:
            if not partner.shipping_address_differs:
                partner._sync_shipping_from_billing()

    @api.depends('shipping_street', 'shipping_street2', 'shipping_city',
                 'shipping_zip', 'shipping_state_id', 'shipping_country_id')
    def _compute_shipping_address(self):
        """Flat text copy, kept for reports and any code reading the old field."""
        for partner in self:
            locality = ", ".join(p for p in (partner.shipping_city,
                                             partner.shipping_state_id.name,
                                             partner.shipping_zip) if p)
            partner.shipping_address = "\n".join(
                p for p in (partner.shipping_street, partner.shipping_street2,
                            locality, partner.shipping_country_id.name) if p
            ) or False

    terms = fields.Selection([
        ('advance', 'Advance'),
        ('credit', 'Credit'),
        ('cod', 'Cash on Delivery'),
    ], string="Terms", default='advance')
    stage_id = fields.Many2one('res.partner.stage',string="Status",tracking=True,group_expand="_group_expand_stage_id")

    document = fields.Binary(string="Document", attachment=True)
    document_filename = fields.Char(string="Document Filename")

    # company_type = fields.Selection(string='Company Type',
    # selection=[('person', 'Individual'), ('company', 'Company')],
    # compute='_compute_company_type', inverse='_write_company_type', default='company')
    
    
    category_ids = fields.Many2many(
        comodel_name='custom.category',
        relation='res_partner_account_tag_rel',  
        column1='partner_id',
        column2='tag_id',
        string="Category",
        copy=False,
    )
    primary_category_ids = fields.Many2many(
        comodel_name='custom.category',
        relation='res_partner_account_tag_rel_1', 
        column1='partner_id',
        column2='tag_id',
        string="Primary Category",
        copy=False,
    )
    lead_source = fields.Selection([('website', 'Website'), ('cold_call', 'Cold Call'), ('email', 'Email'), ('marketing_campaign', 'Marketing Campaign')], string='Lead Source', tracking=True)
    graduation_rate = fields.Selection([('0', 'No Rating'),('1', '1'),('2', '2'),('3', '3'),('4', '4'),], string='Graduation Rate', default='0') 
   
    # vendor_per_carat_price = fields.Float(string="Vendor Price Per Carat")
    # vendor_final_price = fields.Float(string="Vendor Final Price")
    # can_see_all = fields.Boolean(string="Is Admin", compute='_compute_can_see_all',store=False)
    
    @api.model
    def _group_expand_stage_id(self, stages, domain):
        return self.env['res.partner.stage'].search([])

    @api.model
    def _infer_partner_kind(self, vals):
        kind = self.env.context.get('default_partner_kind')
        if kind:
            return kind
        supplier, customer = vals.get('supplier_rank'), vals.get('customer_rank')
        if supplier and not customer:
            return 'supplier'
        if customer and not supplier:
            return 'buyer'
        # A child address inherits whichever book its parent sits in.
        if vals.get('parent_id'):
            return self.browse(vals['parent_id']).partner_kind

        if self.env.user.has_group('contact_stage_bar.group_lgd_procurement'):
            return 'supplier'
        if self.env.user.has_group('contact_stage_bar.group_lgd_sales'):
            return 'buyer'
        return False

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [dict(vals) for vals in vals_list]
        for vals in vals_list:
            if not vals.get('partner_kind'):
                kind = self._infer_partner_kind(vals)
                if kind:
                    vals['partner_kind'] = kind
        partners = super(ResPartner, self.sudo()).create(vals_list)
        partners.filtered(lambda p: not p.shipping_address_differs) \
                ._sync_shipping_from_billing()
        return partners

    def write(self, vals):
        res = super().write(vals)
        # A caller that sets shipping values explicitly is left alone.
        if set(vals) & set(self._SHIPPING_ADDRESS_FIELDS):
            return res
        if set(vals) & set(self._SHIPPING_ADDRESS_FIELDS.values()) \
                or 'shipping_address_differs' in vals:
            mirrored = self.filtered(lambda p: not p.shipping_address_differs)
            mirrored._sync_shipping_from_billing()
            if 'shipping_address_differs' in vals:
                (self - mirrored)._clear_shipping_address()
        return res

    @api.model
    def name_create(self, name):
        return super(ResPartner, self.sudo()).name_create(name)

    @api.model
    def _lgd_backfill_vendor_partner_kind(self):
        """Tag partners used as a line vendor as 'supplier'.

        partner_kind is only set at creation, from the menu the record was
        created in. Vendors that arrived through the website API or before the
        bifurcation carry no kind at all, so lgd_procurement_partner_rule -
        which matches on partner_kind = 'supplier' - hid every one of them from
        the role that exists to work with them.

        Partners that are also the customer on a sale order are skipped: the
        LGD Sales rules treat 'supplier' as "not mine", so tagging a partner
        that trades in both directions would hide it from Sales instead.
        """
        vendors = self.env['sale.order.line'].sudo().search(
            [('vendor_id', '!=', False)]).mapped('vendor_id')
        customers = self.env['sale.order'].sudo().search([]).mapped('partner_id')
        to_tag = vendors.filtered(lambda p: not p.partner_kind) - customers
        if to_tag:
            to_tag.sudo().write({'partner_kind': 'supplier'})
        return len(to_tag)
                
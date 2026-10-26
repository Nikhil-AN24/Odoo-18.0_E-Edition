import logging
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

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

    def _lgd_find_state(self, state_name, country_id):
        """A state id, matched whole and inside its own country.

        Whole, because 'ilike' is a substring match: 'California' found
        Mexico's 'Baja California'. Inside its country, because several
        countries use the same state names. Falls back to the unscoped
        exact name only when the country itself did not resolve - a wrong
        country is a separate problem, already logged where it happens.
        """
        name = (state_name or '').strip()
        if not name:
            return None
        State = self.env['res.country.state'].sudo()
        if country_id:
            found = State.search(
                [('country_id', '=', country_id), ('name', '=ilike', name)],
                limit=1)
            if found:
                return found.id
            # Odoo may simply not carry states for this country - it holds
            # none at all for Bahrain, for instance - so say so plainly
            # rather than reaching for another country's state of that name.
            _logger.warning(
                "Onboarding: state %r does not exist in country id %s; "
                "leaving State empty.", name, country_id)
            return None
        found = State.search([('name', '=ilike', name)], limit=1)
        return found.id or None

    @api.model
    def _lgd_upsert_from_website(self, data, enforce_registration=True,
                                 target=None):
        """Create or update a partner from a platform payload.

        Matches on email. Returns the response dict the API replies with;
        a refusal comes back as {'status': 'error', 'message': ...}.

        target names the record to update when the caller has already found
        it by some other key. The order route identifies a vendor by company
        name, while this method matches on email, so without it a vendor
        whose email had changed would be duplicated rather than updated.

        enforce_registration=False accepts a seller that arrives with no
        GSTIN or EIN instead of refusing it. The order route passes False:
        a vendor named on an order is incidental data, and failing the whole
        order - stones, prices, the lot - because a vendor record is thin
        would block revenue over a data-quality problem. The requirement is
        not lost, it moves: an account missing anything simply does not
        advance past Open. Completeness decides the STAGE, not whether the
        call is accepted.
        """
        
        vals = {}
        state = None
        country = None
        city = None
        
        # Country FIRST: the state lookup is scoped to it. Resolving state
        # on its own, against every state in the world, picked the wrong
        # country's - 'California' matched Mexico's 'Baja California',
        # because the old search was a SUBSTRING match ('ilike') with no
        # country filter and limit=1. The account then carried a Mexican
        # state on a United States address.
        input_country = data.get("address", {}).get("country", {}).get("name")
        _logger.info(f"input_country: {input_country}")

        if input_country:
            country = self.env['res.country'].sudo().search([
                ('name', '=', input_country.title())
            ], limit=1).id
        _logger.info(f" country: {country}")

        state_name = data.get("address", {}).get("state", {}).get("name")
        _logger.info(f"Received state: {state_name}")
        if state_name:
            state = self._lgd_find_state(state_name, country)
            _logger.info(f"Matched state: {state}")
                
        full_name = f"{data.get('firstName', '').strip()} {data.get('lastName', '').strip()}".strip()
        _logger.info(f"Full name constructed: {full_name}")
            
        businessType = None
        businessRole = None

        # '=ilike' matches the whole name, case-insensitively. Plain 'ilike'
        # is a SUBSTRING match, and that quietly mis-filed records: sending
        # businessRole 'Owner' attached the account to an existing, unrelated
        # category called 'Tenant ownerships', because that name happens to
        # contain the word. Any short role or type collides the same way.
        def _category(value):
            value = (value or '').strip()
            if not value:
                return self.env['custom.category'].sudo().browse()
            Category = self.env['custom.category'].sudo()
            found = Category.search([('name', '=ilike', value)], limit=1)
            return found or Category.create({'name': value})

        businessType = _category(data.get('businessType'))
        businessRole = _category(data.get('businessRole'))
        
        # ── partyType -> which book the account belongs in ───────────────
        # Without this the account is created with partner_kind empty, and
        # the All Accounts and Vendors menus both filter on it - so the
        # record existed in the database and appeared in no menu at all.
        # Odoo's own fallback reads the logged-in user's groups, which is
        # meaningless for a public API call.
        _PARTY_KIND = {'buyer': 'buyer', 'customer': 'buyer',
                       'seller': 'supplier', 'vendor': 'supplier',
                       'supplier': 'supplier'}
        party = (data.get('partyType') or '').strip().lower()
        partner_kind = _PARTY_KIND.get(party)
        if party and not partner_kind:
            _logger.warning(
                "Onboarding %s: partyType %r is not one of %s; leaving the "
                "account type unset, which hides it from both menus.",
                data.get('email'), data.get('partyType'),
                ', '.join(sorted(set(_PARTY_KIND))))

        # ── A seller must arrive with a registration number ──────────────
        # Enforced here rather than as a model constraint on res.partner: 8
        # of the 24 suppliers already in the database have neither, and a
        # constraint would make every one of them unsavable the next time
        # Procurement edited it. Onboarding is the boundary the rule is
        # about, so this is where it belongs.
        if partner_kind == 'supplier':
            kyc_in = data.get('kyc') or {}
            india = (input_country or '').strip().lower() == 'india'
            wanted = 'kyc.gstin' if india else 'kyc.einNumber'
            got = kyc_in.get('gstin') if india else kyc_in.get('einNumber')
            if not (got or '').strip():
                if enforce_registration:
                    _logger.warning(
                        "Onboarding refused for seller %s: %s is required for "
                        "a seller registered in %s.",
                        data.get('email'), wanted,
                        input_country or 'an unknown country')
                    return {
                        'status': 'error',
                        'message': ("%s is required for a seller registered "
                                    "in %s."
                                    % (wanted,
                                       input_country or 'an unknown country')),
                    }
                _logger.info(
                    "Seller %s arrived on an order without %s; stored, but it "
                    "stays in Open until one is supplied.",
                    data.get('email'), wanted)

        gst_stage_map = {
            'verified': 'Verified',
            'not_verified': 'Not Verified',
            'pending': 'Pending',
        }
        gst_value = data.get("kyc", {}).get('gstinStatus')
        _logger.info(f"GST value: {gst_value}")
        
        ein_value = data.get("kyc", {}).get('einStatus')
        _logger.info(f"EIN value: {ein_value}")
                    
        # ── leadSource -> only the four the field accepts ────────────────
        _LEAD_SOURCES = ('website', 'cold_call', 'email', 'marketing_campaign')
        lead_source = (data.get('leadSource') or '').strip().lower() or None
        if lead_source and lead_source not in _LEAD_SOURCES:
            _logger.warning(
                "Onboarding %s: leadSource %r is not one of %s; ignored.",
                data.get('email'), data.get('leadSource'),
                ', '.join(_LEAD_SOURCES))
            lead_source = None

        # ── A delivery address that differs from the billing one ─────────
        # Unmapped until now, so it could not be stored at all. Odoo copies
        # billing to shipping on create, which hid the gap: it only shows
        # when the two genuinely differ, which is exactly the case the
        # website needs.
        ship = data.get('shippingAddress') or {}
        ship_differs = bool(data.get('shippingAddressDiffers'))
        ship_state = ship_country = None
        if ship_differs:
            ship_country_name = (ship.get('country') or {}).get('name')
            if ship_country_name:
                ship_country = self.env['res.country'].sudo().search(
                    [('name', '=', ship_country_name.title())], limit=1).id
            # Same resolver as the billing address, so the two cannot drift.
            ship_state = self._lgd_find_state(
                (ship.get('state') or {}).get('name'), ship_country)

        mapping = { 
            'name': data.get('companyName'),
            'partner_kind': partner_kind,
            # The menus also require a top-level company record.
            'is_company': True,
            'contact_person_name': data.get('contactPersonName'),
            'lead_source': lead_source,
            'shipping_address_differs': ship_differs or None,
            'shipping_street': ship.get('addressLine1') if ship_differs else None,
            'shipping_street2': ship.get('addressLine2') if ship_differs else None,
            'shipping_city': (ship.get('city') or {}).get('name') if ship_differs else None,
            'shipping_zip': ship.get('pincode') if ship_differs else None,
            'shipping_state_id': ship_state,
            'shipping_country_id': ship_country,
            'first_name':data.get('firstName'),
            'last_name':data.get('lastName'),
            'phone' : data.get('mobileNumber'),
            'email' : data.get('email'),
            'street' : data.get("address",{}). get("addressLine1"),
            'street2' : data.get("address",{}). get("addressLine2"),
            'city': data.get("address", {}).get("city", {}).get("name"),
            'zip' : data.get("address",{}). get("pincode"),
            # .ids, not [.id]: an absent businessType/businessRole gives an
            # empty recordset whose .id is False, and [(6, 0, [False])] is
            # written straight to an integer column.
            'category_ids': [(6, 0, businessType.ids)] if businessType else None,
            'primary_category_ids': [(6, 0, businessRole.ids)] if businessRole else None,
            'state_id' : state,
            'country_id' : country,
            'vat' : data.get("kyc",{}). get('gstin'),
            'ein_number' : data.get("kyc",{}). get('einNumber'),
            'gst_stages' : gst_value,
            'ein_status' : ein_value,
            'l10n_in_pan' : data.get("kyc",{}). get('panNumber')
            }
        _logger.info(f"Mapping values: {mapping}")   
        partner = target or self.env['res.partner'].sudo().search([('email','=',data.get('email'))],limit=1)
        _logger.info(f"Partner found: {partner}")
        
        for field_name, value in mapping.items():
            if value:
                vals[field_name] = value
        
        if partner:
            _logger.info(f"Writing values to partner {partner.id}: {vals}")
            partner.write(vals)
            # Was `partner.stage_id = 15`, a hardcoded id no database holds -
            # this one has only 1-4 - so EVERY repeat sync of an existing
            # account died with MissingError and rolled the update back. A
            # first call succeeded and a second never did.
            #
            # An existing account's stage is also not ours to move: Sales
            # advances it through Contacted, Registered and Converted, and
            # resetting it on each sync would undo that. Only a brand-new
            # account gets a stage, below, and it starts at Open.
            _logger.info(
                "Updated partner %s; stage was %s",
                partner.id, partner.stage_id.name or 'none')
            # The field that was missing may have just arrived.
            partner._lgd_try_advance_stage()
            
            return {
                'status': 'Existing Accounts Updated',
                'name': partner.name,
                'first_name': partner.first_name,
                'last_name': partner.last_name,
                'partner_id': partner.id,
                'stages': partner.stage_id.name,
                'country': partner.country_id.name if partner.country_id else '',
                'Bussiness Type': businessType.name if businessType else '',
                'Bussiness Role': businessRole.name if businessRole else '',
                'phone': partner.phone,
                'email': partner.email,
                'street': partner.street,
                'street2': partner.street2,    
                'city': partner.city,
                'zip': partner.zip, 
                'pan_number': partner.l10n_in_pan,
                'gst_stages': partner.gst_stages,
                'ein_status': partner.ein_status,
                'Gst Number': partner.vat,
                'Ein Number': partner.ein_number,
            }
            
        else:
            # A new account starts at Open. Registered and Converted are
            # gated on the full field list, so an account that arrives
            # incomplete is still stored for Sales to finish rather than
            # being refused. Resolved by xmlid, never a hardcoded id.
            open_stage = self.env.ref(
                'contact_stage_bar.open_stage', raise_if_not_found=False)
            if open_stage:
                vals.setdefault('stage_id', open_stage.id)
            _logger.info(f"Creating new partner with vals: {vals}")
            new_partner = self.env['res.partner'].sudo().create(vals)
            _logger.info(f"Created new partner {new_partner.id} with vals: {vals}")
            # A payload complete on arrival need not wait in Open.
            new_partner._lgd_try_advance_stage()
            return {
                'status': 'New Accounts Company Created',
                'name': new_partner.name,
                'first_name': new_partner.first_name,
                'last_name': new_partner.last_name,
                'stages': new_partner.stage_id.name,
                'partner_id': new_partner.id,
                'Bussiness Type': businessType.name if businessType else '',
                'Bussiness Role': businessRole.name if businessRole else '',    
                'phone': new_partner.phone,
                'email': new_partner.email, 
                'street': new_partner.street,
                'street2': new_partner.street2,
                'city': new_partner.city,
                'zip': new_partner.zip,
                'pan_number': new_partner.l10n_in_pan,
                'gst_stages': new_partner.gst_stages,
                'ein_status': new_partner.ein_status,
                'Gst Number': new_partner.vat,
                'Ein Number': new_partner.ein_number,
                'state': new_partner.state_id.name if new_partner.state_id else '',
                'city': new_partner.city,
                'zip': new_partner.zip,
                'country': new_partner.country_id.name if new_partner.country_id else '',
            }

    _LGD_ACCOUNT_REQUIRED_FIELDS = (
        ('name',                "Name"),
        ('street',              "Street"),
        ('street2',             "Street2"),
        ('city',                "City"),
        ('zip',                 "Zip"),
        ('state_id',            "State"),
        ('country_id',          "Country"),
        ('contact_person_name', "Contact Person Name"),
        ('phone',               "Phone"),
        ('email',               "Email"),
        ('lead_source',         "Lead Source"),
    )

    #: The registration number, which one depending on where the account is
    #: registered: GSTIN in India, EIN everywhere else. Demanding GSTIN of
    #: everyone blocked every foreign account - most of the platform's
    #: buyers are registered in Bahrain and have no GSTIN at all. Mirrors
    #: the split cart_add already makes on buyer_country_code == '91'.
    _LGD_INDIA_COUNTRY_CODE = 'IN'

    def _lgd_missing_registration_number(self):
        """'Tax ID' / 'EIN Number' when the one this account needs is
        missing, else None."""
        self.ensure_one()
        if self.country_id.code == self._LGD_INDIA_COUNTRY_CODE:
            return None if self.vat else "Tax ID (GSTIN)"
        return None if self.ein_number else "EIN Number"

    _LGD_GATED_STAGE_XMLIDS = (
        'contact_stage_bar.registered_stage',
        'contact_stage_bar.converted_stage',
    )

    def _lgd_gated_stage_ids(self):
        """Resolve the gated stages, skipping any that no longer exist."""
        ids = []
        for xmlid in self._LGD_GATED_STAGE_XMLIDS:
            stage = self.env.ref(xmlid, raise_if_not_found=False)
            if stage:
                ids.append(stage.id)
        return ids

    #: Stages an account may be advanced OUT OF automatically. Converted is
    #: absent on purpose: an account Sales has already converted must never
    #: be dragged backwards by a later sync, and Registered is where this
    #: lands anyway.
    _LGD_AUTO_ADVANCE_FROM = ('open', 'contacted')

    def _lgd_try_advance_stage(self):
        """Move a complete account to Registered on its own.

        An account is complete when every field in the gate is filled and it
        carries the registration number its country requires - exactly the
        test _lgd_validate_stage_gate applies. Until now that test only ever
        said no; this lets it say yes, so a lead whose last missing field
        arrives is promoted without anybody opening the form.

        Only ever forwards, and never out of Converted.
        """
        Stage = self.env['res.partner.stage'].sudo()
        registered = self.env.ref('contact_stage_bar.registered_stage',
                                  raise_if_not_found=False)
        if not registered:
            registered = Stage.search([('name', '=ilike', 'Registered')],
                                      limit=1)
        if not registered:
            _logger.warning(
                "No Registered stage in this database; leaving stages alone.")
            return
        for partner in self:
            if partner.parent_id and partner.type != 'contact':
                continue
            current = (partner.stage_id.name or '').strip().lower()
            if current not in self._LGD_AUTO_ADVANCE_FROM:
                continue
            missing = [
                label for fname, label in self._LGD_ACCOUNT_REQUIRED_FIELDS
                if not partner[fname]
            ]
            if partner._lgd_missing_registration_number():
                continue
            if missing:
                continue
            partner.stage_id = registered.id
            _logger.info(
                "Partner %s is complete; advanced %s -> Registered.",
                partner.id, current or 'none')

    def _lgd_validate_stage_gate(self):
        for partner in self:

            if partner.parent_id and partner.type != 'contact':
                continue
            missing = [
                label for fname, label in self._LGD_ACCOUNT_REQUIRED_FIELDS
                if not partner[fname]
            ]
            needed = partner._lgd_missing_registration_number()
            if needed:
                missing.append(needed)
            if missing:
                raise ValidationError(_(
                    "%(partner)s cannot move to %(stage)s yet.\n\n"
                    "Complete these fields first:\n%(missing)s"
                ) % {
                    'partner': partner.display_name or _("This account"),
                    'stage': partner.stage_id.name,
                    'missing': "\n".join("  \u2022 %s" % m for m in missing),
                })

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
        gated = partners._lgd_gated_stage_ids()
        if gated:
            partners.filtered(
                lambda p: p.stage_id.id in gated)._lgd_validate_stage_gate()
        return partners

    def write(self, vals):
        # Capture which records are already in a gated stage BEFORE the write,
        # so an edit to an existing Registered account is not mistaken for a move into one.
        gate_candidates = self.browse()
        if 'stage_id' in vals:
            gated = self._lgd_gated_stage_ids()
            if gated and vals['stage_id'] in gated:
                gate_candidates = self.filtered(
                    lambda p: p.stage_id.id != vals['stage_id'])
        res = super().write(vals)
        if gate_candidates:
            gate_candidates._lgd_validate_stage_gate()
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

        po_vendors = self.env['purchase.order'].sudo().search(
            [('partner_id', '!=', False)]).mapped('partner_id')
        vendors = vendors | po_vendors
        customers = self.env['sale.order'].sudo().search([]).mapped('partner_id')
        to_tag = vendors.filtered(lambda p: not p.partner_kind) - customers
        if to_tag:
            to_tag.sudo().write({'partner_kind': 'supplier'})
        return len(to_tag)

    @api.depends('vat', 'state_id', 'country_id', 'fiscal_country_codes')
    def _compute_l10n_in_gst_state_warning(self):

        bad = self.filtered(lambda p: not isinstance(p.fiscal_country_codes, str))
        for partner in bad:
            partner.l10n_in_gst_state_warning = False
        remaining = self - bad
        if remaining:
            super(ResPartner, remaining)._compute_l10n_in_gst_state_warning()
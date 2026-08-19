import requests
import json
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError


class CustomerOnboarding(models.Model):
    _name = 'customer.onboarding'
    _description = 'Customer Onboarding'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(
        string='Reference',
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: _('New'),
    )

    # ── Record Owner ───────────────────────────────────────────────────────
    user_id = fields.Many2one(
        'res.users', 
        string='Sales Person', 
        default=lambda self: self.env.user, 
        tracking=True,
        help="The user who owns/manages this onboarding record."
    )

    # ── Company details ────────────────────────────────────────────────────
    partner_id = fields.Many2one(
        'res.partner', string='Company Name', tracking=True, required=True,
    )

    email = fields.Char(
        string='Email',
        related='partner_id.email',
        readonly=False,
        tracking=True,
    )

    @api.model
    def _get_country_code_selection(self):
        countries = self.env['res.country'].sudo().search([])
        selection = []
        for c in countries:
            flag = ''
            if c.code and len(c.code) == 2:
                try:
                    flag = chr(ord(c.code[0].upper()) + 127397) + chr(ord(c.code[1].upper()) + 127397)
                except Exception:
                    flag = ''
            phone_code = f" (+{c.phone_code})" if c.phone_code else ""
            name = f"{flag} {c.name}{phone_code}"
            selection.append((str(c.id), name))
        return selection

    country_code = fields.Selection(
        selection='_get_country_code_selection',
        string='Country Code',
    )
    
    city = fields.Char(string='City', related='partner_id.city', readonly=False, store=True, tracking=True)
    state_id = fields.Many2one('res.country.state', string='State', related='partner_id.state_id', readonly=False, store=True, tracking=True)
    zip = fields.Char(string='Zip Code', related='partner_id.zip', readonly=False, store=True, tracking=True)

    is_india = fields.Boolean(compute='_compute_is_india', string="Is India?")

    @api.depends('country_code')
    def _compute_is_india(self):
        """Check if the dynamically selected country_code is India."""
        india = self.env.ref('base.in', raise_if_not_found=False)
        india_id_str = str(india.id) if india else False
        for rec in self:
            rec.is_india = bool(rec.country_code and rec.country_code == india_id_str)

    @api.onchange('partner_id')
    def _onchange_country_code_custom(self):
        """Auto-fill Country Code from the selected Customer's country."""
        for rec in self:
            if rec.partner_id and rec.partner_id.country_id:
                rec.country_code = str(rec.partner_id.country_id.id)
            else:
                rec.country_code = False

    mobile_number = fields.Char(
        string='Mobile Number', related='partner_id.phone', readonly=False, tracking=True, )

    vat = fields.Char(string='GST Number', tracking=True)
    ein_number = fields.Char(string='Company Registered Number', tracking=True)

    gst_treatment = fields.Selection([
        ('within_maharashtra', 'Within Maharashtra'), ('outside_maharashtra', 'Outside Maharashtra'),
    ], string='Gst Treatment', tracking=True)

    location = fields.Selection(
        [('mumbai', 'India'), ('surat', 'USA')],
        string='Location',
        tracking=True,
    )

    # ── Order related details ───────────────────────────────────────────────
    quotation_date = fields.Date(
        string='Quotation Date', default=fields.Date.context_today, tracking=True,
    )

    pricelist_id = fields.Many2one(
        'product.pricelist', string='Pricelist', tracking=True,
    )

    shipping_address = fields.Text(string='Shipping Address')
    other_address = fields.Text(string='Other Address')
    
    # ── KYB Status Field ───────────────────────────────────────────────────
    verification_status = fields.Selection([
        ('verified', 'Verified'),
        ('not_verified', 'Not Verified')
    ], string='Verification Status', tracking=True, readonly=True)

    # ── Fields for PAN Card & GST Document ─────────────────────────────
    pan_gst_document = fields.Binary(
        string='PAN Card & GST Document', 
        attachment=True
    )
    pan_gst_filename = fields.Char(string='Document Filename')

    @api.constrains('pan_gst_document', 'pan_gst_filename')
    def _check_pdf_format(self):
        """Ensure the uploaded document is strictly a PDF."""
        for record in self:
            if record.pan_gst_document and record.pan_gst_filename:
                if not record.pan_gst_filename.lower().endswith('.pdf'):
                    raise ValidationError(_("Only PDF documents are allowed for the PAN Card & GST Document field."))

    active = fields.Boolean(default=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('customer.onboarding') or _('New')
        
        records = super(CustomerOnboarding, self).create(vals_list)
        for record in records:
            if record.partner_id and record.user_id:
                record.partner_id.user_id = record.user_id
        return records

    def write(self, vals):
        res = super(CustomerOnboarding, self).write(vals)
        for record in self:
            if 'user_id' in vals or 'partner_id' in vals:
                if record.partner_id and record.user_id:
                    record.partner_id.user_id = record.user_id
        return res

    # ── Verification Actions ───────────────────────────────────────────────
    def action_verify_gst(self):
        self.ensure_one()
        if not self.vat:
            return self._show_error_popup(_("Please enter a GST Number first."))
        return self._perform_kyb_api_call(self.vat.strip())

    def action_verify_ein(self):
        self.ensure_one()
        if not self.ein_number:
            return self._show_error_popup(_("Please enter a Company Registered Number first."))
        return self._perform_kyb_api_call(self.ein_number.strip())

    def action_manual_verify(self):
        """Manually sets the status to verified and logs it in the chatter."""
        for rec in self:
            if rec.verification_status != 'verified':
                rec.verification_status = 'verified'
                rec.message_post(body=_("Verification Status was manually set to 'Verified'."))

    def _get_api_access_token(self):
        """Fetches a fresh access token using urlencoded data payload."""
        token_url = self.env['ir.config_parameter'].sudo().get_param('idmerit.token_url', '').strip()
        username = self.env['ir.config_parameter'].sudo().get_param('idmerit.username', '').strip()
        password = self.env['ir.config_parameter'].sudo().get_param('idmerit.password', '').strip()

        if not token_url or not username or not password:
            return False

        payload = {'username': username, 'password': password}
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}

        try:
            response = requests.post(token_url, data=payload, headers=headers, timeout=15, verify=False)
            if response.status_code != 200:
                return False
            data = response.json()
            return data.get('access_token')
        except requests.exceptions.RequestException:
            return False

    def _perform_kyb_api_call(self, search_value):
        """Verifies company details using IDMERIT's strict region-specific payload rules."""
        api_url = self.env['ir.config_parameter'].sudo().get_param('idmerit.api_url', '').strip()
        if not api_url:
            self.verification_status = 'not_verified'
            return self._show_error_popup(_("IDMERIT API endpoint URL is not configured."))

        access_token = self._get_api_access_token()
        if not access_token:
            self.verification_status = 'not_verified'
            return self._show_error_popup(_("Failed to authenticate with IDMERIT. Check credentials."))

        headers = {
            'Authorization': f'Bearer {access_token}',
        }
        
        # ── RESOLVE CORRECT COUNTRY ISO CODE ──
        country_iso = 'IN' if self.is_india else 'US'
        if self.country_code:
            try:
                country_record = self.env['res.country'].browse(int(self.country_code))
                if country_record.exists() and country_record.code:
                    country_iso = country_record.code.upper()
            except Exception:
                pass
        elif self.partner_id.country_id and self.partner_id.country_id.code:
            country_iso = self.partner_id.country_id.code.upper()

        # ── STRICT REGIONAL PAYLOADS ──
        if country_iso == 'IN':
            # IDMERIT Mandate: Indian GST searches MUST use JSON and specific ID parameters
            headers['Content-Type'] = 'application/json'
            payload = {
                "country_code": "IN",
                "id_type": "1",  # 1 denotes GST lookup
                "id_num": search_value,
                "source": "5",
                "request_id": self.name or "0001"
            }
        else:
            # Standard URL-Encoded payload for US, Canada, etc.
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
            payload = {
                "company_name": self.partner_id.name or "", 
                "country_code": country_iso,
                "request_id": self.name or "0001",
                "company_number": search_value
            }
            
            # IDMERIT STRICT RULE: ONLY send the state parameter for the US. 
            if country_iso == 'US':
                if self.state_id:
                    payload['state'] = self.state_id.code or self.state_id.name
                else:
                    self.verification_status = 'not_verified'
                    return self._show_error_popup(_("The IDMERIT API strictly requires a State parameter for the US. Please select a State before verifying."))

        try:
            # Dispatch correctly formatted request based on region
            if headers['Content-Type'] == 'application/json':
                response = requests.post(api_url, headers=headers, json=payload, timeout=15, verify=False)
            else:
                response = requests.post(api_url, headers=headers, data=payload, timeout=15, verify=False)
            
            if response.status_code != 200 or response.json().get('status') is False:
                self.verification_status = 'not_verified'
                self.env.cr.commit() 
                
                try:
                    response_json = response.json()
                    err_msg = response_json.get('message') or response_json.get('data') or 'Verification failed.'
                    if isinstance(err_msg, dict):
                        err_msg = "Validation Error"
                        
                    val_data = response_json.get('validation')
                    if val_data:
                        err_lines = []
                        if isinstance(val_data, dict):
                            for field_name, err_list in val_data.items():
                                if isinstance(err_list, list):
                                    flat_errs = []
                                    for item in err_list:
                                        if isinstance(item, list):
                                            flat_errs.extend(item)
                                        else:
                                            flat_errs.append(str(item))
                                    if flat_errs:
                                        err_lines.append(f"• {field_name.upper()}: {', '.join(flat_errs)}")
                                else:
                                    err_lines.append(f"• {field_name.upper()}: {str(err_list)}")
                        elif isinstance(val_data, list):
                            err_lines.append(f"• Invalid Parameters: {', '.join(val_data)}")
                            
                        if err_lines:
                            err_msg = str(err_msg) + "\n\nFields to correct:\n" + "\n".join(err_lines)
                    
                    return self._show_error_popup(str(err_msg))
                except Exception:
                    return self._show_error_popup(_(f"API Error [{response.status_code}]:\n{response.text}"))

            response_json = response.json()
            api_data = response_json.get('data') or response_json.get('result')
            
            if isinstance(api_data, str) and "not found" in api_data.lower():
                self.verification_status = 'not_verified'
                self.env.cr.commit() 
                return self._show_error_popup(_(f"IDMERIT Search Failed:\n\n{api_data}"))
                
            if isinstance(api_data, list) and len(api_data) > 0:
                api_data = api_data[0]
            
            if not api_data or not isinstance(api_data, dict):
                api_data = response_json

            # Look for every possible name key IDMERIT might use
            fetched_name = (
                api_data.get('company_name') or 
                api_data.get('name') or 
                api_data.get('business_name') or 
                api_data.get('legal_name') or 
                api_data.get('trade_name') or 
                api_data.get('tradename') or 
                ''
            )
            
            if response_json.get('status') is True and fetched_name:
                self.verification_status = 'verified'
                self.env.cr.commit() 
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Verification Successful'),
                        'message': _('Company details fetched and verified successfully.'),
                        'type': 'success',
                        'sticky': False,
                        'next': {'type': 'ir.actions.client', 'tag': 'reload'}
                    }
                }
            elif response_json.get('status') is True and not fetched_name:
                self.verification_status = 'not_verified'
                self.env.cr.commit() 
                return self._show_error_popup(f"Record found, but IDMERIT name keys do not match. RAW DATA:\n\n{json.dumps(response_json)}")
            else:
                self.verification_status = 'not_verified'
                self.env.cr.commit() 
                return self._show_error_popup(_("Company not found or invalid match."))

        except requests.exceptions.RequestException as e:
            self.verification_status = 'not_verified'
            self.env.cr.commit()
            return self._show_error_popup(_(f"Network Error:\n{str(e)}"))

    def _show_error_popup(self, message):
        """Helper to return a red sticky notification."""
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('IDMERIT Verification Failed'),
                'message': message,
                'type': 'danger',
                'sticky': True,
            }
        }
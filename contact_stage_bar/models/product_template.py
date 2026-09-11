import email
from odoo import models, fields, api
from odoo.exceptions import ValidationError,UserError
from odoo.tools.sql import column_exists
from odoo.http import request
import requests
from odoo import _
from markupsafe import Markup
import re
import tempfile

import logging
_logger = logging.getLogger(__name__)


class ProductTemplate(models.Model):
    _inherit = "product.template" 

    labs = fields.Char(
        string='LAB',
        help="Grading lab, as sent by the Augmont website (e.g. IGI, WISE, "
             "No-cert). IGI — or blank — auto-fetches specs from the "
             "certificate number; any other lab is manual entry only.",
    )
    # Replaces the former lab_id selection, which duplicated labs.
    is_igi_lab = fields.Boolean(compute='_compute_is_igi_lab')
    website_product_id = fields.Char()
    certificate = fields.Char(string="Certificate Number")
    certificate_type = fields.Char(string="Certificate Type")
    purchase_ok = fields.Boolean(default=False)
    is_non_certified_source = fields.Boolean(
        string="Non-Certified Source",
        default=False,
        help="True when this product record was spawned from a Non-Certified RFQ.",
    )

    # True when the current user belongs to any LGD Sales role — used by the
    # product form to make the General Information tab viewable-only for
    # Sales groups. Procurement roles and Admin see it editable as usual.
    is_readonly_for_sales_view = fields.Boolean(
        string="Readonly for Sales",
        compute='_compute_is_readonly_for_sales_view',
    )

    @api.depends_context('uid')
    def _compute_is_readonly_for_sales_view(self):
        user = self.env.user
        is_sales = (
            user.has_group('contact_stage_bar.group_lgd_sales')
            or user.has_group('contact_stage_bar.group_lgd_regional_sales_head')
            or user.has_group('contact_stage_bar.group_lgd_sales_manager')
        )
        is_admin = user.has_group('base.group_system')
        is_procurement = (
            user.has_group('contact_stage_bar.group_lgd_procurement')
            or user.has_group('contact_stage_bar.group_lgd_procurement_manager')
        )
        for rec in self:
            rec.is_readonly_for_sales_view = is_sales and not (is_admin or is_procurement)
    
    measurements = fields.Char(string="Measurements")
    weight = fields.Char(string="Carat Weight")
    # ── Diamond spec fields ──────────────────────────────────────────────────
    weight_carat = fields.Char(string="Carat Weight", tracking=True)
    color = fields.Char(string="Color", tracking=True)
    clarity = fields.Char(string="Clarity", tracking=True)
    polish = fields.Char(string='Polish', tracking=True)
    cut = fields.Char(string='Cut', tracking=True)
    luster = fields.Char(string="Luster Grade")
    symmetry = fields.Char(string='Symmetry', tracking=True)
    treatments = fields.Char(string='Treatments')
    shapes = fields.Char(string="Shapes", tracking=True)
    shade = fields.Char(string="Shade")
    url_link = fields.Char(string="URL Link",compute='_compute_url_link', store=True)
    image_augmont = fields.Char(string="Image")
    video_360 = fields.Char(string="Diamond 360 Video")
    final_price = fields.Float(string="Vendor Final Price")
    final_price_margin = fields.Float(string="Final Price")
    price_per_carat = fields.Float(string="Vendor Price Per Carat")
    country_id = fields.Many2one('res.country',string="Country")
    stock_number = fields.Char(string="Vendor Stock Number")
    lgd_stock_number = fields.Char(string="Website Stock Number")
    
    length = fields.Float(string="Length")
    width = fields.Float(string="Width")
    depth = fields.Float(string="Depth")
    eye_clean = fields.Char(string="Eye Clean")
    fluorescence_color = fields.Char(string="Fluorescence Color")
    fluorescence_intensity = fields.Char(string="Fluorescence Intensity")
    
    order_specific = fields.Char(string="Order Specific Notes")
    item_notes = fields.Char(string="General Order Notes")

    customer_reference_note = fields.Char(string="Customer Reference Note")
    default_qc_requirements = fields.Char(string="Default Qc Requirements")
    
    
    is_show_product = fields.Boolean(string="Is Show Product", default=False)
    webiste_product_id = fields.Char(string="Website Product id")

    sdk_is_code_enabled = fields.Boolean(string="Is Code Enabled", default=False, copy=False)
    
    def action_combine_sdk_fields(self):
        GRADE_SHORT_NAMES = {
            'EXCELLENT': 'EX',
            'VERY GOOD': 'VG',
            'GOOD': 'G',
            'FAIR': 'F',
            'POOR': 'P',
            'IDEAL': 'ID',
        }

        for record in self:
            parts = []

            # Shape
            if record.shapes:
                first_word = record.shapes.split()[0].capitalize()
                parts.append(first_word)
                
            # Weight
            if record.weight_carat:
                try:
                    numeric_str = re.findall(r"\d+\.\d+|\d+", str(record.weight_carat))[0]
                    weight_float = float(numeric_str)
                    parts.append(f"{weight_float:.2f}ct")
                except (ValueError, IndexError):
                    parts.append(f"{record.weight_carat}ct")

            
            # Color
            if record.color:
                parts.append(record.color.upper())
                
            # Clarity
            if record.clarity:
                parts.append(record.clarity)
            
                
             # Cut
            if record.cut:
                short = GRADE_SHORT_NAMES.get(record.cut.upper(), record.cut)
                parts.append(short)

            

            # Polish
            if record.polish:
                short = GRADE_SHORT_NAMES.get(record.polish.upper(), record.polish)
                parts.append(short)

            

            # Symmetry
            if record.symmetry:
                short = GRADE_SHORT_NAMES.get(record.symmetry.upper(), record.symmetry)
                parts.append(short)

            # Combine everything
            combined_name = " ".join(parts)
            record.name = combined_name
            record.sdk_is_code_enabled = True

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)

        # if sale_order_id is passed in context → add line
        sale_order_id = self.env.context.get('default_order_id')
        if sale_order_id:
            order = self.env['sale.order'].browse(sale_order_id)
            for record in records:
                if order and (record.certificate or record.lgd_stock_number or record.stock_number):
                    # Add product line to order
                    order.order_line.create({
                        'order_id': order.id,
                        'product_id': record.product_variant_id.id,
                        'product_template_id': record.id,
                        'name': record.product_variant_id.name,
                        'product_uom_qty': 1,
                        'price_unit': record.final_price_margin if record.final_price_margin > 0 else record.final_price,
                        # 'vendor_id' : record.seller_ids[0].id
                        'vendor_id': record.seller_ids[:1].partner_id.id if record.seller_ids else False,
                    })
                    # raise ValidationError(f"{record.final_price_margin}  {record.final_price}")
        return records
    
    @api.constrains('name')
    def _check_unique_name(self):
        # Override to disable the name uniqueness check
        return

    @api.depends('certificate')
    def _compute_url_link(self):
        for record in self:
            if record.certificate:
                record.url_link = f"https://api.igi.org/viewpdf.php?r={record.certificate}"
                record.barcode = record.certificate
            else:
                record.url_link = False

    @api.depends('labs')
    def _compute_is_igi_lab(self):
        # Blank counts as IGI, so records with no lab still auto-fetch.
        for record in self:
            record.is_igi_lab = (record.labs or 'IGI').strip().upper() == 'IGI'

    @api.model
    def _lgd_backfill_labs_from_lab_id(self):
        cr = self.env.cr
        if not column_exists(cr, 'product_template', 'lab_id'):
            return
        cr.execute("""
            UPDATE product_template
               SET labs = CASE lab_id WHEN 'igi' THEN 'IGI'
                                      WHEN 'gia' THEN 'GIA'
                                      ELSE 'Other' END
             WHERE lab_id IS NOT NULL AND COALESCE(labs, '') = ''
        """)
        if cr.rowcount:
            _logger.info("Copied lab_id into labs on %s product(s)", cr.rowcount)
            self.invalidate_model(['labs'])

    @api.onchange('certificate')
    def _onchange_certificate_autofetch(self):

        from ..services import igi_service
        if not self.certificate:
            return
        if not self.is_igi_lab:
            # Lab explicitly marked non-IGI — manual entry only, never call out.
            return
        try:
            result = igi_service.fetch_by_report_number(self.env, self.certificate)
        except Exception:
            return
        if not result or result.get('error'):
            return
        vals = self._igi_build_vals(result)
        for field_name, value in vals.items():
            self[field_name] = value
    
    def copy(self, default=None):
        self.ensure_one()
        if default is None:
            default = {}
        default['name'] = '[DUPLICATE]'  
        return super().copy(default)

                

    def action_fetch_igi_data(self):
        from ..services import igi_service

        self.ensure_one()
        if not self.certificate:
            raise UserError(_("Please enter a Certificate Number first."))
        if not self.is_igi_lab:
            raise UserError(_(
                "This record is marked as a non-IGI lab. IGI fetch does not "
                "apply — please enter specs manually."
            ))

        result = igi_service.fetch_by_report_number(self.env, self.certificate)

        if not result:
            return self._igi_notify('warning', _("IGI returned no data."))
        if result.get('error') == 'not_found':
            return self._igi_notify(
                'warning',
                _("IGI could not find report %s. Please check the number or "
                  "enter specs manually.") % self.certificate,
            )
        if result.get('error') == 'unavailable':
            return self._igi_notify(
                'warning',
                _("IGI is temporarily unavailable. Please enter specs "
                  "manually; you can retry later."),
            )

        vals = self._igi_build_vals(result)
        if vals:
            self.write(vals)

        return self._igi_notify('success', _("IGI specs applied."))

    def _igi_build_vals(self, data):
        """Map normalised IGI data → product.template field vals."""
        vals = {}

        def _set(field, val):
            if val in (None, '', False):
                return
            vals[field] = val

        _set('shapes',                 (data.get('shape') or '').title() or None)
        _set('weight_carat',           str(data.get('carat_value')) if data.get('carat_value') else None)
        _set('color',                  data.get('color'))
        _set('clarity',                data.get('clarity_norm'))
        _set('cut',                    data.get('cut'))
        _set('polish',                 data.get('polish'))
        _set('symmetry',               data.get('symmetry'))
        _set('fluorescence_intensity', data.get('fluorescence'))
        _set('measurements',           data.get('measurements'))
        _set('length',                 data.get('length_mm'))
        _set('width',                  data.get('width_mm'))
        _set('depth',                  data.get('depth_mm'))
        _set('labs',                   'IGI')
        _set('certificate_type',       'IGI')


        name = self._igi_compose_name(data)
        if name:
            _set('name', name)

        return vals

    _IGI_GRADE_SHORT = {
        'EXCELLENT': 'EX', 'VERY GOOD': 'VG', 'GOOD': 'G',
        'FAIR': 'F', 'POOR': 'P', 'IDEAL': 'ID',
    }

    # Fields that feed the trade-format product name.
    _NAME_SPEC_FIELDS = (
        'shapes', 'weight_carat', 'color', 'clarity',
        'cut', 'polish', 'symmetry',
    )

    def _compose_trade_name(self, shape=None, weight_carat=None, color=None,
                            clarity=None, cut=None, polish=None, symmetry=None):
        def _short(val):
            if not val:
                return None
            return self._IGI_GRADE_SHORT.get(str(val).strip().upper(), str(val))

        def _carat(val):
            if val in (None, '', False):
                return None
            import re as _re
            m = _re.search(r'([\d.]+)', str(val))
            if not m:
                return None
            try:
                return f"{float(m.group(1)):.2f}ct"
            except ValueError:
                return None

        parts_4c = []
        if shape:
            parts_4c.append(str(shape).split()[0].capitalize())
        c = _carat(weight_carat)
        if c:
            parts_4c.append(c)
        if color:
            parts_4c.append(str(color).upper())
        if clarity:
            parts_4c.append(str(clarity))

        parts_grades = [g for g in (_short(cut), _short(polish), _short(symmetry)) if g]

        if not parts_4c:
            return None
        if parts_grades:
            return " ".join(parts_4c) + " - " + " ".join(parts_grades)
        return " ".join(parts_4c)

    def _igi_compose_name(self, data):
        """Compose the trade name from a normalised IGI dict."""
        return self._compose_trade_name(
            shape=data.get('shape'),
            weight_carat=data.get('carat_value'),
            color=data.get('color'),
            clarity=data.get('clarity_norm'),
            cut=data.get('cut'),
            polish=data.get('polish'),
            symmetry=data.get('symmetry'),
        )

    def _name_from_self(self):
        """Compose the trade name from the record's own field values."""
        self.ensure_one()
        return self._compose_trade_name(
            shape=self.shapes, weight_carat=self.weight_carat,
            color=self.color, clarity=self.clarity,
            cut=self.cut, polish=self.polish, symmetry=self.symmetry,
        )

    @api.onchange(*_NAME_SPEC_FIELDS)
    def _onchange_spec_fields_update_name(self):
        """Live-update Name in the UI as the user edits any of the 7 spec fields."""
        for rec in self:
            new_name = rec._name_from_self()
            if new_name and new_name != rec.name:
                rec.name = new_name

    def write(self, vals):
        touched = [f for f in self._NAME_SPEC_FIELDS if f in vals]
        old_snapshot = {}
        if touched:
            for rec in self:
                old_snapshot[rec.id] = {f: rec[f] for f in touched}

        result = super().write(vals)

        # Recompose Name.
        if touched and 'name' not in vals:
            for rec in self:
                new_name = rec._name_from_self()
                if new_name and new_name != rec.name:
                    super(ProductTemplate, rec).write({'name': new_name})

        # Mirror to any Sale Order that has a line for this product.
        if touched:
            self._mirror_spec_change_to_sale_orders(old_snapshot, touched)

        return result

    # ── Field-name → human label (matches the strings shown on the form) ────
    _SPEC_FIELD_LABELS = {
        'shapes': 'Shape', 'weight_carat': 'Carat Weight', 'color': 'Color',
        'clarity': 'Clarity', 'cut': 'Cut', 'polish': 'Polish',
        'symmetry': 'Symmetry',
    }

    def _mirror_spec_change_to_sale_orders(self, old_snapshot, touched):

        SaleOrder = self.env['sale.order'].sudo()
        for rec in self:
            diffs = []
            for f in touched:
                old = old_snapshot.get(rec.id, {}).get(f)
                new = rec[f]
                if (old or '') == (new or ''):
                    continue
                label = self._SPEC_FIELD_LABELS.get(f, f)
                diffs.append(
                    Markup("<li><b>%s:</b> %s → %s</li>") % (
                        label, old or '(empty)', new or '(empty)')
                )
            if not diffs:
                continue
            orders = SaleOrder.search([
                ('order_line.product_template_id', '=', rec.id),
            ])
            if not orders:
                continue
            body = Markup(
                "<p>Product <b>%s</b> updated:</p><ul>%s</ul>"
            ) % (rec.name or '', Markup('').join(diffs))
            for order in orders:
                order.message_post(body=body)

    def _igi_notify(self, level, message):
        # Chain a soft-reload after success so the populated fields appear without needing a manual refresh.
        params = {
            'title': _("IGI"),
            'message': message,
            'type': level,
            'sticky': level != 'success',
        }
        if level == 'success':
            params['next'] = {'type': 'ir.actions.client', 'tag': 'soft_reload'}
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': params,
        }

    def action_fetch_certificate_data(self):
        """
        Fetch certificate/vendor product data from Augmont API and update record fields accordingly.
        """
        for rec in self:
            number_id = rec.certificate or rec.stock_number or rec.lgd_stock_number
            if not number_id:
                raise UserError("No certificate/stock number found for this record.")
            Param = self.env['ir.config_parameter'].sudo()
            url = Param.get_param('base_augmont_url')
            url = f"{url}/api/v1/odoo/product?id={number_id}&country=IN"
            headers = {
                "Authorization": (
                    "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJtZXJjaGFudElkIjoiZjA3NDA3YzItZTI1ZS00YjI2LTk1MWUtODliZGQxZjI4YmQ2Iiwic2hvcnROYW1lIjoib2RvbyIsImlhdCI6MTc2MDYxNjU3OCwiZXhwIjoyMDc2MTkyNTc4fQ.XpKKN78VVCezg6BrEhQYxvTs1FTmES59pyrp3zFpmdI"
                )
            }

            _logger.info("Fetching certificate data for product %s | URL: %s", number_id, url)

            try:
                response = requests.get(url, headers=headers, timeout=15)
                _logger.debug("API Response Code: %s | Text: %s", response.status_code, response.text[:500])

                if response.status_code != 200:
                    rec.description = f"API error: {response.status_code}"
                    _logger.error("API error code %s | URL: %s", response.status_code, url)
                    continue

                try:
                    res_json = response.json()
                    _logger.debug("Parsed JSON: %s", res_json)
                except Exception as e:
                    _logger.error("JSON parse error: %s", e)
                    raise UserError("API did not return valid JSON data")

                if not res_json.get("success") or not res_json.get("data"):
                    msg = res_json.get("message") or "No data found in API response."
                    rec.description = msg
                    _logger.warning("No product data found: %s", msg)
                    continue

                record = res_json.get("data", {})
                _logger.info("Fetched product record: %s", record)

                rec.website_product_id = record.get("id") or ""
                rec.measurements = record.get("measurements") or ""
                rec.list_price = record.get("pricePerCaratMargin") or record.get("pricePerCarat")
                rec.standard_price = record.get("finalPrice")
                rec.cut = record.get("cut") or False
                rec.clarity = record.get("clarity") or False
                rec.color = record.get("color") or False
                rec.shapes = record.get("shape") or False
                rec.weight_carat = record.get("weight") or 0.0
                rec.shade = record.get("shade") or False
                rec.treatments = record.get("treatment") or False
                rec.certificate = record.get("certNumber") or False
                rec.certificate_type = record.get("certType") or False
                rec.stock_number = record.get("stockNum") or False
                rec.lgd_stock_number = record.get("lgdStockNum") or False
                rec.video_360 = record.get("diamondVideo360") or False
                rec.image_augmont = record.get("diamondImage") or False
                rec.symmetry = record.get("symmetry") or False
                rec.polish = record.get("polish") or False
                rec.eye_clean = record.get("eyeClean") or False
                rec.length = record.get("length") or 0.0
                rec.width = record.get("width") or 0.0
                rec.depth = record.get("height") or 0.0
                rec.final_price = record.get("finalPrice")
                rec.final_price_margin = record.get("finalPriceMargin") or 0.0
                rec.price_per_carat = record.get("pricePerCaratMargin") or 0.0


                rec.barcode = record.get("certNumber") or False
                rec.fluorescence_intensity  = record.get("fluorescenceIntensity") or False
                rec.fluorescence_color  = record.get("fluorescenceColor") or False


                # === Vendor handling ===
                seller = record.get('seller', {})
                vendor_name = f"{seller.get('firstName', '')} {seller.get('lastName', '')}".strip()
                vendor_phone = seller.get('mobileNumber')
                vendor_email = seller.get('email')
                vendor_company = seller.get("companyName")
                
                name = vendor_company or vendor_name
                if not name:
                    name = "Anirath"
                
                vendor = request.env['res.partner'].sudo().search([('name', '=', name),('phone', '=', vendor_phone)],limit=1)
            
                if not vendor:
                    vendor_vals = {
                        "name": name,
                        "first_name" : seller.get('firstName', ''),
                        "last_name" : seller.get('lastName', ''),
                        "phone" : vendor_phone,
                        "email" : vendor_email,
                        "supplier_rank": 1,
                    }
                    vendor = self.env["res.partner"].create(vendor_vals)
                    _logger.info("Created new vendor: %s", vendor.name)

                if vendor:
                    seller_vals = {
                        "partner_id": vendor.id,
                        "min_qty": 1.0,
                        "price": record.get("vendorFinalPrice") or 0.0,
                        "vendor_final_price": record.get("vendorFinalPrice") or 0.0,
                        "vendor_per_carat_price": record.get("vendorPricePerCarat") or 0.0,
                    }
                    rec.seller_ids = [(0, 0, seller_vals)]
                    _logger.info("Mapped vendor %s to product %s", vendor.name, rec.name)

                rec.description = "Certificate data & vendor mapped successfully."

                if hasattr(rec, "action_combine_sdk_fields"):
                    rec.action_combine_sdk_fields()

            except requests.RequestException as e:
                rec.description = f"API request failed: {str(e)}"
                _logger.exception("API request failed for %s", url)
      

class ProductProduct(models.Model):
    _inherit = "product.product"

    @api.constrains('barcode')
    def _check_barcode_uniqueness(self):
        """ Override to skip Odoo's default duplicate barcode validation """
        _logger.info("Skipped default barcode uniqueness validation for products.")

    def _check_duplicated_product_barcodes(self, barcodes_within_company, company_id):
        """ Override to bypass duplicate product barcode validation """
        _logger.debug(
            "Bypassed duplicate product barcode validation for company ID %s with barcodes: %s",
            company_id,
            barcodes_within_company,
        )
        return

    def action_fetch_igi_data(self):
        self.ensure_one()
        return self.product_tmpl_id.action_fetch_igi_data()



class ProductSupplierinfo(models.Model):
    _inherit = 'product.supplierinfo'

    vendor_final_price = fields.Float(string="Vendor Final Price")
    vendor_per_carat_price = fields.Float(string="Vendor Per Carat Price")
    
    
import email
from odoo import models, fields, api
from odoo.exceptions import ValidationError,UserError
from odoo.http import request
import requests
from odoo import _
import re
import tempfile

import logging
_logger = logging.getLogger(__name__)


class ProductTemplate(models.Model):
    _inherit = "product.template" 

    labs = fields.Char(string='LAB')
    website_product_id = fields.Char()
    certificate = fields.Char(string="Certificate Number")
    certificate_type = fields.Char(string="Certificate Type")
    
    measurements = fields.Char(string="Measurements")
    weight = fields.Char(string="Carat Weight")
    weight_carat = fields.Char(string="Carat Weight")
    color = fields.Char(string="Color")
    clarity = fields.Char(string="Clarity")
    polish = fields.Char(string='Polish')
    cut = fields.Char(string='Cut')
    luster = fields.Char(string="Luster Grade")
    symmetry = fields.Char(string='Symmetry')
    treatments = fields.Char(string='Treatments')
    shapes = fields.Char(string="Shapes")
    shade = fields.Char(string="Shade")
    url_link = fields.Char(string="URL Link",compute='_compute_url_link', store=True)
    image_augmont = fields.Char(string="Image")
    video_360 = fields.Char(string="Diamond 360 Video")
    final_price = fields.Float(string="Vendor Final Price")
    final_price_margin = fields.Float(string="Final Price")
    price_per_carat = fields.Float(string="Vendor Price Per Carat")
    country_id = fields.Many2one('res.country',string="Country")
    stock_number = fields.Char(string="Stock Number")
    lgd_stock_number = fields.Char(string="Lgd Stock Number")
    
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

    
    
    # sdk_labs = fields.Selection([('igi', 'IGI'), ('lg', 'LG')], string='LAB')
    # sdk_certificate = fields.Char(string="Certificate Number")
    # sdk_certificate_type = fields.Char(string="Certificate Type")
    # sdk_id_number = fields.Char(string="ID Number")
    # sdk_shapes = fields.Selection([('Round', 'Diamond Round'), ('Pear', 'Diamond Pear'), ('Oval', 'Diamond Oval'), ('Emerald', 'Diamond Emerald'),
    #                                ('Marquise', 'Diamond Marquise'), ('Asscher', 'Diamond Asscher'), ('Marquise', 'Diamond Marquise')],
    #                               string='Diamond Shapes')
    # sdk_weight = fields.Float(string="Carat Weight")
    # sdk_measurements = fields.Char(string="Measurements")
    # sdk_weight_1 = fields.Char(string="Carat Weight")
    # sdk_color = fields.Selection([
    #     ('D', 'D'),
    #     ('E', 'E'),
    #     ('F', 'F'),
    #     ('G', 'G'),
    #     ('H', 'H'),
    #     ('I', 'I'),
    #     ('J', 'J'),
    #     ('K', 'K'),
    #     ('L', 'L'),
    #     ('M', 'M'),
    # ], string="Diamond Color")

    
    # sdk_clarity = fields.Selection([('FL', 'FL'),('VS1','VS1'), ('IF', 'IF'), ('VVS1', 'VVS1')], string='Clarity')
    # sdk_polish = fields.Selection([('excellent', 'Excellent'), ('very_good', 'Very Good'), ('good', 'Good'),
    #                                ('fair', 'Fair'), ('poor', 'Poor')], string='Polish')
    # sdk_cut = fields.Selection([('excellent', 'Excellent'), ('very_good', 'Very Good'), ('good', 'Good'),
    #                                ('fair', 'Fair'), ('poor', 'Poor')], string='Cut', required=True)
    # sdk_symmetry = fields.Selection([('excellent', 'Excellent'), ('very_good', 'Very Good'), ('good', 'Good'),
    #                                ('fair', 'Fair'), ('poor', 'Poor')], string='Symmetry')
    # sdk_growth_type = fields.Selection([('CVD', 'CVD'), ('HPHT', 'HPHT'),('Natural','Natural')], string='Growth Type')
    # sdk_treatments = fields.Selection([('CVD', 'CVD'), ('HPHT', 'HPHT'),('Natural','Natural')], string='Treatments')

    sdk_is_code_enabled = fields.Boolean(string="Is Code Enabled", default=False, copy=False)
    # sdk_url_link = fields.Char(string="URL Link",compute='_compute_sdk_url_link', store=True)
    # parsed_data = fields.Char(string="Data")
    # sdk_igi_pdf = fields.Binary(string="IGI PDF", attachment=True)
    # sdk_igi_pdf_filename = fields.Char(string="PDF Filename")
    
    
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

    # @api.constrains('name')
    # def _check_unique_name(self):
    #     for product in self:
    #         if not product.name:
    #             raise ValidationError('Product name is required')
    #         if self.search_count([('name', '=', product.name)]) > 1:
    #             if product.name != '[DUPLICATE]' and product.name != '[NEW]':
    #                 raise ValidationError(f'A product with this name "{product.name}" already exists. Please choose a unique name.')
    #             else:
    #                 raise ValidationError(f'A {product.name} product already exists. Please use the same to generate the product')
                
    # @api.onchange('sdk_certificate')
    # def _onchange_sdk_certificate(self):
    #     if self.sdk_certificate:
    #         igi_url = f"https://api.igi.org/viewpdf.php?r={self.sdk_certificate}"
    #         self.sdk_url_link = igi_url
            
    @api.depends('certificate')
    def _compute_url_link(self):
        for record in self:
            if record.certificate:
                record.url_link = f"https://api.igi.org/viewpdf.php?r={record.certificate}"
                record.barcode = record.certificate
            else:
                record.url_link = False
    
    def copy(self, default=None):
        self.ensure_one()
        if default is None:
            default = {}
        default['name'] = '[DUPLICATE]'  
        return super().copy(default)

                

    def action_fetch_certificate_data(self):
        """
        Fetch certificate/vendor product data from Augmont API
        and update record fields accordingly.
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
      
                
    # @api.onchange('certificate', 'stock_number', 'lgd_stock_number')
    # def _onchange_cert_stock(self):
    #     if self.certificate or self.stock_number or self.lgd_stock_number:
    #         # Auto-fetch if not already fetched
    #         if not self.measurements and not self.list_price:
    #             try:
    #                 self.action_fetch_certificate_data()
    #             except Exception as e:
    #                 _logger.warning("Auto-fetch failed: %s", e)

    
    # def action_fetch_certificate_data(self):
    #     for rec in self:
    #         if not rec.certificate:
    #             raise UserError("Please enter a Certificate number first.")

    #         url = f"https://lgdtest.augmont.com/igi.php?cert={rec.certificate}"
    #         try:
    #             response = requests.get(url, timeout=10)
    #             if response.status_code == 200:
    #                 try:
    #                     data = response.json()
    #                 except Exception:
    #                     raise UserError("API did not return valid JSON data.")

    #                 if data and isinstance(data, list) and len(data) > 0:
    #                     record = data[0]

    #                     # rec.measurements = record.get('Measurements', '')
    #                     # parts = rec.measurements.replace(' ', '').replace('-', '|').replace('*', '|').split('|')
    #                     # if len(parts) == 3:
    #                     #     rec.length = float(parts[0])
    #                     #     rec.width = float(parts[1])
    #                     #     rec.depth = float(parts[2])
    #                     rec.measurements = record.get('Measurements', '')
    #                     if rec.measurements:
    #                         # remove "mm", normalize separators
    #                         clean = rec.measurements.lower().replace('mm', '').strip()
    #                         # replace x, -, * with | then split
    #                         clean = clean.replace('x', '|').replace('-', '|').replace('*', '|')
    #                         # also remove spaces
    #                         parts = [p.strip() for p in clean.split('|') if p.strip()]
    #                         if len(parts) == 3:
    #                             rec.length = float(parts[0])
    #                             rec.width = float(parts[1])
    #                             rec.depth = float(parts[2])
                                
                                
    #                     # rec.weight_carat = record.get('CARAT WEIGHT', '')
    #                     value = record.get('CARAT WEIGHT', '')
    #                     match = re.search(r'\d+(\.\d+)?', value)

    #                     rec.weight_carat = match.group(0) if match else ''
    #                     rec.color = record.get('COLOR GRADE', '')
    #                     rec.clarity = record.get('CLARITY GRADE', '')
    #                     rec.polish = record.get('POLISH', '')
    #                     rec.cut = record.get('SYMMETRY', '')
    #                     rec.symmetry = record.get('SYMMETRY', '')
    #                     rec.shapes = record.get('SHAPE AND CUT', '')
    #                     rec.treatments = record.get('COMMENTS', '')
    #                     if rec.treatments:
    #                         text = rec.treatments.replace('\r\n', '\n').replace('\r', '\n')
    #                         # Split lines
    #                         lines = [line.strip() for line in text.split('\n') if line.strip()]
    #                         # If first word is "treatment", take the rest
    #                         if lines and lines[0].lower().startswith("treatment"):
    #                             rec.treatments = " ".join(lines[1:])
    #                     # rec.treatments = record.get('COMMENTS', '')
    #                     self.action_combine_sdk_fields()
    #                     # rec.description = "Certificate data fetched successfully."
    #                 else:
    #                     rec.description = "No data found in the API response."
    #             else:
    #                 rec.description = f"API error: {response.status_code}"
    #         except requests.RequestException as e:
    #             rec.description = f"API request failed: {str(e)}"

    
    
    
    
    # @api.onchange('certificate')
    # def _onchange_certificate_number(self):
    #     if self.certificate:
    #         url = f"https://lgdtest.augmont.com/igi.php?cert={self.certificate}"
    #         print(f"Fetching data from URL: {url}")  # Debug

    #         try:
    #             response = requests.get(url, timeout=10)
    #             print(f"Response status code: {response.status_code}")  # Debug

    #             if response.status_code == 200:
    #                 data = response.json()
    #                 print(f"Response JSON data: {data}")  # Debug

    #                 if data and isinstance(data, list) and len(data) > 0:
    #                     record = data[0]
    #                     print(f"Parsed record: {record}")  # Debug

    #                     # self.name = record.get('DESCRIPTION', '')
    #                     self.measurements = record.get('Measurements', '')
    #                     parts = self.measurements.replace(' ', '').replace('-', '|').replace('*', '|').split('|')
    #                     if len(parts) == 3:
    #                         self.length = float(parts[0])
    #                         self.width = float(parts[1])
    #                         self.depth = float(parts[2])
    #                     # self.weight = record.get('CARAT WEIGHT', '')
    #                     self.weight_carat = record.get('CARAT WEIGHT', '')
    #                     self.color = record.get('COLOR GRADE', '')
    #                     self.clarity = record.get('CLARITY GRADE', '')
    #                     self.polish = record.get('POLISH', '')
    #                     self.cut = record.get('CUT GRADE', '')
    #                     self.symmetry = record.get('SYMMETRY', '')
    #                     self.shapes = record.get('SHAPE AND CUT', '')
    #                     self.treatments = record.get('COMMENTS', '')
    #                     self.action_combine_sdk_fields()
    #                 else:
    #                     self.description = "No data found in the API response."
    #                     print("No valid data in API response.")  # Debug
    #             else:
    #                 self.description = f"API error: {response.status_code}"
    #                 print(f"API returned error code: {response.status_code}")  # Debug
    #         except requests.RequestException as e:
    #             self.description = f"API request failed: {str(e)}"
    #             print(f"API request exception: {str(e)}")  # Debug


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


        
class ProductSupplierinfo(models.Model):
    _inherit = 'product.supplierinfo'

    vendor_final_price = fields.Float(string="Vendor Final Price")
    vendor_per_carat_price = fields.Float(string="Vendor Per Carat Price")
    
    
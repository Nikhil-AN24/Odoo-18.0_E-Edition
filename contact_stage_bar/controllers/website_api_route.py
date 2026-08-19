from odoo import http
from odoo.http import request, Response
from markupsafe import Markup

import logging, json
from datetime import datetime

from odoo import models, fields, api

_logger = logging.getLogger(__name__)

ALLOWED_STATUSES = {
    'Diamond Booked',
    'Confirmed',
    'Not available',
    'In QC process',
    'QC Fail',  
    'Payment Pending',
    'Cancelled',
    'Confirmation Pending', 
    'Availability Check', 
    'Order Confirmed',  
    'Payment Completed',
    'Dispatched',
    'Return of Order',
    'Re - Dispatched',
    'Delivered',
    'Order Completed'
}

STATUS_MAP = {
    'Diamond Booked':    'diamond_booked',
    'Confirmed':         'confirmed',
    'Not available':     'not_available',
    'Cancelled':         'cancelled',
    'In QC process':     'in_qc_process',
    'QC Fail':           'qc_fail',
    'Payment Pending':   'payment_pending',
    'Payment Completed': 'payment_completed',
    'Dispatched':        'dispatched',
    'Return of Order':   'return_of_order',
    'Re - Dispatched':   're_dispatched',
    'Delivered':         'delivered',
    'Order Completed':   'order_completed',
}

class PartnerSyncController(http.Controller):
    # @http.route('/api/contact/login', type='json', auth='public', methods=['POST'], csrf=False)
    # def contact_login_or_create(self):
        
    #     # target_db = request.httprequest.headers.get('X-Odoo-Database', 'Testing')
    #     # request.session.db = 'augmont_private_limited'
        
    #     data = json.loads(request.httprequest.data)
    #     _logger.info(f"Received data: {data}")
        
    #     vals = {}
    #     state = None
    #     country = None
    #     city = None
        
    #     state_name = data.get("address", {}).get("state", {}).get("name")
    #     _logger.info(f"Received state: {state_name}")
    #     if state_name:
    #         state = request.env['res.country.state'].sudo().search([('name', 'ilike', state_name)], limit=1)
    #         _logger.info(f"Matched state: {state}")
    #         if state:
    #             state = state.id

       
    #     input_country = data.get("address", {}).get("country", {}).get("name")
    #     _logger.info(f"input_country: {input_country}")
            
    #     if input_country:
    #         country = request.env['res.country'].sudo().search([
    #             ('name', '=', input_country.title())
    #         ], limit=1).id
    #     _logger.info(f" country: {country}")
                
    #     full_name = f"{data.get('firstName', '').strip()} {data.get('lastName', '').strip()}".strip()
    #     _logger.info(f"Full name constructed: {full_name}")
            
    #     businessType = None
    #     businessRole = None

    #     businessType = request.env['custom.category'].sudo().search([
    #         ('name', 'ilike', data.get('businessType'))
    #     ], limit=1)

    #     if not businessType:
    #         businessType = request.env['custom.category'].sudo().create({
    #             'name': data.get('businessType'),
    #         })

    #     businessRole = request.env['custom.category'].sudo().search([
    #         ('name', 'ilike', data.get('businessRole'))
    #     ], limit=1)

    #     if not businessRole:
    #         businessRole = request.env['custom.category'].sudo().create({
    #             'name': data.get('businessRole'),
    #         })
        
    #     gst_stage_map = {
    #         'verified': 'Verified',
    #         'not_verified': 'Not Verified',
    #         'pending': 'Pending',
    #     }
    #     gst_value = data.get("kyc", {}).get('gstinStatus')
    #     _logger.info(f"GST value: {gst_value}")
        
    #     ein_value = data.get("kyc", {}).get('einStatus')
    #     _logger.info(f"EIN value: {ein_value}")
                    
    #     mapping = { 
    #         'name': data.get('companyName'),
    #         'first_name':data.get('firstName'),
    #         'last_name':data.get('lastName'),
    #         'phone' : data.get('mobileNumber'),
    #         'email' : data.get('email'),
    #         'street' : data.get("address",{}). get("addressLine1"),
    #         'street2' : data.get("address",{}). get("addressLine2"),
    #         'city': data.get("address", {}).get("city", {}).get("name"),
    #         'zip' : data.get("address",{}). get("pincode"),
    #         'category_ids': [(6, 0, [businessType.id])],
    #         'primary_category_ids': [(6, 0, [businessRole.id])],
    #         'state_id' : state,
    #         'country_id' : country,
    #         'vat' : data.get("kyc",{}). get('gstin'),
    #         'ein_number' : data.get("kyc",{}). get('einNumber'),
    #         'gst_stages' : gst_value,
    #         'ein_status' : ein_value,
    #         'l10n_in_pan' : data.get("kyc",{}). get('panNumber')
    #         }
    #     _logger.info(f"Mapping values: {mapping}")   
    #     partner = request.env['res.partner'].sudo().search([('email','=',data.get('email'))],limit=1)
    #     _logger.info(f"Partner found: {partner}")
        
    #     for field_name, value in mapping.items():
    #         if value:
    #             vals[field_name] = value
        
    #     if partner:
    #         _logger.info(f"Writing values to partner {partner.id}: {vals}")
    #         partner.write(vals)
    #         partner.stage_id = 15  
    #         _logger.info(f"Updated partner {partner.id} to stage_id  ({partner.stage_id.name})")
            
    #         return {
    #             'status': 'Existing Accounts Updated',
    #             'name': partner.name,
    #             'first_name': partner.first_name,
    #             'last_name': partner.last_name,
    #             'partner_id': partner.id,
    #             'stages': partner.stage_id.name,
    #             'country': partner.country_id.name if partner.country_id else '',
    #             'Bussiness Type': businessType.name if businessType else '',
    #             'Bussiness Role': businessRole.name if businessRole else '',
    #             'phone': partner.phone,
    #             'email': partner.email,
    #             'street': partner.street,
    #             'street2': partner.street2,    
    #             'city': partner.city,
    #             'zip': partner.zip, 
    #             'pan_number': partner.l10n_in_pan,
    #             'gst_stages': partner.gst_stages,
    #             'ein_status': partner.ein_status,
    #             'Gst Number': partner.vat,
    #             'Ein Number': partner.ein_number,
    #         }
            
    #     else:
    #         _logger.info(f"Creating new partner with vals: {vals}")
    #         new_partner = request.env['res.partner'].sudo().create(vals)
    #         _logger.info(f"Created new partner {new_partner.id} with vals: {vals}")
    #         return {
    #             'status': 'New Accounts Company Created',
    #             'name': new_partner.name,
    #             'first_name': new_partner.first_name,
    #             'last_name': new_partner.last_name,
    #             'stages': new_partner.stage_id.name,
    #             'partner_id': new_partner.id,
    #             'Bussiness Type': businessType.name if businessType else '',
    #             'Bussiness Role': businessRole.name if businessRole else '',    
    #             'phone': new_partner.phone,
    #             'email': new_partner.email, 
    #             'street': new_partner.street,
    #             'street2': new_partner.street2,
    #             'city': new_partner.city,
    #             'zip': new_partner.zip,
    #             'pan_number': new_partner.l10n_in_pan,
    #             'gst_stages': new_partner.gst_stages,
    #             'ein_status': new_partner.ein_status,
    #             'Gst Number': new_partner.vat,
    #             'Ein Number': new_partner.ein_number,
    #             'state': new_partner.state_id.name if new_partner.state_id else '',
    #             'city': new_partner.city,
    #             'zip': new_partner.zip,
    #             'country': new_partner.country_id.name if new_partner.country_id else '',
    #         }
    
    
    @http.route('/api/contact/login', type='json', auth='public', methods=['POST'], csrf=False)
    def contact_login_or_create(self):
        
        
        data = json.loads(request.httprequest.data)
        _logger.info(f"Received data: {data}")
        
        vals = {}
        state = None
        country = None
        city = None
        
        state_name = data.get("address", {}).get("state", {}).get("name")
        _logger.info(f"Received state: {state_name}")
        if state_name:
            state = request.env['res.country.state'].sudo().search([('name', 'ilike', state_name)], limit=1)
            _logger.info(f"Matched state: {state}")
            if state:
                state = state.id

       
        input_country = data.get("address", {}).get("country", {}).get("name")
        _logger.info(f"input_country: {input_country}")
            
        if input_country:
            country = request.env['res.country'].sudo().search([
                ('name', '=', input_country.title())
            ], limit=1).id
        _logger.info(f" country: {country}")
                
        full_name = f"{data.get('firstName', '').strip()} {data.get('lastName', '').strip()}".strip()
        _logger.info(f"Full name constructed: {full_name}")
            
        businessType = None
        businessRole = None

        businessType = request.env['custom.category'].sudo().search([
            ('name', 'ilike', data.get('businessType'))
        ], limit=1)

        if not businessType:
            businessType = request.env['custom.category'].sudo().create({
                'name': data.get('businessType'),
            })

        businessRole = request.env['custom.category'].sudo().search([
            ('name', 'ilike', data.get('businessRole'))
        ], limit=1)

        if not businessRole:
            businessRole = request.env['custom.category'].sudo().create({
                'name': data.get('businessRole'),
            })
        
        gst_stage_map = {
            'verified': 'Verified',
            'not_verified': 'Not Verified',
            'pending': 'Pending',
        }
        gst_value = data.get("kyc", {}).get('gstinStatus')
        _logger.info(f"GST value: {gst_value}")
        
        ein_value = data.get("kyc", {}).get('einStatus')
        _logger.info(f"EIN value: {ein_value}")
                    
        mapping = { 
            'name': data.get('companyName'),
            'first_name':data.get('firstName'),
            'last_name':data.get('lastName'),
            'phone' : data.get('mobileNumber'),
            'email' : data.get('email'),
            'street' : data.get("address",{}). get("addressLine1"),
            'street2' : data.get("address",{}). get("addressLine2"),
            'city': data.get("address", {}).get("city", {}).get("name"),
            'zip' : data.get("address",{}). get("pincode"),
            'category_ids': [(6, 0, [businessType.id])],
            'primary_category_ids': [(6, 0, [businessRole.id])],
            'state_id' : state,
            'country_id' : country,
            'vat' : data.get("kyc",{}). get('gstin'),
            'ein_number' : data.get("kyc",{}). get('einNumber'),
            'gst_stages' : gst_value,
            'ein_status' : ein_value,
            'l10n_in_pan' : data.get("kyc",{}). get('panNumber')
            }
        _logger.info(f"Mapping values: {mapping}")   
        partner = request.env['res.partner'].sudo().search([('email','=',data.get('email'))],limit=1)
        _logger.info(f"Partner found: {partner}")
        
        for field_name, value in mapping.items():
            if value:
                vals[field_name] = value
        
        if partner:
            _logger.info(f"Writing values to partner {partner.id}: {vals}")
            partner.write(vals)
            partner.stage_id = 15  
            _logger.info(f"Updated partner {partner.id} to stage_id  ({partner.stage_id.name})")
            
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
            _logger.info(f"Creating new partner with vals: {vals}")
            new_partner = request.env['res.partner'].sudo().create(vals)
            _logger.info(f"Created new partner {new_partner.id} with vals: {vals}")
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
            
    

    # @http.route('/api/order/cart_add', type='json', auth='public', methods=['POST'], csrf=False)
    # def add_to_cart(self):
        # GRADE_SHORT_NAMES = {
        #     'Excellent': 'EX',
        #     'Very Good': 'VG',
        #     'Ideal': 'ID',
        #     'Good': 'G',
        #     'Fair': 'F',
        #     'Poor': 'P'
        # }

        # try:
        #     data = json.loads(request.httprequest.data)
        #     _logger.info("Received data: %s", data)

        #     orders = data.get('orders', [])
        #     _logger.info("Processing %d orders", len(orders))

        #     # Get common data
        #     order_invoice_number = data.get('invoiceNumber')
        #     buyer = data.get('buyerKyc', {})
        #     email = buyer.get('email')
        #     customer_name = f"{buyer.get('firstName', '')} {buyer.get('lastName', '')}".strip()

        #     partner = request.env['res.partner'].sudo().search([('email', '=', email)], limit=1)
        #     if not partner:
        #         partner = request.env['res.partner'].sudo().create({
        #             'name': customer_name,
        #             'email': email,
        #             'phone': buyer.get('mobileNumber'),
        #         })

        #     # Try to find existing draft sale order
        #     sale_order = request.env['sale.order'].sudo().search([
        #         ('partner_id', '=', partner.id),
        #         ('state', '=', 'draft'),
        #         ('sdk_augmont_number','=',order_invoice_number)
        #     ], limit=1)

        #     if not sale_order:
        #         raw_order_date = orders[0].get('invoiceDate') if orders else None
        #         order_date = datetime.strptime(raw_order_date, "%Y-%m-%dT%H:%M:%S.%fZ") if raw_order_date else fields.Datetime.now()
        #         order_status = orders[0].get('orderCurrentStatus', {}).get("statusName") if orders else ''

        #         billing_address = shipping_address = ''
        #         if orders and 'orderAddress' in orders[0]:
        #             addr = orders[0]['orderAddress'][0]
        #             billing_address = addr.get('billingAddress', '')
        #             shipping_address = addr.get('shippingAddress', '')

        #         sale_order = request.env['sale.order'].sudo().create({
        #             'partner_id': partner.id,
        #             'state': 'draft',
        #             'sdk_augmont_status': order_status,
        #             'date_order': order_date,
        #             'sdk_augmont_number': order_invoice_number,
        #             'billing_address': billing_address,
        #             'shipping_address': shipping_address,
        #             'location': 'mumbai',
        #             'gst_treatment' : 'within_maharashtra',
        #             'is_website' : True,
        #         })

        #     # Process each order item
        #     for order in orders:
        #         product_details = order.get('productDetails', {})
        #         product_name = product_details.get('title')
        #         quantity = float(order.get('quantity', 1.0))
        #         order_price = order.get('productPrice')
        #         order_number = order.get('orderUniqueId')

        #         # Vendor
        #         seller = order.get('seller', {})
        #         vendor_company = seller.get('companyName')
        #         full_name = f"{seller.get('firstName', '').strip()} {seller.get('lastName', '').strip()}".strip()
        #         vendor_phone = seller.get('mobileNumber')
        #         vendor_email = seller.get('email')
        #         name = vendor_company or full_name
        #         vendor = request.env['res.partner'].sudo().search([
        #             ('name', '=', name),
        #             ('supplier_rank', '>', 0)
        #         ], limit=1)

        #         if not vendor:
        #             vendor = request.env['res.partner'].sudo().create({
        #                 'name': name,
        #                 'first_name': seller.get('firstName', '').strip(),
        #                 'last_name': seller.get('LastName', '').strip(),
        #                 'supplier_rank': 1,
        #                 'phone': vendor_phone,
        #                 'email': vendor_email,
        #             })

        #         input_country = product_details.get('country')
        #         country_id = False
        #         if input_country:
        #             country = request.env['res.country'].sudo().search([('name', '=', input_country.title())], limit=1)
        #             country_id = country.id if country else False

        #         # product = request.env['product.template'].sudo().search([('name', '=', product_name)], limit=1)
        #         certificate =  product_details.get('certNumber')
        #         stock_number = product_details.get('stockNum')
        #         lgd_stock_number =  product_details.get('lgdStockNum')
                
        #         product = request.env['product.template'].sudo().search([
        #             ('certificate', '=', certificate),
        #             ('stock_number', '=', stock_number),
        #             ('lgd_stock_number', '=', lgd_stock_number)
        #         ], limit=1)
                

        #         buy = request.env.ref('purchase_stock.route_warehouse0_buy', raise_if_not_found=False)
        #         mto = request.env.ref('stock.route_warehouse0_mto', raise_if_not_found=False)
        #         routes = [r.id for r in (buy, mto) if r]
        #         warehouse = request.env['stock.warehouse'].sudo().search([], limit=1)
        #         _logger.info("product_id>>>>>>>>",product_details.get('id'))
        #         product_vals = {
        #             'website_product_id': product_details.get('id'),
        #             'name': product_name,
        #             'type': 'consu',
        #             'list_price': product_details.get('pricePerCarat'),
        #             'standard_price': product_details.get('finalPriceMargin'),
        #             'labs': product_details.get('lab'),
        #             'cut': product_details.get('cut'),
        #             'clarity': product_details.get('clarity'),
        #             'color': product_details.get('color'),
        #             'shapes': product_details.get('shape'),
        #             'weight_carat': product_details.get('weight'),
        #             'shade': product_details.get('shade'),
        #             'treatments': product_details.get('treatment'),
        #             'measurements': product_details.get('measurements'),
        #             'order_specific': order.get('orderNote'),
        #             'item_notes': order.get('generalNotes'),
        #             'default_qc_requirements': order.get('defaultQcRequirements'),
        #             'certificate': product_details.get('certNumber'),
        #             'certificate_type': product_details.get('certType'),
        #             'stock_number': product_details.get('stockNum'),
        #             'lgd_stock_number': product_details.get('lgdStockNum'),
        #             'video_360': product_details.get('diamondVideo360'),
        #             'final_price': product_details.get('finalPrice'),
        #             'symmetry': product_details.get('symmetry'),
        #             'image_augmont': product_details.get('diamondImage'),
        #             'polish': product_details.get('polish'),
        #             'country_id': country_id,
        #             'barcode': product_details.get('certNumber'),
        #             'length': product_details.get('length'),
        #             'width': product_details.get('width'),
        #             'depth': product_details.get('height'),
        #             'eye_clean': product_details.get('eyeClean'),
        #             'taxes_id': [(6, 0, [112])],
        #             'supplier_taxes_id': [(6, 0, [112])],
        #             'route_ids': [(6, 0, routes)],
        #             'is_storable': True,
        #             'fluorescence_color': product_details.get('fluorescenceColor'),
        #             'fluorescence_intensity': product_details.get('fluorescenceIntensity'),
        #             'warehouse_id': warehouse.id,
        #             # 'seller_ids': [(0, 0, {
        #             #     'partner_id': vendor.id,
        #             #     'min_qty': quantity,
        #             #     'price': product_details.get('finalPriceMargin'),
        #             #     'vendor_final_price': product_details.get('finalPriceMargin'),
        #             #     'vendor_per_carat_price': product_details.get('pricePerCaratMargin'),
        #             # })],
        #             # custom field
        #             'sdk_is_code_enabled': True,
        #         }
        #         _logger.info(product_vals,"Testing product_vals")

        #         existing_seller = product.seller_ids.filtered(lambda s: s.partner_id.id == vendor.id)

        #         seller_vals = []
        #         if not existing_seller:
        #             seller_vals = [(0, 0, {
        #                 'partner_id': vendor.id,
        #                 'min_qty': quantity,
        #                 'price': product_details.get('finalPriceMargin'),
        #                 'vendor_final_price': product_details.get('finalPriceMargin'),
        #                 'vendor_per_carat_price': product_details.get('pricePerCaratMargin'),
        #             })]

        #         product_vals.update({
        #             'seller_ids': seller_vals
        #         })
        #         if not product:
        #             # Create product template
        #             product = request.env['product.template'].sudo().create(product_vals)
        #             _logger.info(product)

        #         else:
        #             # Update existing template
        #             product.sudo().write(product_vals)
        #             _logger.info(product)


        #         # Ensure variant exists
        #         variant = product.product_variant_id
        #         if not variant:
        #             variant = request.env['product.product'].sudo().create({
        #                 'product_tmpl_id': product.id,
        #                 'name': product.name,
        #                 'barcode': product.certificate,  # barcode = certificate
        #             })
                

        #         # Now handle sale order line
        #         existing_line = request.env['sale.order.line'].sudo().search([
        #             ('order_id', '=', sale_order.id),
        #             ('certificate', '=', certificate),
        #             ('lgd_stock_number', '=', lgd_stock_number),
        #         ], limit=1)

        #         if not existing_line:
        #             sale_order.sudo().write({
        #                 'order_line': [(0, 0, {
        #                     'product_id': variant.id,
        #                     'product_uom_qty': quantity,
        #                     'price_unit': product.list_price,
        #                     'name': product.name,
        #                     'order_number': order_number,
        #                     'vendor_id': vendor.id,
        #                     'tax_id': [(6, 0, product.taxes_id.ids)],
        #                 })]
        #             })

        #     return {
        #         'status': 'Sale Order Updated with all line items',
        #         'Sale Order': sale_order.name,
        #         'Customer': partner.name,
        #         'order_count': len(orders)
        #         }



        # except Exception as e:
        #     _logger.exception("Error in /api/order/cart_add: %s", str(e))
        #     return {'error': str(e)}


    @http.route('/training/api/order/cart_add', type='json', auth='public', methods=['POST'], csrf=False)
    def add_to_cart(self):
        GRADE_SHORT_NAMES = {
            'Excellent': 'EX',
            'Very Good': 'VG',
            'Ideal': 'ID',
            'Good': 'G',
            'Fair': 'F',
            'Poor': 'P'
        }

        try:
            data = json.loads(request.httprequest.data)
            _logger.info("=" * 80)
            _logger.info("📥 CART_ADD REQUEST")
            _logger.info(f"   Invoice: {data.get('invoiceNumber')}")
            _logger.info(f"   Orders: {len(data.get('orders', []))}")
            _logger.info("=" * 80)

            orders = data.get('orders', [])
            if not orders:
                _logger.error("❌ No orders in payload")
                return {'status': 'error', 'message': 'No orders provided in payload'}

            _logger.info("Processing %d orders", len(orders))

            # Get common data
            order_invoice_number = data.get('invoiceNumber')
            buyer = data.get('buyerKyc', {})
            email = buyer.get('email')
            customer_name = f"{buyer.get('firstName', '')} {buyer.get('lastName', '')}".strip()

            buyer_country_code = data.get('orders', [{}])[0].get('buyer', {}).get('countryCode')
            kyc_info = buyer.get('kyc', {})

            gst_number = None
            ein_number = None

            if buyer_country_code == "91":
                gst_number = kyc_info.get('gstin')
            else:
                ein_number = kyc_info.get('einNumber')

            partner = request.env['res.partner'].sudo().search([('email', '=', email)], limit=1)
            if not partner:
                partner = request.env['res.partner'].sudo().create({
                    'name': customer_name,
                    'email': email,
                    'phone': buyer.get('mobileNumber'),
                })

            # Try to find existing draft sale order
            sale_order = request.env['sale.order'].sudo().search([
                ('partner_id', '=', partner.id),
                ('state', '=', 'draft'),
                ('sdk_augmont_number','=',order_invoice_number)
            ], limit=1)

            if not sale_order:
                raw_order_date = orders[0].get('invoiceDate') if orders else None
                order_date = datetime.strptime(raw_order_date, "%Y-%m-%dT%H:%M:%S.%fZ") if raw_order_date else fields.Datetime.now()
                order_status = (orders[0].get('orderCurrentStatus') or {}).get("statusName", '') if orders else ''

                billing_address = shipping_address = ''
                if orders and 'orderAddress' in orders[0]:
                    addr = orders[0]['orderAddress'][0]
                    billing_address = addr.get('billingAddress', '')
                    shipping_address = addr.get('shippingAddress', '')

                sale_order = request.env['sale.order'].sudo().create({
                    'partner_id': partner.id,
                    'state': 'draft',
                    'sdk_augmont_status': order_status,
                    'date_order': order_date,
                    'sdk_augmont_number': order_invoice_number,
                    'billing_address': billing_address,
                    'shipping_address': shipping_address,
                    'location': 'mumbai',
                    'gst_treatment' : 'within_maharashtra',
                    'is_website' : True,
                    'order_source': 'website',
                    'vat': gst_number,
                    'ein_number': ein_number,
                })

            # Process each order item
            for order in orders:

                # The website now sends both Certified (LGD) & Non-Certified
                # (MELEE / parcel) items inside the same orders array.

                order_line_type = order.get('lineType', 'LGD')
                # ── MELEE (Non-Certified / Parcel) order handling ─────────────
                if order_line_type == 'MELEE':
                    _logger.info("   🔷 Processing MELEE (non-certified) order item")

                    melee_request   = order.get('meleeDiamondRequest') or {}
                    product_details = order.get('productDetails') or {}
                    _is_new_fmt = bool(product_details) and not bool(melee_request)

                    _logger.info(
                        "   📋 MELEE payload format: %s",
                        "new/flat (productDetails)" if _is_new_fmt else "legacy (meleeDiamondRequest)",
                    )

                    if _is_new_fmt:
                        # All diamond attributes live directly in productDetails.
                        # Shapes / clarities / cuts arrive as single strings not lists in the new format
                        request_number  = product_details.get('requestNumber')
                        summary_title   = product_details.get('title') or request_number
                        treatment       = product_details.get('treatment', '')
                        melee_shapes_v  = product_details.get('shape', '')
                        melee_clarv     = product_details.get('clarity', '')
                        melee_cutsv     = product_details.get('cut', '')
                        color_type      = product_details.get('colorType', '')
                        fancy_color     = product_details.get('color', '') or '' 
                        final_price_val = float(
                            order.get('productPrice')
                            or order.get('finalPrice')
                            or 0
                        )
                        parcels         = []
                        pricing_map     = {}
                        melee_quote_id  = ''

                    # ── Field extraction — legacy format ──────────────────────
                    else:
                        request_number  = melee_request.get('requestNumber')
                        summary_title   = melee_request.get('summaryTitle') or request_number
                        treatment       = melee_request.get('treatment', '')
                        req_payload     = melee_request.get('payload', {})
                        melee_shapes_v  = ', '.join(req_payload.get('shapes', []))
                        melee_clarv     = ', '.join(req_payload.get('clarities', []))
                        melee_cutsv     = ', '.join(req_payload.get('cuts', []))
                        fancy_color     = req_payload.get('fancyColor', '') or ''
                        final_price_val = float(order.get('productPrice') or order.get('finalPrice') or 0)
                        parcels         = melee_request.get('parcels', [])
                        melee_quote_id  = order.get('meleeQuoteId', '')
                        # Legacy pricing map: meleeRequestQuotePricing → quoteParcelLines
                        _legacy_lines = (
                            order
                            .get('meleeRequestQuotePricing', {})
                            .get('quote', {})
                            .get('quoteParcelLines', [])
                        )
                        pricing_map = {
                            qpl.get('meleeDiamondRequestParcelId'): qpl
                            for qpl in _legacy_lines
                            if qpl.get('meleeDiamondRequestParcelId')
                        }

                    melee_unit_val  = order.get('meleeUnit', 'CARAT')
                    melee_qty_val   = float(order.get('meleeQty') or 0)
                    melee_order_qty = float(order.get('quantity') or 1)
                    order_number    = order.get('orderUniqueId')

                    melee_seller       = order.get('seller') or {}
                    melee_vendor_co    = melee_seller.get('companyName')
                    melee_vendor_full  = (
                        f"{melee_seller.get('firstName','').strip()} "
                        f"{melee_seller.get('lastName','').strip()}".strip()
                    )
                    melee_vendor_name  = melee_vendor_co or melee_vendor_full
                    if not melee_vendor_name:
                        melee_vendor_name = "Anirath"
                    melee_vendor = request.env['res.partner'].sudo().search([
                        ('name', '=', melee_vendor_name),
                        ('supplier_rank', '>', 0),
                    ], limit=1)
                    if not melee_vendor and melee_vendor_name:
                        melee_vendor = request.env['res.partner'].sudo().create({
                            'name': melee_vendor_name,
                            'first_name': melee_seller.get('firstName', '').strip(),
                            'last_name': melee_seller.get('lastName', '').strip(),
                            'supplier_rank': 1,
                            'phone': melee_seller.get('mobileNumber'),
                            'email': melee_seller.get('email'),
                        })

                    melee_req_id = (
                        product_details.get('requestNumber')
                        if _is_new_fmt
                        else melee_request.get('id', '')
                    )
                    melee_product = request.env['product.template'].sudo().search([
                        ('website_product_id', '=', melee_req_id),
                    ], limit=1) if melee_req_id else None

                    melee_product_vals = {
                        'website_product_id': melee_req_id,
                        'name': summary_title,
                        'type': 'service',   # Non-certified = service product
                        'list_price': final_price_val,
                        'final_price': final_price_val,
                        'final_price_margin': final_price_val,
                        'treatments': treatment,
                        'shapes': melee_shapes_v,
                        'clarity': melee_clarv,
                        'cut': melee_cutsv,
                        'color': fancy_color,
                        'sdk_is_code_enabled': True,
                    }

                    if not melee_product:
                        melee_product = request.env['product.template'].sudo().create(melee_product_vals)
                        _logger.info("   ✅ Created MELEE parent product: %s", summary_title)
                    else:
                        melee_product.sudo().write(melee_product_vals)
                        _logger.info("   ✅ Updated MELEE parent product: %s", summary_title)

                    melee_variant = melee_product.product_variant_id

                    _melee_status_raw  = (order.get('orderCurrentStatus') or {})
                    _melee_status_name = _melee_status_raw.get('statusName', '')
                    melee_avail_status = STATUS_MAP.get(_melee_status_name, False)
                    _logger.info(
                        "   📊 MELEE availability from payload: '%s' → '%s'",
                        _melee_status_name or '(none)', melee_avail_status or '(blank)',
                    )

                    # ── Create / find the parent MELEE sale order line ────────
                    # Search by order_number (orderUniqueId) to avoid collision when requestNumber is None
                    search_id = str(order_number) if order_number else request_number
                    existing_parent = request.env['sale.order.line'].sudo().search([
                        ('order_id', '=', sale_order.id), ('order_number', '=', search_id),
                        ('is_melee_parent', '=', True), ], limit=1)

                    if not existing_parent:
                        sale_order.sudo().write({
                            'order_line': [(0, 0, {
                                'product_id': melee_variant.id,
                                'product_uom_qty': melee_order_qty,
                                'price_unit': final_price_val,
                                'name': summary_title,
                                'order_number': str(order_number) if order_number else request_number,
                                'vendor_id': melee_vendor.id if melee_vendor else False,
                                'tax_id': [(6, 0, melee_product.taxes_id.ids)],
                                'availability_status': melee_avail_status,
                                # MELEE-specific fields
                                'line_type': 'melee',
                                'is_melee_parent': True,
                                'melee_request_number': request_number,
                                'melee_summary_title': summary_title,
                                'melee_unit': melee_unit_val,
                                'melee_total_qty': melee_qty_val,
                                'melee_quote_id': melee_quote_id,
                                'melee_treatment': treatment,
                                'melee_shapes': melee_shapes_v,
                                'melee_clarities': melee_clarv,
                                'melee_cuts': melee_cutsv,
                                'melee_fancy_color': fancy_color,
                            })]
                        })
                        # Re-fetch the parent line we just created
                        melee_parent_line = request.env['sale.order.line'].sudo().search([
                            ('order_id', '=', sale_order.id),
                            ('order_number', '=', search_id),
                            ('is_melee_parent', '=', True),
                        ], limit=1)
                        _logger.info(
                            "   ✅ Created MELEE parent order line: %s (request: %s)",
                            summary_title, request_number,
                        )
                    else:
                        melee_parent_line = existing_parent
                        _logger.info(
                            "   ℹ️  MELEE parent order line already exists: %s", request_number
                        )

                    # Keep track of total price sum
                    total_parcel_price = 0.0

                    # ── Create child parcel lines ─────────────────────────────
                    for parcel in parcels:
                        parcel_id        = parcel.get('id', '')
                        sort_order       = parcel.get('sortOrder', 0)
                        unit_type        = parcel.get('unitType', 'PIECE')
                        parcel_qty       = float(parcel.get('quantity') or 0)
                        length_mm        = float(parcel.get('lengthMm') or 0)
                        width_mm         = float(parcel.get('widthMm') or 0)
                        depth_mm         = float(parcel.get('depthMm') or 0)
                        avg_carat        = float(parcel.get('avgCaratPerStone') or 0)

                        # Check if payload denotes CARAT or PIECE unit
                        is_carat = melee_unit_val.upper() == 'CARAT' or unit_type.upper() == 'CARAT'
                        
                        if is_carat:
                            qty_carats = parcel_qty
                            unit_display = "ct"
                            parcel_qty_display = f"{parcel_qty:g}" # Clean format, eg 3.000 -> 3
                        else:
                            qty_carats = round(parcel_qty * avg_carat, 6)
                            unit_display = "pcs"
                            parcel_qty_display = str(int(parcel_qty))

                        price_per_carat = float(
                            parcel.get('pricePerCarat')
                            or pricing_map.get(parcel_id, {}).get('pricePerCarat')
                            or 0
                        )
                        
                        # Calculate accurate parcel price and add to parent sum
                        parcel_line_price = float(parcel.get('parcelPrice'))
                        total_parcel_price += parcel_line_price

                        # Build a readable parcel product name using correct dynamic unit
                        parcel_name = (
                            f"\u21b3 Parcel {sort_order}: "
                            f"{parcel_qty_display}{unit_display} "
                            f"({length_mm} x {width_mm} x {depth_mm}mm)"
                        )

                        # Check whether this child parcel line already exists
                        existing_child = request.env['sale.order.line'].sudo().search([
                            ('order_id', '=', sale_order.id),
                            ('melee_parent_line_id', '=', melee_parent_line.id),
                            ('melee_parcel_sort_order', '=', sort_order),
                        ], limit=1)

                        if not existing_child:
                            parcel_product = request.env['product.template'].sudo().search([
                                ('website_product_id', '=', parcel_id),
                            ], limit=1) if parcel_id else None

                            parcel_product_vals = {
                                'website_product_id': parcel_id,
                                'name': parcel_name,
                                'type': 'service',
                                'list_price': price_per_carat,
                                'final_price': parcel_line_price,
                                'final_price_margin': parcel_line_price,
                                'sdk_is_code_enabled': True,
                            }

                            if not parcel_product:
                                parcel_product = request.env['product.template'].sudo().create(
                                    parcel_product_vals
                                )
                            else:
                                parcel_product.sudo().write(parcel_product_vals)

                            parcel_variant = parcel_product.product_variant_id

                            sale_order.sudo().write({
                                'order_line': [(0, 0, {
                                    'product_id': parcel_variant.id,
                                    'product_uom_qty': 1.0,
                                    'price_unit': 0,
                                    'final_price': parcel_line_price,
                                    'name': parcel_name,
                                    'order_number': str(order_number) if order_number else '',
                                    'vendor_id': melee_vendor.id if melee_vendor else False,
                                    'tax_id': [(6, 0, melee_product.taxes_id.ids)],
                                    'availability_status': melee_avail_status,
                                    'line_type': 'melee',
                                    'is_melee_parent': False,
                                    'melee_parent_line_id': melee_parent_line.id,
                                    'melee_parcel_sort_order': sort_order,
                                    'melee_parcel_unit_type': unit_type,
                                    'melee_parcel_quantity': parcel_qty,
                                    'melee_parcel_length_mm': length_mm,
                                    'melee_parcel_width_mm': width_mm,
                                    'melee_parcel_depth_mm': depth_mm,
                                    'melee_parcel_avg_carat': avg_carat,
                                    'melee_parcel_qty_carats': qty_carats,
                                    'melee_parcel_price_per_carat': price_per_carat,
                                })]
                            })
                            _logger.info("   ✅ Created MELEE parcel child line: %s", parcel_name)
                        else:
                            _logger.info("   ℹ️  MELEE parcel child line already exists: Parcel %s", sort_order)

                    # Enforce parent summation over parcels
                    if total_parcel_price > 0 and (final_price_val == 0 or final_price_val != total_parcel_price):
                        melee_parent_line.sudo().write({
                            'price_unit': total_parcel_price,
                            'final_price': total_parcel_price,
                        })
                        if melee_product:
                            melee_product.sudo().write({
                                'list_price': total_parcel_price,
                                'final_price': total_parcel_price,
                                'final_price_margin': total_parcel_price,
                            })

                    # Skip the LGD (certified) block below for this iteration
                    continue
                product_details = order.get('productDetails', {})
                product_name = product_details.get('title')
                quantity = float(order.get('quantity', 1.0))
                order_price = order.get('productPrice')
                order_number = order.get('orderUniqueId')

                # Vendor
                seller = order.get('seller', {})
                vendor_company = seller.get('companyName')
                full_name = f"{seller.get('firstName', '').strip()} {seller.get('lastName', '').strip()}".strip()
                vendor_phone = seller.get('mobileNumber')
                vendor_email = seller.get('email')
                name = vendor_company or full_name
                if not name:
                    name = "Anirath"
                vendor = request.env['res.partner'].sudo().search([
                    ('name', '=', name),
                    ('supplier_rank', '>', 0)
                ], limit=1)

                if not vendor:
                    vendor = request.env['res.partner'].sudo().create({
                        'name': name,
                        'first_name': seller.get('firstName', '').strip(),
                        'last_name': seller.get('lastName', '').strip(),
                        'supplier_rank': 1,
                        'phone': vendor_phone,
                        'email': vendor_email,
                    })

                input_country = product_details.get('country')
                country_id = False
                if input_country:
                    country = request.env['res.country'].sudo().search([('name', '=', input_country.title())], limit=1)
                    country_id = country.id if country else False

                certificate =  product_details.get('certNumber')
                stock_number = product_details.get('stockNum')
                lgd_stock_number =  product_details.get('lgdStockNum')
                
                product = request.env['product.template'].sudo().search([
                    ('certificate', '=', certificate),
                    ('stock_number', '=', stock_number),
                    ('lgd_stock_number', '=', lgd_stock_number)
                ], limit=1)
                

                buy = request.env.ref('purchase_stock.route_warehouse0_buy', raise_if_not_found=False)
                mto = request.env.ref('stock.route_warehouse0_mto', raise_if_not_found=False)
                routes = [r.id for r in (buy, mto) if r]
                warehouse = request.env['stock.warehouse'].sudo().search([], limit=1)

                product_vals = {
                    'website_product_id': product_details.get('id'),
                    'name': product_name,
                    'type': 'consu',
                    'list_price': product_details.get('pricePerCaratMargin'),
                    'standard_price': product_details.get('finalPrice'),
                    'final_price': product_details.get('finalPrice'),
                    'final_price_margin': product_details.get('finalPriceMargin'),
                    'price_per_carat': product_details.get('pricePerCarat'),
                    'labs': product_details.get('lab'),
                    'cut': product_details.get('cut'),
                    'clarity': product_details.get('clarity'),
                    'color': product_details.get('color'),
                    'shapes': product_details.get('shape'),
                    'weight_carat': product_details.get('weight'),
                    'shade': product_details.get('shade'),
                    'treatments': product_details.get('treatment'),
                    'measurements': product_details.get('measurements'),
                    'order_specific': order.get('orderNote'),
                    'item_notes': order.get('generalNotes'),
                    'customer_reference_note': order.get('customerReference', None),
                    'default_qc_requirements': order.get('defaultQcRequirements'),
                    'certificate': product_details.get('certNumber'),
                    'certificate_type': product_details.get('certType'),
                    'stock_number': product_details.get('stockNum'),
                    'lgd_stock_number': product_details.get('lgdStockNum'),
                    'video_360': product_details.get('diamondVideo360'),
                    'symmetry': product_details.get('symmetry'),
                    'image_augmont': product_details.get('diamondImage'),
                    'polish': product_details.get('polish'),
                    'country_id': country_id,
                    'barcode': product_details.get('certNumber'),
                    'length': product_details.get('length'),
                    'width': product_details.get('width'),
                    'depth': product_details.get('height'),
                    'eye_clean': product_details.get('eyeClean'),
                    'taxes_id': [(6, 0, [112])],
                    'supplier_taxes_id': [(6, 0, [112])],
                    'route_ids': [(6, 0, routes)],
                    'is_storable': True,
                    'fluorescence_color': product_details.get('fluorescenceColor'),
                    'fluorescence_intensity': product_details.get('fluorescenceIntensity'),
                    'warehouse_id': warehouse.id,
                    # 'seller_ids': [(0, 0, {
                    #     'partner_id': vendor.id,
                    #     'min_qty': quantity,
                    #     'price': product_details.get('finalPriceMargin'),
                    #     'vendor_final_price': product_details.get('finalPriceMargin'),
                    #     'vendor_per_carat_price': product_details.get('pricePerCaratMargin'),
                    # })],
                    # custom field
                    'sdk_is_code_enabled': True,
                }

                _logger.info(f"product_vals>>>>>>>>>>>>>>>>>>>: {product_vals}")
                existing_seller = product.seller_ids.filtered(lambda s: s.partner_id.id == vendor.id)

                seller_vals = []
                if not existing_seller:
                    seller_vals = [(0, 0, {
                        'partner_id': vendor.id,
                        'min_qty': quantity,
                        'price': product_details.get('finalPriceMargin'),
                        'vendor_final_price': product_details.get('finalPriceMargin'),
                        'vendor_per_carat_price': product_details.get('pricePerCaratMargin'),
                    })]

                product_vals.update({
                    'seller_ids': seller_vals
                })
                if not product:
                    # Create product template
                    product = request.env['product.template'].sudo().create(product_vals)
                else:
                    # Update existing template
                    product.sudo().write(product_vals)

                # Ensure variant exists
                variant = product.product_variant_id
                if not variant:
                    variant = request.env['product.product'].sudo().create({
                        'product_tmpl_id': product.id,
                        'name': product.name,
                        'barcode': product.certificate,  # barcode = certificate
                    })
                

                # Now handle sale order line
                existing_line = request.env['sale.order.line'].sudo().search([
                    ('order_id', '=', sale_order.id),
                    ('certificate', '=', certificate),
                    ('lgd_stock_number', '=', lgd_stock_number),
                ], limit=1)

                if not existing_line:
                    # Fetch availability_status from this order item's
                    # orderCurrentStatus.statusName via the shared STATUS_MAP.
                    # Falls back to False (blank) when the key is absent or null identical to the MELEE handling above.
                    _lgd_status_raw  = (order.get('orderCurrentStatus') or {})
                    _lgd_status_name = _lgd_status_raw.get('statusName', '')
                    lgd_avail_status = STATUS_MAP.get(_lgd_status_name, False)

                    sale_order.sudo().write({
                        'order_line': [(0, 0, {
                            'product_id': variant.id,
                            'product_uom_qty': quantity,
                            'price_unit': product.final_price_margin,
                            'final_price': product.final_price,
                            'name': product.name,
                            'order_number': order_number,
                            'vendor_id': vendor.id,
                            'tax_id': [(6, 0, product.taxes_id.ids)],
                            'availability_status': lgd_avail_status,
                        })]
                    })
                    _logger.info(
                        "   ✅ Created order line: %s (status: '%s')",
                        order_number, lgd_avail_status or '(blank)',
                    )

            _logger.info("=" * 80)
            _logger.info(f"✅ CART_ADD SUCCESS")
            _logger.info(f"   Invoice: {sale_order.name} ({order_invoice_number})")
            _logger.info(f"   Customer: {partner.name}")
            _logger.info(f"   Lines: {len(sale_order.order_line)}")
            _logger.info(f"   Status: {sale_order.sdk_augmont_status}")
            _logger.info("=" * 80)

            return {
                'status': 'success',
                'message': 'Order created/updated successfully',
                'data': {
                    'sale_order': sale_order.name,
                    'invoice_number': order_invoice_number,
                    'order_id': sale_order.id,
                    'customer': partner.name,
                    'order_count': len(orders),
                    'line_count': len(sale_order.order_line),
                    'invoice_status': sale_order.sdk_augmont_status
                }
            }

        except Exception as e:
            _logger.exception("Error in /api/order/cart_add: %s", str(e))
            return {'error': str(e)}

    @http.route('/api/sale_order/update_status', type='http', auth='public', methods=['POST'], csrf=False)
    def update_sale_order_status(self, **kwargs):

        STATUS_MAP = {
            'Diamond Booked':    'diamond_booked',
            'Confirmed':         'confirmed',
            'Not available':     'not_available',
            'Cancelled':         'cancelled',
            'In QC process':     'in_qc_process',
            'QC Fail':           'qc_fail',
            'Payment Pending':   'payment_pending',
            'Payment Completed': 'payment_completed',
            'Dispatched':        'dispatched',
            'Return of Order':   'return_of_order',
            'Re - Dispatched':   're_dispatched',
            'Delivered':         'delivered',
            'Order Completed':   'order_completed',
        }

        # ── Parse request body ────────────────────────────────────────────────
        try:
            data = json.loads(request.httprequest.data)
        except (json.JSONDecodeError, Exception):
            return Response(
                json.dumps({'status': 'error',
                            'message': 'Invalid JSON format in request body',
                            'code': 400}),
                content_type='application/json', status=400
            )

        # ── Detect mode: single or bulk ───────────────────────────────────────
        #   Single: top-level "orderNumber" key is present
        #   Bulk:   top-level "orders" list is present
        
        is_bulk = 'orders' in data and isinstance(data.get('orders'), list)
        is_single = 'orderNumber' in data

        if not is_bulk and not is_single:
            return Response(
                json.dumps({
                    'status': 'error',
                    'message': (
                        'Payload must contain either "orderNumber" (single) '
                        'or "orders" (bulk list).'
                    ),
                    'code': 400
                }),
                content_type='application/json', status=400
            )

        # For single mode: wrap the flat fields into a one-item list so the
        # processing loop below is identical for both modes.
        # For bulk mode: use the "orders" list directly; shared fields
        # (updated_by) live at the top level.
        
        updated_by_info = data.get('updated_by', {}) or data.get('updatedBy', {})

        if is_single:
            entries = [{
                'orderNumber':        data.get('orderNumber'),
                'status':             data.get('status'),
                'utr':                data.get('utr'),
                'courierPartnerName': data.get('courierPartnerName'),
                'trackingNumber':     data.get('trackingNumber'),
                'trackingUrl':        data.get('trackingUrl'),
                'comment':            data.get('comment'),
            }]
        else:
            entries = data.get('orders', [])

        if not entries:
            return Response(
                json.dumps({'status': 'error',
                            'message': '"orders" list must not be empty',
                            'code': 400}),
                content_type='application/json', status=400
            )

        _logger.info("=" * 80)
        _logger.info(
            "◀◀ WEBSITE → ODOO | %s | STATUS UPDATE | mode=%s | count=%d",
            fields.Datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'single' if is_single else 'bulk',
            len(entries),
        )

        # ── Shared processing loop ────────────────────────────────────────────
        results          = []
        processed        = 0
        failed           = 0
        last_sale_order  = None

        for entry in entries:
            order_number         = str(entry.get('orderNumber', '') or '').strip()
            new_status           = str(entry.get('status', '') or '').strip()
            utr_number           = entry.get('utr')
            courier_partner_name = entry.get('courierPartnerName')
            tracking_number      = entry.get('trackingNumber')
            tracking_url         = entry.get('trackingUrl')
            comment              = entry.get('comment', '')

            # ── Per-entry validation ──────────────────────────────────────────
            if not order_number or not new_status:
                results.append({
                    'orderNumber': order_number or '(missing)',
                    'status': 'error',
                    'message': 'Both "orderNumber" and "status" are required',
                })
                failed += 1
                continue

            if new_status not in STATUS_MAP:
                results.append({
                    'orderNumber': order_number,
                    'status': 'error',
                    'message': (
                        f'Invalid status: "{new_status}". '
                        f'Allowed: {sorted(STATUS_MAP.keys())}'
                    ),
                })
                failed += 1
                continue

            if new_status == 'Payment Completed' and not utr_number:
                results.append({
                    'orderNumber': order_number,
                    'status': 'error',
                    'message': 'UTR number is required for Payment Completed status',
                })
                failed += 1
                continue

            if new_status == 'Dispatched':
                if not tracking_number:
                    results.append({
                        'orderNumber': order_number,
                        'status': 'error',
                        'message': 'trackingNumber is required for Dispatched status',
                    })
                    failed += 1
                    continue
                if not courier_partner_name:
                    results.append({
                        'orderNumber': order_number,
                        'status': 'error',
                        'message': 'courierPartnerName is required for Dispatched status',
                    })
                    failed += 1
                    continue

            if new_status == 'Re - Dispatched':
                if not tracking_number:
                    _logger.warning('No tracking number for Re-Dispatched: %s', order_number)
                if not courier_partner_name:
                    _logger.warning('No courierPartnerName for Re-Dispatched: %s', order_number)

            # ── Find the order line ───────────────────────────────────────────
            order_line = request.env['sale.order.line'].sudo().search(
                [('order_number', '=', order_number)], limit=1
            )
            if not order_line:
                results.append({
                    'orderNumber': order_number,
                    'status': 'error',
                    'message': f'Order number "{order_number}" not found',
                    'code': 404,
                })
                failed += 1
                continue

            sale_order      = order_line.order_id
            last_sale_order = sale_order

            # ── Row-level lock: serialise writes to this sale_order row ───────
            # Prevents PostgreSQL SerializationFailure when the website sends
            # multiple concurrent requests for lines in the same invoice.
            request.env.cr.execute(
                "SELECT id FROM sale_order WHERE id = %s FOR UPDATE",
                (sale_order.id,)
            )
            # Flush ORM cache so compute reads freshly committed DB values.
            sale_order.order_line.invalidate_recordset(['availability_status'])
            sale_order.invalidate_recordset(['sdk_augmont_status'])

            availability_value = STATUS_MAP[new_status]

            _logger.info(
                "   [%s] orderNumber=%s | %s → %s",
                'SINGLE' if is_single else 'BULK',
                order_number, order_line.availability_status, availability_value,
            )
            _logger.info(
                "   Invoice: %s (%s) | Customer: %s",
                sale_order.name,
                sale_order.sdk_augmont_number,
                sale_order.partner_id.name,
            )

            try:
                old_line_status = order_line.availability_status

                # ── Write the line status ─────────────────────────────────────
                order_line.sudo().with_context(from_website_api=True).write({
                    'availability_status': availability_value
                })
                _logger.info(
                    "   ✅ Line %s: %s → %s",
                    order_number, old_line_status, availability_value,
                )
                _logger.info(
                    "   ✅ Invoice Status (after recompute): %s",
                    sale_order.sdk_augmont_status,
                )

                # ── Optional order-level fields ───────────────────────────────
                update_vals = {}
                if new_status == 'Payment Completed' and utr_number:
                    update_vals['utr_number'] = utr_number
                if new_status in ['Dispatched', 'Re - Dispatched']:
                    if courier_partner_name:
                        update_vals['courier_partner_name'] = courier_partner_name
                    if tracking_number:
                        update_vals['tracking_number'] = tracking_number
                    if tracking_url:
                        update_vals['tracking_url'] = tracking_url
                if update_vals:
                    sale_order.sudo().with_context(from_website_api=True).write(update_vals)

                # ── Auto-confirm ──────────────────────────────────────────────
                if (sale_order.sdk_augmont_status == 'Order Confirmed'
                        and sale_order.state in ('draft', 'sent')):
                    try:
                        _logger.info(
                            "🔄 Auto-confirming %s (triggered by %s → %s)",
                            sale_order.name, order_number, availability_value,
                        )
                        sale_order.sudo().with_context(
                            from_website_api=True
                        ).action_confirm_wrapper()
                        _logger.info(
                            "✅ %s confirmed → state: %s",
                            sale_order.name, sale_order.state,
                        )
                    except Exception as _ce:
                        _logger.error("❌ Auto-confirm failed for %s: %s",
                                      sale_order.name, _ce)

                # ── Auto-cancel ───────────────────────────────────────────────
                if (availability_value == 'cancelled'
                        and sale_order.sdk_augmont_status == 'Cancelled'
                        and sale_order.state not in ['cancel', 'done']):
                    try:
                        _logger.info(
                            "🔄 Auto-cancelling %s (all lines cancelled via website)",
                            sale_order.name,
                        )
                        sale_order.sudo().with_context(
                            from_website_api=True
                        ).action_cancel()
                        _logger.info(
                            "✅ %s cancelled → state: %s",
                            sale_order.name, sale_order.state,
                        )
                    except Exception as _xe:
                        _logger.error("❌ Auto-cancel failed for %s: %s",
                                      sale_order.name, _xe)

                # ── Chatter ───────────────────────────────────────────────────
                msg = f"<p><b>Status updated via API:</b></p><ul>"
                msg += f"<li><b>New Status:</b> {new_status}</li>"
                msg += f"<li><b>Order Number:</b> {order_number}</li>"
                if utr_number and new_status in ['Payment Completed', 'Payment Pending']:
                    msg += f"<li><b>UTR:</b> {utr_number}</li>"
                if new_status in ['Dispatched', 'Re - Dispatched']:
                    if tracking_number:
                        msg += f"<li><b>Tracking Number:</b> {tracking_number}</li>"
                    if courier_partner_name:
                        msg += f"<li><b>Courier Partner:</b> {courier_partner_name}</li>"
                    if tracking_url:
                        msg += f"<li><b>Tracking URL:</b> {tracking_url}</li>"
                if comment:
                    msg += f"<li><b>Comment:</b> {comment}</li>"
                if updated_by_info:
                    msg += (
                        f"<li><b>Updated By:</b> "
                        f"{updated_by_info.get('name', 'Unknown')} "
                        f"({updated_by_info.get('email', '')})</li>"
                    )
                msg += "</ul>"
                sale_order.sudo().message_post(
                    body=Markup(msg),
                    subject="Status Update via API",
                    message_type='notification',
                )

                results.append({
                    'orderNumber':        order_number,
                    'status':             'success',
                    'line_status':        availability_value,
                    'line_status_display': new_status,
                    'invoice_status':     sale_order.sdk_augmont_status,
                })
                processed += 1

            except Exception as entry_err:
                _logger.exception(
                    "   ❌ ERROR processing orderNumber=%s: %s",
                    order_number, entry_err,
                )
                results.append({
                    'orderNumber': order_number,
                    'status': 'error',
                    'message': str(entry_err),
                })
                failed += 1

        # ── Build response ────────────────────────────────────────────────────
        final_invoice_status = (
            last_sale_order.sdk_augmont_status if last_sale_order else None
        )

        _logger.info(
            "◀◀ WEBSITE → ODOO | %s | COMPLETE | mode=%s | "
            "processed=%d failed=%d invoice_status=%s",
            fields.Datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'single' if is_single else 'bulk',
            processed, failed, final_invoice_status,
        )
        _logger.info("=" * 80)

        if is_single:
            # ── Single response: same format as before (backward-compatible) ──
            first = results[0] if results else {}
            if first.get('status') == 'success':
                r = first
                if updated_by_info:
                    updated_by_response = {
                        'id':    None,
                        'name':  updated_by_info.get('name'),
                        'login': updated_by_info.get('email'),
                        'email': updated_by_info.get('email'),
                    }
                else:
                    user = last_sale_order.write_uid if last_sale_order else None
                    updated_by_response = {
                        'id':    user.id    if user else None,
                        'name':  user.name  if user else None,
                        'login': user.login if user else None,
                        'email': user.email if user else None,
                    } if user else None

                response_data = {
                    'status': 'success',
                    'data': {
                        'invoice_number': (
                            last_sale_order.sdk_augmont_number
                            if last_sale_order and hasattr(last_sale_order, 'sdk_augmont_number')
                            else ''
                        ),
                        'order_number':        r['orderNumber'],
                        'line_status':         r['line_status'],
                        'line_status_display': r['line_status_display'],
                        'invoice_status':      r['invoice_status'],
                        'current_status':      r['line_status_display'],
                        'augmont_order_id': (
                            last_sale_order.augmont_order_unique_id
                            if last_sale_order and hasattr(last_sale_order, 'augmont_order_unique_id')
                            else None
                        ),
                        'utr_number': (
                            last_sale_order.utr_number
                            if last_sale_order and hasattr(last_sale_order, 'utr_number')
                            else None
                        ),
                        'tracking_number': (
                            last_sale_order.tracking_number
                            if last_sale_order and hasattr(last_sale_order, 'tracking_number')
                            else None
                        ),
                        'courier_partner_name': (
                            last_sale_order.courier_partner_name
                            if last_sale_order and hasattr(last_sale_order, 'courier_partner_name')
                            else None
                        ),
                        'updated_by':   updated_by_response,
                        'last_updated': (
                            last_sale_order.write_date.strftime('%Y-%m-%d %H:%M:%S')
                            if last_sale_order and last_sale_order.write_date
                            else None
                        ),
                    }
                }
                return Response(
                    json.dumps(response_data, ensure_ascii=False),
                    content_type='application/json', status=200
                )
            else:
                # single entry failed — return error preserving old format
                err_msg  = first.get('message', 'Update failed')
                err_code = first.get('code', 400)
                return Response(
                    json.dumps({'status': 'error', 'message': err_msg,
                                'code': err_code}),
                    content_type='application/json', status=err_code
                )

        else:
            # ── Bulk response ─────────────────────────────────────────────────
            overall = (
                'success'         if failed == 0 else
                'partial_success' if processed > 0 else
                'error'
            )
            return Response(
                json.dumps({
                    'status':         overall,
                    'processed':      processed,
                    'failed':         failed,
                    'invoice_status': final_invoice_status,
                    'results':        results,
                }, ensure_ascii=False, default=str),
                content_type='application/json',
                status=200 if failed == 0 else (207 if processed > 0 else 400)
            )


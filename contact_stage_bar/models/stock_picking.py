from markupsafe import Markup
from datetime import timedelta
from odoo.exceptions import RedirectWarning
import requests
from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError

import logging
_logger = logging.getLogger(__name__)

class StockMove(models.Model):
    _inherit = 'stock.move'

    weight_carat = fields.Char(related="product_id.product_tmpl_id.weight_carat", string="Carat Weight")
    lgd_stock_number = fields.Char(related="product_id.product_tmpl_id.lgd_stock_number", string="LGD SKU")
    order_specific = fields.Char(related="product_id.product_tmpl_id.order_specific", string="Order Specific Notes")
    item_notes = fields.Char(related="product_id.product_tmpl_id.item_notes", string="Item Notes")
    default_qc_requirements = fields.Char(
        related="product_id.product_tmpl_id.default_qc_requirements",
        string="Default QC Requirements"
    )

    is_order_specific = fields.Boolean(string="Order Specific Notes Check")
    is_item_notes = fields.Boolean(string="Item Notes Check")
    is_default_qc_requirements = fields.Boolean(string="Default QC Requirements Check")
    is_weight_carat = fields.Boolean(string="Carat Weight Check")

    is_pass = fields.Boolean(default=False)
    is_fail = fields.Boolean(default=False)

    qc_status = fields.Selection(
        [('pass', 'Passed'), ('fail', 'Failed')],
        compute="_compute_qc_status",
        store=True,
        tracking=True
    )

    failure_reason = fields.Char(string="Failure Reason", copy=False)

    # ---------------------------------------------------------
    # HARD CONSTRAINT: cannot be pass + fail
    # ---------------------------------------------------------
    @api.constrains('is_pass', 'is_fail')
    def _check_qc_flags(self):
        for rec in self:
            if rec.is_pass and rec.is_fail:
                raise ValidationError(
                    _("A product cannot be marked as both QC Pass and QC Fail.")
                )

    # ---------------------------------------------------------
    # COMPUTE QC STATUS
    # ---------------------------------------------------------
    @api.depends('is_pass', 'is_fail')
    def _compute_qc_status(self):
        for rec in self:
            if rec.is_fail:
                rec.qc_status = 'fail'
            elif rec.is_pass:
                rec.qc_status = 'pass'
            else:
                rec.qc_status = False

    # ---------------------------------------------------------
    # QC PASS ACTION
    # ---------------------------------------------------------
    def action_pass(self):
        import datetime as _dt
        for move in self:
            _logger.info(
                "=" * 60 + "\n"
                "🟢 [QC PASS] Button clicked | %s | Move ID=%s | Product=%s | Picking=%s",
                _dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                move.id,
                move.product_id.display_name,
                move.picking_id.name if move.picking_id else 'N/A'
            )

            # 🔒 HARD LOCK — no QC flip-flop
            if move.qc_status:
                raise UserError(
                    _("QC decision already taken and cannot be changed.")
                )

            missing_fields = []

            if not move.is_order_specific:
                missing_fields.append("Order Specific Check")
            if not move.is_item_notes:
                missing_fields.append("Item Notes")
            if not move.is_default_qc_requirements:
                missing_fields.append("Default QC Requirements")
            if not move.is_weight_carat:
                missing_fields.append("Weight in Carat")

            if missing_fields:
                raise UserError(
                    "Please complete the following QC checks:\n- " +
                    "\n- ".join(missing_fields)
                )

            # MARK PASS
            move.is_pass = True
            move.is_fail = False

            # -------------------------------
            # FIND RELATED SALE ORDER & LINE
            # UPDATE LINE AVAILABILITY to payment_pending
            # -------------------------------
            sale_order = None
            picking = move.picking_id

            if picking:
                sale_order = picking.sale_id

                # Try direct link via purchase_line_id.sale_order_id
                if not sale_order and move.purchase_line_id and move.purchase_line_id.sale_order_id:
                    sale_order = move.purchase_line_id.sale_order_id

                # If no direct sale order, try to find via PO origin
                if not sale_order and picking.origin:
                    purchase_order = self.env['purchase.order'].search(
                        [('name', '=', picking.origin)],
                        limit=1
                    )
                    if purchase_order and purchase_order.origin:
                        sale_order = self._find_sale_order_from_origin(purchase_order.origin, move.product_id)

                # If still not found, try direct SO search
                if not sale_order and picking.origin:
                    sale_order = self.env['sale.order'].search(
                        [('name', '=', picking.origin)],
                        limit=1
                    )

                # ── FALLBACK 4: via move.purchase_line_id → direct sale_order_id or PO.origin ──
                if not sale_order and move.purchase_line_id:
                    # Direct link: purchase.order.line has sale_order_id
                    if move.purchase_line_id.sale_order_id:
                        sale_order = move.purchase_line_id.sale_order_id
                        _logger.info(
                            "✅ [QC PASS] SO found via purchase_line_id.sale_order_id | SO: %s",
                            sale_order.name
                        )
                    else:
                        po = move.purchase_line_id.order_id
                        if po and po.origin:
                            sale_order = self._find_sale_order_from_origin(po.origin, move.product_id)
                            if sale_order:
                                _logger.info(
                                    "✅ [QC PASS] SO found via purchase_line_id origin | PO: %s | SO: %s",
                                    po.name, sale_order.name
                                )

                # ── FALLBACK 5: via product match on in_qc_process SO lines ──────
                if not sale_order and move.product_id:
                    so_line = self.env['sale.order.line'].search([
                        ('product_id', '=', move.product_id.id),
                        ('availability_status', '=', 'in_qc_process'),
                    ], limit=1)
                    if so_line and so_line.order_id:
                        sale_order = so_line.order_id
                        _logger.info(
                            "✅ [QC PASS] SO found via product+in_qc_process match | Product: %s | SO: %s",
                            move.product_id.display_name, sale_order.name
                        )

                # ── FALLBACK 6: via lgd_stock_number match ────────────────────
                if not sale_order and move.lgd_stock_number:
                    so_line = self.env['sale.order.line'].search([
                        ('lgd_stock_number', '=', move.lgd_stock_number),
                        ('availability_status', '=', 'in_qc_process'),
                    ], limit=1)
                    if so_line and so_line.order_id:
                        sale_order = so_line.order_id
                        _logger.info(
                            "✅ [QC PASS] SO found via lgd_stock_number | Stock#: %s | SO: %s",
                            move.lgd_stock_number, sale_order.name
                        )

            if not sale_order:
                _logger.warning(
                    "⚠️ [QC PASS] No sale order found for move %s | Picking: %s | Origin: %s",
                    move.id, picking.name if picking else 'N/A', picking.origin if picking else 'N/A'
                )
                continue

            # Find and update the sale order line
            so_line = sale_order.order_line.filtered(
                lambda l: l.product_id.id == move.product_id.id
            )[:1]

            if not so_line and move.lgd_stock_number:
                so_line = sale_order.order_line.filtered(
                    lambda l: l.lgd_stock_number == move.lgd_stock_number
                )[:1]

            # Update the line availability to "payment_pending"
            if so_line:
                _logger.info(
                    "🔄 [QC PASS] BEFORE Update | Line: %s | Old Status: %s",
                    so_line.order_number or so_line.product_id.name,
                    so_line.availability_status
                )
                
                # Write the new status
                so_line.write({'availability_status': 'payment_pending'})
                
                _logger.info(
                    "✅ [QC PASS] AFTER Update | Line: %s | New Status: %s",
                    so_line.order_number or so_line.product_id.name,
                    so_line.availability_status
                )
                
                # FORCE recompute Order Status
                sale_order.invalidate_recordset(['sdk_augmont_status'])
                sale_order._compute_order_status_from_lines()
                
                _logger.info(
                    "🔄 [QC PASS] Order Status Recomputed | Order: %s | Status: %s",
                    sale_order.name,
                    sale_order.sdk_augmont_status
                )

                try:
                    sale_order._call_augmont_status_api('Payment Pending')
                    _logger.info(
                        "✅ [QC PASS API] 'Payment Pending' pushed to website | Order: %s",
                        sale_order.name
                    )
                except Exception as _api_err:
                    _logger.error(
                        "❌ [QC PASS API] Failed to push 'Payment Pending' for %s: %s",
                        sale_order.name, _api_err
                    )

        #   - If all Pass (Payment Pending)  → normal workflow continues.
        #   - If any Fail (QC Fail)          → qc_fail lines → Cancelled + website updated.
        # This satisfies the requirement that Validate is always visible irrespective
        # of the order-level status being [QC Fail] or [Payment Pending].
        pickings_to_validate = self.mapped('picking_id').filtered(
            lambda p: p.location_id.id == 18 and p.location_dest_id.id == 10
            and p.state not in ('done', 'cancel')
        )
        for qc_picking in pickings_to_validate:
            all_moves = qc_picking.move_ids_without_package.filtered(
                lambda m: m.state not in ('done', 'cancel')
            )
            undecided = all_moves.filtered(lambda m: not m.qc_status)
            if not undecided and all_moves:
                _logger.info(
                    "⏸ [QC AUTO-VALIDATE] Skipped for picking %s — user must click Validate manually",
                    qc_picking.name
                )

        return True

    # ---------------------------------------------------------
    # QC FAIL ACTION
    # ---------------------------------------------------------
    def action_fail(self):
        import datetime as _dt
        for move in self:
            _logger.info(
                "=" * 60 + "\n"
                "🔴 [QC FAIL] Button clicked | %s | Move ID=%s | Product=%s | Picking=%s",
                _dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                move.id,
                move.product_id.display_name,
                move.picking_id.name if move.picking_id else 'N/A'
            )

            # 🔒 HARD LOCK — no QC flip-flop
            if move.qc_status:
                raise UserError(
                    _("QC decision already taken and cannot be changed.")
                )

            # Now both Pass & Fail require the same four Q.C checks.
            missing_fields = []
            if not move.is_order_specific:
                missing_fields.append("Order Specific Check")
            if not move.is_item_notes:
                missing_fields.append("Item Notes")
            if not move.is_default_qc_requirements:
                missing_fields.append("Default QC Requirements")
            if not move.is_weight_carat:
                missing_fields.append("Weight in Carat")

            if missing_fields:
                raise UserError(
                    _("Please complete the following QC checks before marking as Fail:\n- ")
                    + "\n- ".join(missing_fields)
                )

            # ❌ MARK FAIL
            move.is_fail = True
            move.is_pass = False

            # -------------------------------
            # FIND RELATED SALE ORDER & LINE
            # UPDATE LINE AVAILABILITY to qc_fail
            # -------------------------------
            sale_order = None
            picking = move.picking_id

            if picking:
                sale_order = picking.sale_id

                # Try direct link via purchase_line_id.sale_order_id
                if not sale_order and move.purchase_line_id and move.purchase_line_id.sale_order_id:
                    sale_order = move.purchase_line_id.sale_order_id

                if not sale_order and picking.origin:
                    purchase_order = self.env['purchase.order'].search(
                        [('name', '=', picking.origin)],
                        limit=1
                    )
                    if purchase_order and purchase_order.origin:
                        sale_order = self._find_sale_order_from_origin(purchase_order.origin, move.product_id)

                if not sale_order and picking.origin:
                    sale_order = self.env['sale.order'].search(
                        [('name', '=', picking.origin)],
                        limit=1
                    )

                # Fallback via purchase_line_id → PO.origin
                if not sale_order and move.purchase_line_id:
                    if move.purchase_line_id.sale_order_id:
                        sale_order = move.purchase_line_id.sale_order_id
                    else:
                        po = move.purchase_line_id.order_id
                        if po and po.origin:
                            sale_order = self._find_sale_order_from_origin(po.origin, move.product_id)

                # Fallback via product match in in_qc_process state
                if not sale_order and move.product_id:
                    so_line = self.env['sale.order.line'].search([
                        ('product_id', '=', move.product_id.id),
                        ('availability_status', '=', 'in_qc_process'),
                    ], limit=1)
                    if so_line and so_line.order_id:
                        sale_order = so_line.order_id

            if not sale_order:
                continue

            # Find and update the sale order line
            so_line = sale_order.order_line.filtered(
                lambda l: l.product_id.id == move.product_id.id
            )[:1]

            if not so_line and move.lgd_stock_number:
                so_line = sale_order.order_line.filtered(
                    lambda l: l.lgd_stock_number == move.lgd_stock_number
                )[:1]

            # Update the line availability to "qc_fail"
            if so_line:
                _logger.info(
                    "🔄 [QC FAIL] BEFORE Update | Line: %s | Old Status: %s",
                    so_line.order_number or so_line.product_id.name,
                    so_line.availability_status
                )
                
                # Write the new status
                so_line.write({'availability_status': 'qc_fail'})
                
                _logger.info(
                    "❌ [QC FAIL] AFTER Update | Line: %s | New Status: %s",
                    so_line.order_number or so_line.product_id.name,
                    so_line.availability_status
                )
                
                # FORCE recompute Order Status
                if sale_order:
                    sale_order.invalidate_recordset(['sdk_augmont_status'])
                    sale_order._compute_order_status_from_lines()
                    
                    _logger.info(
                        "🔄 [QC FAIL] Order Status Recomputed | Order: %s | Status: %s",
                        sale_order.name,
                        sale_order.sdk_augmont_status
                    )

                    try:
                        sale_order._call_augmont_status_api(
                            sale_order.sdk_augmont_status or 'QC Fail'
                        )
                        _logger.info(
                            "✅ [QC FAIL API] Synced | Order: %s | Per-line statuses sent to website",
                            sale_order.name
                        )
                    except Exception as _api_err:
                        _logger.error(
                            "❌ [QC FAIL API] Failed | Order: %s | Error: %s",
                            sale_order.name, _api_err
                        )


        #   - If all Pass (Payment Pending)  → normal workflow continues.
        #   - If any Fail (QC Fail)          → qc_fail lines → Cancelled + website updated.
        # This satisfies the requirement that Validate is always visible irrespective
        # of the order-level status being [QC Fail] or [Payment Pending].
        pickings_to_validate = self.mapped('picking_id').filtered(
            lambda p: p.location_id.id == 18 and p.location_dest_id.id == 10
            and p.state not in ('done', 'cancel')
        )
        for qc_picking in pickings_to_validate:
            all_moves = qc_picking.move_ids_without_package.filtered(
                lambda m: m.state not in ('done', 'cancel')
            )
            undecided = all_moves.filtered(lambda m: not m.qc_status)
            if not undecided and all_moves:
                _logger.info(
                    "⏸ [QC AUTO-VALIDATE] Skipped for picking %s — user must click Validate manually",
                    qc_picking.name
                )

        return True


  
class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def _find_sale_order_from_origin(self, origin, product_id=None):
        """Find sale order(s) from a PO origin field that may be comma-separated.
        When product_id is given, prefer the SO that contains a line with that product.
        """
        if not origin:
            return self.env['sale.order']
        origin_names = [o.strip() for o in origin.split(',') if o.strip()]
        sale_orders = self.env['sale.order'].search([('name', 'in', origin_names)])
        if not sale_orders:
            return self.env['sale.order']
        if len(sale_orders) == 1 or not product_id:
            return sale_orders[0]
        # Prefer the SO that has a line with the matching product
        for so in sale_orders:
            if so.order_line.filtered(lambda l: l.product_id.id == product_id.id):
                return so
        return sale_orders[0]

    @api.model
    def _get_augmont_locations(self):
        """Lookup or auto-create custom locations and picking types for the Augmont flow."""
        Location = self.env['stock.location']
        PickingType = self.env['stock.picking.type']
        warehouse = self.env['stock.warehouse'].search([], limit=1)
        wh_parent = warehouse.lot_stock_id.location_id if warehouse else False

        # Standard Odoo locations (by usage)
        vendor_loc = Location.search([('usage', '=', 'supplier')], limit=1)
        customer_loc = Location.search([('usage', '=', 'customer')], limit=1)

        # Custom locations (search by name, create if missing)
        qc_loc = Location.search([('name', '=', 'Quality Control'), ('usage', '=', 'internal')], limit=1)
        if not qc_loc and wh_parent:
            qc_loc = Location.create({
                'name': 'Quality Control',
                'usage': 'internal',
                'location_id': wh_parent.id,
            })

        inv_loc = Location.search([('name', '=', 'Inventory'), ('usage', '=', 'internal')], limit=1)
        if not inv_loc and wh_parent:
            inv_loc = Location.create({
                'name': 'Inventory',
                'usage': 'internal',
                'location_id': wh_parent.id,
            })

        # QC picking type (search by sequence_code, create if missing)
        qc_type = PickingType.search([('sequence_code', '=', 'QC')], limit=1)
        if not qc_type:
            qc_type = PickingType.create({
                'name': 'QC To Inventory',
                'code': 'internal',
                'sequence_code': 'QC',
                'default_location_src_id': qc_loc.id if qc_loc else False,
                'default_location_dest_id': inv_loc.id if inv_loc else False,
                'warehouse_id': warehouse.id if warehouse else False,
            })

        return {
            'vendor': vendor_loc.id if vendor_loc else False,
            'customer': customer_loc.id if customer_loc else False,
            'quality_control': qc_loc.id if qc_loc else False,
            'inventory': inv_loc.id if inv_loc else False,
            'qc_picking_type': qc_type.id if qc_type else False,
        }

    sale_id = fields.Many2one('sale.order', store=True)

    shipping_address = fields.Text(
        related='sale_id.shipping_address',
        string='Shipping Address',
        
    )
    billing_address = fields.Text(
        related='sale_id.billing_address',
        string='Billing Address',
        store=True
    )
    sdk_augmont_number = fields.Char(related='sale_id.sdk_augmont_number',string="Online Order Number")
    state = fields.Selection(selection_add=[('pack', 'Packed')])
    phone = fields.Char(related="partner_id.phone", string="Phone", store=True)
    email_id = fields.Char(related="partner_id.email", string="Email Id", store=True)

    street1 = fields.Char(related='partner_id.street', string="Street1")

    street_2 = fields.Char(related='partner_id.street2', string="Street 2")
    city = fields.Char(related='partner_id.city', string="City")
    state_id = fields.Many2one(related='partner_id.state_id', string="State")
    zip = fields.Char(related='partner_id.zip', string="ZIP")
    country = fields.Many2one(related='partner_id.country_id', string="Country")

    tracker_name = fields.Char(string="Delivered By")
    tracking_number = fields.Char(string="Courier Number",copy="False")
    tracking_url = fields.Char(string="Tracking Url",copy="False")
    utr_number = fields.Char(string="UTR Number", related='sale_id.utr_number', store=True, readonly=True, help="UTR Number from Pack operation")
    is_confirm = fields.Boolean(string="Is Confirm",compute="_compute_status_waiting")
    location = fields.Selection([('mumbai', 'India'), ('surat', 'USA')], string='Location',store=True)
    partner_id = fields.Many2one(comodel_name='res.partner', string='Vendor')


    # ── Cancelled Stone tab fields ────────────────────────────────────────────
    # These five fields capture information about a stone that has been cancelled / returned during the logistics process.
    cancelled_stone_person_name = fields.Char(
        string="Person Name:",
        help="Name of the person handling the cancelled stone."
    )
    cancelled_stone_phone = fields.Char(
        string="Phone Number:",
        help="Contact phone number."
    )
    cancelled_stone_destination = fields.Char(
        string="Destination Location:",
        help="Alphanumeric destination location for the cancelled stone."
    )
    cancelled_stone_delivery_datetime = fields.Datetime(
        string="Approx Delivery Date & Time:",
        help="Approximate date & time when the stone is expected to be delivered."
    )
    cancelled_stone_return_date = fields.Datetime(
        string="Returned Date & Time:",
        help="Actual date & time when the stone was returned."
    )

    _sql_constraints = []
    


    procurement_reference = fields.Char(
        string="Procurement Reference",
        compute="_compute_procurement_reference",
        store=True,
        help="SDK Augmont Number from the related Sale Order"
    )
    
    @api.depends('origin', 'purchase_id', 'purchase_id.origin', 'sale_id', 'sale_id.sdk_augmont_number')
    def _compute_procurement_reference(self):
        """
        Get the sdk_augmont_number from related Sale Order.
        Optimized for batch processing.
        """
        for picking in self:
            picking.procurement_reference = False
        
        # Method 1: Handle pickings with direct sale_id
        pickings_with_sale = self.filtered(lambda p: p.sale_id and p.sale_id.sdk_augmont_number)
        for picking in pickings_with_sale:
            picking.procurement_reference = picking.sale_id.sdk_augmont_number
        
        # Method 2: Handle pickings with purchase_id that has origin
        pickings_with_po = self.filtered(
            lambda p: not p.procurement_reference and p.purchase_id and p.purchase_id.origin
        )
        
        if pickings_with_po:
            # Collect all PO origins (split comma-separated values)
            all_origin_names = set()
            for p in pickings_with_po:
                for o in p.purchase_id.origin.split(','):
                    name = o.strip()
                    if name:
                        all_origin_names.add(name)

            # Single query to get all related sale orders
            sale_orders = self.env['sale.order'].search([
                ('name', 'in', list(all_origin_names))
            ])

            # Create mapping: sale order name -> sdk_augmont_number
            so_sdk_map = {
                so.name: so.sdk_augmont_number
                for so in sale_orders
                if so.sdk_augmont_number
            }

            # Update pickings
            for picking in pickings_with_po:
                po_origin = picking.purchase_id.origin
                # Split comma-separated origins and find matching sdk numbers
                origin_names = [o.strip() for o in po_origin.split(',') if o.strip()]
                sdk_numbers = [so_sdk_map[name] for name in origin_names if name in so_sdk_map]
                if sdk_numbers:
                    picking.procurement_reference = ', '.join(sdk_numbers)
        
        # Method 3: Handle remaining pickings using origin field
        remaining_pickings = self.filtered(lambda p: not p.procurement_reference and p.origin)
        
        if remaining_pickings:
            # Collect all possible origin references
            all_origins = set()
            for picking in remaining_pickings:
                origins = picking.origin.split(', ')
                all_origins.update([o.strip() for o in origins if o.strip()])
            
            if all_origins:
                # Get sale orders
                sale_orders = self.env['sale.order'].search([
                    ('name', 'in', list(all_origins))
                ])
                so_sdk_map = {
                    so.name: so.sdk_augmont_number 
                    for so in sale_orders 
                    if so.sdk_augmont_number
                }
                
                # Initialize po_to_sdk_map HERE - BEFORE the if block
                po_to_sdk_map = {}
                
                # Get purchase orders for origins not found in sale orders
                purchase_orders = self.env['purchase.order'].search([
                    ('name', 'in', list(all_origins))
                ])
                
                # For POs, get their sale order origins (handle comma-separated)
                all_po_origin_names = set()
                for po in purchase_orders:
                    if po.origin:
                        for o in po.origin.split(','):
                            name = o.strip()
                            if name:
                                all_po_origin_names.add(name)
                if all_po_origin_names:
                    related_sale_orders = self.env['sale.order'].search([
                        ('name', 'in', list(all_po_origin_names))
                    ])
                    # Now build the po_to_sdk_map
                    for po in purchase_orders:
                        if po.origin:
                            po_origin_names = [o.strip() for o in po.origin.split(',') if o.strip()]
                            related_so = related_sale_orders.filtered(lambda s: s.name in po_origin_names)
                            if related_so:
                                sdk_nums = [s.sdk_augmont_number for s in related_so if s.sdk_augmont_number]
                                if sdk_nums:
                                    po_to_sdk_map[po.name] = ', '.join(sdk_nums)
                
                # Update remaining pickings
                for picking in remaining_pickings:
                    origins = [o.strip() for o in picking.origin.split(', ') if o.strip()]
                    for origin in origins:
                        # Check if it's a sale order
                        if origin in so_sdk_map:
                            picking.procurement_reference = so_sdk_map[origin]
                            break
                        # Check if it's a purchase order
                        elif origin in po_to_sdk_map:
                            picking.procurement_reference = po_to_sdk_map[origin]
                            break


    is_order_completed = fields.Boolean(string="Is Order Completed", default=False)
                   

    def action_order_completed(self):
        """
        Update sale order status to 'Order Completed'
        This is called AFTER delivery when user clicks 'Order Completed' button
        Workflow: Delivered → Order Completed
        """
        self.ensure_one()
        
        # Check if all tracking fields are filled
        if not self.tracking_number or not self.tracker_name:
            raise UserError(_('Please fill all tracking details (Delivered By, Tracking Number) before completing the order.'))
        
        # Get sale order
        sale_order = self.sale_id
        if not sale_order and self.origin:
            # Try to find sale order through purchase order
            purchase_order = self.env['purchase.order'].search([
                ('name', '=', self.origin)
            ], limit=1)
            if purchase_order and purchase_order.origin:
                sale_order = self._find_sale_order_from_origin(purchase_order.origin)

        if not sale_order:
            raise UserError(_('No sale order found for this picking.'))
        

        _eligible_check = sale_order.order_line.filtered(
            lambda l: l.availability_status in [
                'payment_completed', 'dispatched', 're_dispatched',
                'return_of_order', 'delivered'
            ]
        )
        if not _eligible_check:
            raise UserError("Order already completed!!")
        
        _logger.info(
            "🏁 [ORDER COMPLETED CLICKED] Picking: %s | Order: %s | Current Status: %s",
            self.name, sale_order.name, sale_order.sdk_augmont_status
        )

        eligible_lines = sale_order.order_line.filtered(
            lambda l: l.availability_status in [
                'payment_completed', 'dispatched', 're_dispatched',
                'return_of_order', 'delivered'
            ]
        )
        
        if eligible_lines:
            _logger.info(
                "🔄 [ORDER COMPLETED] Updating %d lines to 'order_completed':",
                len(eligible_lines)
            )
            for line in eligible_lines:
                _logger.info(
                    "   Line: %s | %s → order_completed",
                    line.order_number or line.product_id.name,
                    line.availability_status
                )
            
            eligible_lines.write({'availability_status': 'order_completed'})
            
            _logger.info(
                "✅ [ORDER COMPLETED] Updated %d lines to 'order_completed' for order %s",
                len(eligible_lines), sale_order.name
            )
        else:
            _logger.warning(
                "⚠️ [ORDER COMPLETED] No eligible lines found for order %s",
                sale_order.name
            )
        
        # Recompute order status from lines
        # This will trigger Condition 13 if all active lines are order_completed
        sale_order.invalidate_recordset(['sdk_augmont_status'])
        sale_order._compute_order_status_from_lines()
        
        new_status = sale_order.sdk_augmont_status
        _logger.info(
            "📊 [ORDER COMPLETED] Order Status Recomputed | Order: %s | Status: %s",
            sale_order.name, new_status
        )
        
        # Call Augmont API to sync status
        try:
            sale_order._call_augmont_status_api(new_status)
            _logger.info(
                "✅ [API SUCCESS] Order: %s | Status '%s' synced to Augmont",
                sale_order.name, new_status
            )
        except Exception as e:
            _logger.error(
                "❌ [API FAIL] Order: %s | Status '%s' sync failed | Error: %s",
                sale_order.name, new_status, e
            )

        # Mark picking as completed (hide button)
        self.is_order_completed = True
        self.env.cr.commit()
        
        # Post message to chatter
        sale_order.message_post(
            body=_("Order marked as <b>Completed</b> from Dispatch picking %s") % self.name,
            subject="Order Completed"
        )
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Success'),
                'message': _('Order has been marked as completed. Status: %s') % new_status,
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'}
            }
        }


    is_dispatched = fields.Boolean(string="Is Dispatched", default=False)
    is_order_delivered = fields.Boolean(string="Is Order delivered", default=False)


    def action_delivered(self):
        """
        This is called when user clicks 'Delivered' button in Dispatch module
        Workflow: Dispatched/Re-Dispatched/Return of Order → Delivered
        """
        self.ensure_one()

        if not self.tracking_number or not self.tracker_name:
            raise UserError(_('Please fill all tracking details (Delivered By, Tracking Number) before marking as Delivered.'))

        # Get sale order
        sale_order = self.sale_id
        if not sale_order and self.origin:
            purchase_order = self.env['purchase.order'].search([('name', '=', self.origin)], limit=1)
            if purchase_order and purchase_order.origin:
                sale_order = self._find_sale_order_from_origin(purchase_order.origin)

        if not sale_order:
            raise UserError(_("No sale order found!"))

        # Prevent duplicate delivery
        if sale_order.sdk_augmont_status == 'Delivered':
            raise UserError("Order already marked as Delivered!")

        _logger.info(
            "📦 [DELIVERED CLICKED] Picking: %s | Order: %s | Current Status: %s",
            self.name, sale_order.name, sale_order.sdk_augmont_status
        )

        # Update line statuses to 'delivered'
        # Update lines that are in logistics flow (dispatched, re_dispatched, return_of_order)

        active_lines = sale_order.order_line.filtered(
            lambda l: l.availability_status in [
                'payment_completed', 'dispatched', 're_dispatched', 'return_of_order'
            ]
        )
        
        if active_lines:
            _logger.info(
                "🔄 [DELIVERED] Updating %d lines to 'delivered':",
                len(active_lines)
            )
            for line in active_lines:
                _logger.info(
                    "   Line: %s | %s → delivered",
                    line.order_number or line.product_id.name,
                    line.availability_status
                )
            
            active_lines.write({'availability_status': 'delivered'})
            
            _logger.info(
                "✅ [DELIVERED] Updated %d lines to 'delivered' for order %s",
                len(active_lines), sale_order.name
            )
        else:
            _logger.warning(
                "⚠️ [DELIVERED] No eligible lines found for order %s",
                sale_order.name
            )
        
        # Recompute order status from lines
        sale_order.invalidate_recordset(['sdk_augmont_status'])
        sale_order._compute_order_status_from_lines()
        
        new_status = sale_order.sdk_augmont_status
        _logger.info(
            "📊 [DELIVERED] Order Status Recomputed | Order: %s | Status: %s",
            sale_order.name, new_status
        )
        
        # Call Augmont API to sync status
        try:
            sale_order._call_augmont_status_api(new_status)
            _logger.info(
                "✅ [API SUCCESS] Order: %s | Status '%s' synced to Augmont",
                sale_order.name, new_status
            )
        except Exception as e:
            _logger.error(
                "❌ [API FAIL] Order: %s | Status '%s' sync failed | Error: %s",
                sale_order.name, new_status, e
            )

        # Mark picking as delivered (show Order Completed button)
        self.is_order_delivered = True
        self.env.cr.commit()
        
        # Post message to chatter
        sale_order.message_post(
            body=_("Order marked as <b>Delivered</b> from Dispatch picking %s<br/>Delivered By: %s<br/>Tracking: %s") % (
                self.name, self.tracker_name, self.tracking_number
            ),
            subject="Order Delivered"
        )

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Success'),
                'message': _('Order marked as Delivered. Status: %s') % new_status,
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'}
            }
        }

    
    def action_update_tracking_to_augmont(self):
        """
        Send courier and tracking information to Augmont API
        """
        for picking in self:
            # Only for delivery orders
            if picking.picking_type_id.code != 'outgoing':
                continue
                
            if not picking.sdk_augmont_number:
                raise UserError(f"No Augmont invoice number found for delivery {picking.name}")
            
            if not picking.tracker_name and not picking.tracking_number:
                raise UserError(f"No courier or tracking information to update for {picking.name}")
            
            url = "https://policies-afternoon-stud-visible.trycloudflare.com/api/v1/odoo/utr-tracking/update"
            headers = {
                "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJtZXJjaGFudElkIjoiZjA3NDA3YzItZTI1ZS00YjI2LTk1MWUtODliZGQxZjI4YmQ2Iiwic2hvcnROYW1lIjoib2RvbyIsImlhdCI6MTc2MDYxNjU3OCwiZXhwIjoyMDc2MTkyNTc4fQ.XpKKN78VVCezg6BrEhQYxvTs1FTmES59pyrp3zFpmdI",
                "Content-Type": "application/json"
            }
            
            tracking_updates = {}
            if picking.tracker_name:
                tracking_updates["courierPartnerName"] = picking.tracker_name
            if picking.tracking_number:
                tracking_updates["trackingNumber"] = picking.tracking_number
            
            current_user = self.env.user
            payload = {
                "invoiceNumber": picking.sdk_augmont_number,
                "trackingUpdates": tracking_updates,
                "user": {
                    "firstName": current_user.partner_id.name.split()[0] if current_user.partner_id.name else "",
                    "lastName": current_user.partner_id.name.split()[-1] if len(current_user.partner_id.name.split()) > 1 else "",
                    "email": current_user.email,
                    "mobileNumber": current_user.partner_id.phone
                }
            }
            
            _logger.info("Updating tracking for invoice %s", picking.sdk_augmont_number)
            
            try:
                response = requests.post(url,headers=headers, json=payload, timeout=15)
                
                if response.status_code == 200:
                    res_json = response.json()
                    _logger.info("Tracking updated successfully: %s", res_json)
                    picking.message_post(
                        body=f"Tracking info sent to Augmont - Courier: {picking.tracker_name}, Tracking: {picking.tracking_number}",
                        subject="Tracking Update Success"
                    )
                    return res_json
                else:
                    error_msg = f"API error: {response.status_code} - {response.text}"
                    _logger.error(error_msg)
                    raise UserError(error_msg)
                    
            except requests.RequestException as e:
                error_msg = f"Failed to update tracking: {str(e)}"
                _logger.exception(error_msg)
                raise UserError(error_msg)
            
            

    is_packed = fields.Boolean(string="Is Packed", default=False)


    def action_pack(self):
        """Show popup to get UTR number - NO status change yet"""
        for picking in self:
            if picking.state == 'assigned':
                # Get sale order
                sale_order = picking.sale_id
                if not sale_order and picking.origin:
                    # Try to find sale order through purchase order
                    purchase_order = self.env['purchase.order'].search([
                        ('name', '=', picking.origin)
                    ], limit=1)
                    if purchase_order and purchase_order.origin:
                        sale_order = self._find_sale_order_from_origin(purchase_order.origin)
            
                _logger.info(
                    "📦 [PACK CLICKED] Picking: %s | Sale Order: %s | Current Status: %s",
                    picking.name,
                    sale_order.name if sale_order else 'N/A',
                    sale_order.sdk_augmont_status if sale_order else 'N/A'
                )
                
                # Show UTR entry popup (status update happens in wizard on "Yes" click)
                return {
                    'name': _('Payment Confirmation'),
                    'type': 'ir.actions.act_window',
                    'res_model': 'payment.confirmation.wizard',
                    'view_mode': 'form',
                    'target': 'new',
                    'context': {
                        'default_picking_id': picking.id,
                        'default_sale_order_id': sale_order.id if sale_order else False,
                    }
                }
        return True
    
    def _update_reserved_qty(self, picking):
        """Force reserved qty = demanded qty"""
        for move in picking.move_ids_without_package:
            # if move.state not in ['done', 'cancel']:
                # for line in move.move_line_ids:
            move.quantity = move.product_uom_qty
            print("_update_reserved_qty")

    def write(self, vals):
        if 'state' in vals and vals['state'] == 'confirmed':
            for picking in self:
                picking.action_assign()
                self._update_reserved_qty(picking)
        
        
        if 'state' in vals and vals['state'] == 'confirmed':
            print("working")
            for picking in self:
                if picking.state == 'confirmed':
                    picking.action_assign()
                    print("working11")
                    for move in picking.move_ids_without_package:
                        move.quantity = move.product_uom_qty
                        print("working123")
        
        for picking in self:
            # if picking.location_id.id == 9 and picking.location_dest_id.id == 10 and picking.origin:
            purchase = self.env['purchase.order'].search([('name', '=', picking.origin)], limit=1)
            if purchase and purchase.partner_id:
                vals['partner_id'] = purchase.partner_id.id
                vals['location'] = purchase.location
                vals['sdk_augmont_number'] = purchase.order_number
                
        if 'state' in vals and vals['state'] == 'done':
            for picking in self:
                activities = self.env['mail.activity'].search([
                    ('res_model', '=', 'stock.picking'),
                    ('res_id', '=', picking.id),
                    ('activity_type_id', '=', self.env.ref('mail.mail_activity_data_todo').id),
                    ('user_id', '!=', False),
                    ('date_deadline', '!=', False),
                ])
                for act in activities:
                    act.action_done()
                    
        if 'tracking_number' in vals or 'tracking_name' in vals:
            for picking in self:
                if picking.picking_type_id.code == 'outgoing' and picking.sdk_augmont_number:
                    try:
                        picking.action_update_tracking_to_augmont()
                    except Exception as e:
                        _logger.warning(f"Failed to auto-send tracking update: {e}")
                    
        return super().write(vals)
    
    @api.model_create_multi
    def create(self, vals_list):
        pickings = super().create(vals_list)

        for picking in pickings:
            if picking.state == 'waiting':
                picking.action_assign()
                self._update_reserved_qty(picking)

            # if picking.picking_type_id.id == 5 and picking.location_id.id == 9:
            if picking.origin:
                purchase = self.env['purchase.order'].search(
                    [('name', '=', picking.origin)], limit=1
                )
                if purchase and purchase.partner_id:
                    picking.write({
                        'location': purchase.location,
                        'partner_id': purchase.partner_id.id,
                        'sdk_augmont_number': purchase.order_number,
                    })
        return pickings
    
    @api.depends('move_ids_without_package.product_id.qty_available')
    def _compute_status_waiting(self):
        for picking in self:
            picking.is_confirm = False
            if (
                picking.state == 'confirmed'
                and picking.picking_type_id.id == 2
                and any(move.product_id.qty_available > 0 for move in picking.move_ids_without_package)
            ):
                picking.is_confirm = True
                for move in picking.move_ids_without_package:
                    if move.product_id.qty_available >= move.product_uom_qty:
                        move.quantity = move.product_uom_qty
                picking.action_assign()

    def _send_notification_email(self, picking_new_id, group_xml_id, subject_template, body_template, recipient_override=None):
        """Helper to send email and post message"""
        subject = subject_template % picking_new_id.name
        body_html = Markup(body_template) % picking_new_id.name

        email_from = self.env.user.partner_id.email or self.env.company.email

        group = self.env.ref(group_xml_id, raise_if_not_found=False)
        recipients = []
        if group:
            recipients = group.users.mapped("partner_id.email")
            recipients = [email for email in recipients if email]

        if recipient_override:
            recipients = [recipient_override] if isinstance(recipient_override, str) else recipient_override

        if not recipients:
            return

        mail_values = {
            'subject': subject,
            'body_html': body_html,
            'email_from': email_from,
            'email_to': ",".join(recipients),
        }

        self.env['mail.mail'].create(mail_values).send()

        picking_new_id.message_post(
            subject=subject,
            body=body_html,
            message_type='notification'
        )

        if group and group.users:
            activity_type = self.env.ref('mail.mail_activity_data_todo')
            model_id = self.env['ir.model']._get_id('stock.picking')
            for user in group.users:
                self.env['mail.activity'].create({
                    'res_model_id': model_id,
                    'res_id': picking_new_id.id,
                    'activity_type_id': activity_type.id,
                    'summary': "Follow up required",
                    'note': body_html,
                    'user_id': user.id,
                    'date_deadline': fields.Date.today(),
                })

    

    # def _create_purchase_order_for_failed_qc(self, move):
    #     """
    #     Create or append to a draft purchase.order for the partner for this failed move.
    #     Attach relation on the move (replacement_po_id).
    #     """
    #     partner = move.picking_id and move.picking_id.partner_id
    #     if not partner:
    #         return False

    #     product = move.product_id
    #     partner_id = partner.id

    #     # Find an existing draft RFQ (same partner) - adjust criteria as needed
    #     po = self.env['purchase.order'].search([
    #         ('partner_id', '=', partner_id),
    #         ('state', '=', 'draft'),
    #         ('origin', '=', move.picking_id and move.picking_id.origin or False),
    #     ], limit=1)

    #     order_line_vals = (0, 0, {
    #         'product_id': product.id,
    #         'name': product.display_name,
    #         'product_qty': move.product_uom_qty,
    #         'product_uom': move.product_uom.id,
    #         'price_unit': product.standard_price or (move.purchase_line_id.price_unit if move.purchase_line_id else 0.0),
    #         'date_planned': fields.Datetime.now(),
    #         # taxes could be added here: 'taxes_id': [(6, 0, [...])],
    #     })

    #     if po:
    #         po.write({'order_line': [order_line_vals]})
    #     else:
    #         po = self.env['purchase.order'].create({
    #             'partner_id': partner_id,
    #             'picking_type_id': 1,  # set appropriate RFQ picking type if you have one
    #             'origin': move.picking_id and move.picking_id.name or move.picking_id and move.picking_id.origin or False,
    #             'order_line': [order_line_vals],
    #         })

    #     # Link the move to the PO
    #     try:
    #         move.write({'replacement_po_id': po.id})
    #     except Exception:
    #         # fallback: set via sudo if needed
    #         move.sudo().write({'replacement_po_id': po.id})

    #     # Optional: force DB flush if you have transaction visibility problems
    #     # self.env.cr.commit()

    #     # send notification mail to procurement group if present
    #     group = self.env.ref("__export__.res_groups_79_862f912f", raise_if_not_found=False)  # adjust to your group external id
    #     recipients = []
    #     if group:
    #         recipients = group.users.mapped('partner_id.email')
    #         recipients = [r for r in recipients if r]

    #     subject = _("New Purchase Order %s created — replacement for failed QC") % (po.name)
    #     body_html = Markup(
    #         _(
    #             "<p>Dear Procurement Team,</p>"
    #             "<p>A replacement RFQ <strong>%s</strong> was created for product <strong>%s</strong> (qty: %s) "
    #             "from Picking: <strong>%s</strong></p>"
    #         )
    #     ) % (po.name, product.display_name, move.product_uom_qty, move.picking_id and move.picking_id.name or '')

    #     if recipients:
    #         mail_values = {
    #             'subject': subject,
    #             'body_html': body_html,
    #             'email_from': self.env.user.partner_id.email or self.env.company.email,
    #             'email_to': ",".join(recipients),
    #         }
    #         self.env['mail.mail'].create(mail_values).send()

    #     # post message on picking chatter
    #     move.picking_id.message_post(
    #         subject=subject,
    #         body=body_html,
    #         message_type='notification'
    #     )
    #     return po
    


    def _merge_qc_pickings_for_sale(self, sale_order, preferred_master=None):
        """
        Merge ALL QC pickings that belong to the SAME Sale Order (SO).
        Handles:
        ✔ QC origin = PO number
        ✔ procurement_reference = sdk_augmont_number
        ✔ QC missing sale_id
        ✔ Multiple vendor POs for same sale order

        :param preferred_master: If given, this picking will be used as the
            master (merge target) instead of the oldest one. This prevents
            the "Missing Record" error when a just-created picking would
            otherwise be deleted by the merge.
        """

        if not sale_order:
            return False

        Picking = self.env['stock.picking']

        # -------------------------------------------------------
        # STEP 1: Get all POs linked to this sale order
        # -------------------------------------------------------
        purchase_orders = self.env['purchase.order'].search([
            ('origin', '=', sale_order.name)
        ])
        po_names = purchase_orders.mapped('name')      # e.g ['P00107','P00108']
        sdk = sale_order.sdk_augmont_number           # e.g 'AUG-112526726'

        # -------------------------------------------------------
        # STEP 2: Search ALL QC pickings linked by:
        #   • origin = sale_order.name (QC pickings use SO name)
        #   • OR origin in PO names (backward compat for old pickings)
        #   • OR procurement_reference = sdk
        #   • OR sale_id matches
        # This catches ALL valid QC pickings for this sale order.
        # -------------------------------------------------------
        qc_pickings = Picking.search([
            ('picking_type_id', '=', self._get_augmont_locations()['qc_picking_type']),
            ('state', 'in', ['draft', 'confirmed', 'assigned']),  # 'confirmed' added
            '|', ('origin', '=', sale_order.name),      # NEW: QC created with SO name as origin
            '|', ('origin', 'in', po_names),            # OLD: QC created from PO (backward compat)
            '|', ('procurement_reference', '=', sdk),   # computed sdk link
                ('sale_id', '=', sale_order.id),       # direct sale link
        ], order='id asc')

        # No merge if nothing or single record
        if len(qc_pickings) <= 1:
            return qc_pickings[:1]

        # -------------------------------------------------------
        # STEP 3: Use preferred_master if given, else first QC
        # -------------------------------------------------------
        if preferred_master and preferred_master in qc_pickings:
            master = preferred_master
        else:
            master = qc_pickings[0]
        duplicates = qc_pickings - master

        for dup in duplicates:

            # -----------------------------------------------
            # Move all moves from duplicate QC to master QC
            # -----------------------------------------------
            for mv in dup.move_ids_without_package:
                mv.sudo().write({'picking_id': master.id})

            # -----------------------------------------------
            # If completely empty → Cancel and Delete
            # -----------------------------------------------
            if not dup.move_ids_without_package:
                try:
                    if dup.state not in ('done', 'cancel'):
                        dup.action_cancel()
                    dup.unlink()
                except Exception:
                    try:
                        if dup.state not in ('done', 'cancel'):
                            dup.action_cancel()
                    except:
                        pass
            else:
                # Only if something didn't merge
                dup.message_post(body=_("Some moves did not merge into master QC."))

        # -------------------------------------------------------
        # STEP 4: Confirm & assign master QC so it's ready
        # -------------------------------------------------------
        try:
            master.action_confirm()
            master.action_assign()
        except Exception:
            pass

        # -------------------------------------------------------
        # STEP 5: Log merge summary
        # -------------------------------------------------------
        master.message_post(
            body=_(
                "QC merge completed. All QC pickings for Sale Order <b>%s</b> "
                "are now merged into QC <b>%s</b>."
            ) % (sale_order.name, master.name)
        )

        return master



    def _force_picking_ready(self):

        self.ensure_one()
        cr = self.env.cr

        # ── 1. Standard reserve ──────────────────────────────────────────
        try:
            self.action_assign()
        except Exception as e:
            _logger.warning("⚠️ [LGD READY] action_assign failed for %s: %s", self.name, e)

        if self.state == 'assigned':
            _logger.info("✅ [LGD READY] %s already assigned after action_assign()", self.name)
            return

        # ── 2. Create missing move lines manually ────────────────────────
        for mv in self.move_ids_without_package.filtered(
                lambda m: m.state not in ('done', 'cancel')):
            existing_lines = mv.move_line_ids.filtered(
                lambda l: l.state not in ('done', 'cancel'))
            if not existing_lines:
                # No detail operations – create one that covers the full demand
                try:
                    self.env['stock.move.line'].create({
                        'picking_id': self.id,
                        'move_id': mv.id,
                        'product_id': mv.product_id.id,
                        'product_uom_id': mv.product_uom.id,
                        'location_id': mv.location_id.id,
                        'location_dest_id': mv.location_dest_id.id,
                        'quantity': mv.product_uom_qty,
                    })
                    _logger.info(
                        "📋 [LGD READY] Created move_line for move %s qty=%s",
                        mv.id, mv.product_uom_qty)
                except Exception as e:
                    _logger.warning(
                        "⚠️ [LGD READY] move_line create failed for %s: %s", mv.id, e)
            else:
                # Lines exist but quantity may be 0 – fill them in
                for ml in existing_lines:
                    if not ml.quantity:
                        try:
                            ml.quantity = mv.product_uom_qty
                        except Exception:
                            cr.execute(
                                "UPDATE stock_move_line SET quantity=%s WHERE id=%s",
                                (mv.product_uom_qty, ml.id))

        # ── 3. Force state via SQL (bypasses FSM / computed field) ───────
        cr.execute(
            "UPDATE stock_picking SET state = 'assigned' WHERE id = %s",
            (self.id,))

        move_ids = self.move_ids_without_package.filtered(
            lambda m: m.state not in ('done', 'cancel')).ids
        if move_ids:
            cr.execute(
                """UPDATE stock_move
                      SET state    = 'assigned',
                          quantity = product_uom_qty
                    WHERE id = ANY(%s)""",
                (move_ids,))

        # Invalidate ORM cache so reads after this see the new state
        self.invalidate_recordset(['state'])
        self.move_ids_without_package.invalidate_recordset(
            ['state', 'quantity', 'move_line_ids'])

        _logger.info(
            "✅ [LGD READY] %s forced to assigned | moves: %s",
            self.name, move_ids)

    def button_validate(self):
        import datetime as _dt

        # Dynamic location/picking type lookups (auto-create if missing)
        locs = self._get_augmont_locations()
        LOC_VENDOR = locs['vendor']
        LOC_QC = locs['quality_control']
        LOC_INV = locs['inventory']
        PT_QC = locs['qc_picking_type']

        _logger.info(
            "=" * 80 + "\n"
            "🔘 [BUTTON_VALIDATE] Called on %d picking(s): %s\n"
            "   Timestamp: %s",
            len(self),
            ', '.join(p.name for p in self),
            _dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        )
        for _p in self:
            _logger.info(
                "   ↳ Picking: %s | State: %s | Type: %s | From: loc%s → loc%s",
                _p.name,
                _p.state,
                _p.picking_type_id.name if _p.picking_type_id else 'N/A',
                _p.location_id.id,
                _p.location_dest_id.id,
            )
        
        # ============================================================
        # REQUIREMENT: For Dispatch pickings, all items must have
        # completed the full flow before dispatch
        # ============================================================
        DISPATCH_READY_STATUSES = {
            'payment_pending', 'payment_completed', 'dispatched',
            're_dispatched', 'return_of_order', 'delivered', 'order_completed',
        }
        for picking in self:
            if picking.location_dest_id.id == 5:
                sale_order = picking.sale_id
                if not sale_order and picking.origin:
                    sale_order = self.env['sale.order'].search(
                        [('name', '=', picking.origin)], limit=1
                    )
                if sale_order:
                    not_ready = []
                    for move in picking.move_ids_without_package.filtered(
                        lambda m: m.state not in ('done', 'cancel')
                    ):
                        so_line = sale_order.order_line.filtered(
                            lambda l: l.product_id.id == move.product_id.id
                        )[:1]
                        if so_line and so_line.availability_status not in DISPATCH_READY_STATUSES:
                            not_ready.append(
                                "%s — Status: %s" % (
                                    move.product_id.display_name,
                                    so_line.availability_status or 'Unknown',
                                )
                            )
                    if not_ready:
                        raise UserError(_(
                            "The following product(s) are not allowed to dispatch "
                            "as they have not completed the required flow:\n\n"
                            "• %s\n\n"
                            "All products must complete RFQ → Logistics → Quality → "
                            "Payment before dispatch."
                        ) % "\n• ".join(not_ready))

        # ============================================================
        # REQUIREMENT: For Dispatch pickings, Pack must be done first
        # ============================================================
        for picking in self:
            # Check if this is a dispatch picking (location_dest_id = 5)
            if picking.location_dest_id.id == 5 and picking.state == 'assigned':
                if not picking.is_packed:
                    raise UserError(_(
                        "❌ Cannot Validate without Packing!\n\n"
                        "Please click the 'Pack' button first and enter:\n"
                        "• UTR Number\n"
                        "• Confirm payment completion\n\n"
                        "Then you can validate the dispatch."
                    ))
        

        pickings_to_reset = self.filtered(lambda p: p.state == 'pack')
        if pickings_to_reset:
            for picking in pickings_to_reset:
                _logger.info(
                    "🔄 Reverting picking %s from 'pack' to 'assigned' before validate",
                    picking.name
                )
            # Write directly via SQL to avoid triggering write() overrides
            self.env.cr.execute(
                "UPDATE stock_picking SET state = 'assigned' WHERE id = ANY(%s)",
                [pickings_to_reset.ids]
            )
            # Invalidate cache so ORM sees the updated state
            pickings_to_reset.invalidate_recordset(['state'])

        # When the user clicks Validate on a QC picking that has fail moves,
        # auto-validate was suppressed (see action_fail/action_pass).  Now that
        # the user has manually clicked Validate, we convert every qc_fail line
        # in the related sale order to 'cancelled' and push the status to the
        # website before the standard picking validation proceeds.
        for picking in self:
            if picking.location_id.id == 18 and picking.location_dest_id.id == 10:
                fail_moves = picking.move_ids_without_package.filtered(
                    lambda m: m.qc_status == 'fail' and m.state not in ('done', 'cancel')
                )
                if fail_moves:
                    so = picking.sale_id
                    if not so and picking.origin:
                        po = self.env['purchase.order'].search(
                            [('name', '=', picking.origin)], limit=1
                        )
                        if po and po.origin:
                            so = self._find_sale_order_from_origin(po.origin)
                        if not so:
                            so = self.env['sale.order'].search(
                                [('name', '=', picking.origin)], limit=1
                            )
                    if so:
                        qc_fail_so_lines = so.order_line.filtered(
                            lambda l: l.availability_status == 'qc_fail'
                        )
                        if qc_fail_so_lines:

                            so.env.cr.execute(
                                "SELECT sdk_augmont_status FROM sale_order WHERE id = %s",
                                (so.id,)
                            )
                            _row = so.env.cr.fetchone()
                            _pre_cancel_status = _row[0] if _row else False

                            qc_fail_so_lines.with_context(
                                from_quality_module=True,
                                skip_api_sync=True,
                            ).write({'availability_status': 'cancelled'})
                            _logger.info(
                                "❌→🚫 [QC→CANCEL] %d qc_fail line(s) → 'cancelled' for order %s "
                                "(manual Validate clicked)",
                                len(qc_fail_so_lines), so.name
                            )
                            # Recompute order status
                            so.invalidate_recordset(['sdk_augmont_status'])
                            so._compute_order_status_from_lines()
                            _post_cancel_status = so.sdk_augmont_status
                            _logger.info(
                                "📊 [QC→CANCEL] Order %s: %s → %s",
                                so.name, _pre_cancel_status, _post_cancel_status
                            )
                            # Directly call the website API — bypasses _db_old_status
                            # comparison that would otherwise suppress the call when
                            # the auto-triggered compute already wrote the new value.
                            if _post_cancel_status and _post_cancel_status != _pre_cancel_status:
                                try:
                                    so._call_augmont_status_api(
                                        _post_cancel_status, _pre_cancel_status
                                    )
                                    _logger.info(
                                        "✅ [QC→CANCEL API] Synced | Order: %s | "
                                        "'%s' sent to website",
                                        so.name, _post_cancel_status
                                    )
                                except Exception as _api_err:
                                    _logger.error(
                                        "❌ [QC→CANCEL API] Failed | Order: %s | Error: %s",
                                        so.name, _api_err
                                    )

                            # ── Auto-cancel the Odoo order when QC fail drives all lines to
                            # 'cancelled' (sdk_augmont_status becomes 'Cancelled').
                            # This moves the stage bar to the CANCELLED stage automatically,
                            # so the admin does not need to manually click {Cancel}.
                            # Guard: only act when status is 'Cancelled' and order is still open.
                            if (_post_cancel_status == 'Cancelled'
                                    and so.state not in ('cancel', 'done')):
                                try:
                                    _logger.info(
                                        "🔄 [QC→CANCEL AUTO] Order %s: all lines cancelled via "
                                        "QC fail → auto-advancing stage bar to Cancelled.",
                                        so.name
                                    )
                                    so.sudo().with_context(from_website_api=False).action_cancel()
                                    _logger.info(
                                        "✅ [QC→CANCEL AUTO] Order %s: stage bar moved to "
                                        "Cancelled (state → %s).",
                                        so.name, so.state
                                    )
                                except Exception as _cancel_err:
                                    _logger.error(
                                        "❌ [QC→CANCEL AUTO] Failed to auto-cancel order %s: %s",
                                        so.name, _cancel_err
                                    )

        # fail product move to vendor location

        vendor_location_id = 4  # Partners/Vendors location

        for picking in self:
            if picking.location_id.id == 18 and picking.location_dest_id.id == 10:
                moves = picking.move_ids_without_package
                no_qc_moves = moves.filtered(lambda m: not m.qc_status)
                if no_qc_moves:
                    raise UserError(_("Please complete the QC check for all products before proceeding."))

                failed_moves = moves.filtered(lambda m: m.qc_status == 'fail')
                if failed_moves:
                    failed_moves.write({'location_dest_id': vendor_location_id})
                    if all(m.location_dest_id.id == vendor_location_id for m in moves):
                        picking.write({'location_dest_id': vendor_location_id})

                    # Post to chatter log only (no email)
                    failed_product_names = ", ".join(failed_moves.mapped("product_id.display_name"))
                    subject = _("QC Failed - Products Sent Back to Vendor")
                    body_html = Markup(_("""
                        <p>Dear Logistics Team,</p>
                        <p>In QC process, the following product(s) have failed quality check and have been sent back to vendor:</p>
                        <p><strong>QC Record:</strong> %s</p>
                        <p><strong>Failed Products:</strong> %s</p>
                        <p><strong>Vendor:</strong> %s</p>
                        <p>Please arrange return of these products to the vendor.</p>
                    """)) % (picking.name, failed_product_names, picking.partner_id.name or "N/A")

                    # Post to QC receipt chatter
                    picking.message_post(
                        subject=subject,
                        body=body_html,
                        message_type='notification'
                    )

                    # Find and post to Logistics receipt chatter (parent receipt)
                    logistics_picking = self.env['stock.picking'].search([
                        ('origin', '=', picking.origin),
                        ('location_id', '=', 4),
                        ('location_dest_id', '=', 18),
                        ('state', '=', 'done')
                    ], limit=1)

                    if logistics_picking:
                        logistics_picking.message_post(
                            subject=subject,
                            body=body_html,
                            message_type='notification'
                        )


        CUSTOM_WORKFLOW_LOCATIONS = {
            # (from_loc, to_loc_or_None)  — None means "any destination"
        }
        for picking in self:
            src = picking.location_id.id
            dst = picking.location_dest_id.id

            is_custom_picking = (
                (src == 18 and dst == 10) or   # QC → LGD Inventory
                (src == 10 and dst in [20, 21]) or  # LGD Inventory → Dispatch staging
                (src in [20, 21] and dst == 5)      # Dispatch staging → Customer
            )
            if is_custom_picking:
                _logger.info(
                    "📦 [PRE-VALIDATE] Ensuring move quantities for picking %s (loc %s → %s)",
                    picking.name, src, dst
                )
                for move in picking.move_ids_without_package:
                    if move.state in ('done', 'cancel'):
                        continue

                    if move.quantity != move.product_uom_qty:
                        _logger.info(
                            "   ↳ Move '%s': qty %s → %s (forced to demand)",
                            move.product_id.name, move.quantity, move.product_uom_qty
                        )
                        move.quantity = move.product_uom_qty


        AUGMONT_LOCATIONS = {4, 5, 10, 18, 20, 21}

        is_augmont_picking = any(
            p.location_id.id in AUGMONT_LOCATIONS or p.location_dest_id.id in AUGMONT_LOCATIONS
            for p in self
        )

        # Extra safety: only apply if the picking has a sale order with in-flight lines
        # (to avoid disrupting unrelated pickings that happen to touch those locations)
        IN_FLIGHT_STATUSES = {
            'in_qc_process', 'qc_fail', 'payment_pending', 'payment_completed',
            'dispatched', 're_dispatched', 'return_of_order', 'delivered', 'order_completed',
        }

        has_in_flight_sale_lines = False
        if is_augmont_picking:
            for p in self:
                so = p.sale_id
                if not so and p.origin:
                    so = self.env['sale.order'].search([('name', '=', p.origin)], limit=1)
                if so:
                    in_flight = so.order_line.filtered(
                        lambda l: l.availability_status in IN_FLIGHT_STATUSES
                    )
                    if in_flight:
                        has_in_flight_sale_lines = True
                        break

        use_dispatch_context = is_augmont_picking and has_in_flight_sale_lines

        if use_dispatch_context:
            _logger.info(
                "Augmont internal picking with in-flight sale lines detected "
                "(%s) — calling super().button_validate() with dispatch_validation=True "
                "to prevent duplicate record creation",
                ', '.join(self.mapped('name'))
            )
            res = super(StockPicking, self.with_context(
                dispatch_validation=True,
                from_pack_wizard=True,
                no_recompute=True,
            )).button_validate()
        else:
            res = super(StockPicking, self).button_validate()

        for picking in self:
            if picking.state != 'done':
                continue

            moves = picking.move_ids_without_package

            sale_order = picking.sale_id
            if not sale_order and picking.origin:
                purchase_order = self.env['purchase.order'].search([('name', '=', picking.origin)], limit=1)
                if purchase_order and purchase_order.origin:
                    sale_order = self._find_sale_order_from_origin(purchase_order.origin)
                if not sale_order:
                    sale_order = self.env['sale.order'].search([('name', '=', picking.origin)], limit=1)


            if picking.location_id.id == LOC_VENDOR and picking.location_dest_id.id == LOC_QC:

                # ── Build a map: {sale_order_id: (sale_order, [moves])} ──────────────
                # For each move, find which Sale Order it belongs to via purchase_line_id.
                # A consolidated PO covers multiple SOs — we split them here.
                move_so_map = {}  # {so_id: (so_record, [move_records])}

                for move in moves:
                    so_for_move = None

                    # Priority 1: direct sale_id on the picking
                    if picking.sale_id:
                        so_for_move = picking.sale_id

                    # Priority 2: via purchase_line → direct sale_order_id link
                    if not so_for_move and move.purchase_line_id:
                        # Direct link: purchase.order.line has sale_order_id
                        if move.purchase_line_id.sale_order_id:
                            so_for_move = move.purchase_line_id.sale_order_id
                        # Fallback: match product against each source document
                        elif move.purchase_line_id.order_id and move.purchase_line_id.order_id.origin:
                            po = move.purchase_line_id.order_id
                            origin_names = [n.strip() for n in po.origin.split(',') if n.strip()]
                            matching_sos = self.env['sale.order'].search([('name', 'in', origin_names)])
                            for so_candidate in matching_sos:
                                if so_candidate.order_line.filtered(
                                    lambda l: l.product_id.id == move.product_id.id
                                ):
                                    so_for_move = so_candidate
                                    break
                            if not so_for_move and matching_sos:
                                so_for_move = matching_sos[0]

                    # Priority 3: picking.origin as SO name directly
                    if not so_for_move and picking.origin:
                        so_for_move = self.env['sale.order'].search(
                            [('name', '=', picking.origin)], limit=1
                        )

                    # Fallback: the sale_order resolved at the top of button_validate
                    if not so_for_move:
                        so_for_move = sale_order

                    key = so_for_move.id if so_for_move else -1
                    if key not in move_so_map:
                        move_so_map[key] = (so_for_move, [])
                    move_so_map[key][1].append(move)

                _logger.info(
                    "📋 Logistics picking %s → %d unique Sale Order group(s): %s",
                    picking.name,
                    len(move_so_map),
                    {(so.name if so else 'UNKNOWN'): len(mvs) for _, (so, mvs) in move_so_map.items()}
                )

                # ── Process each SO group independently ─────────────────────────────
                for _so_id, (group_so, group_moves) in move_so_map.items():
                    if group_so:

                        moved_product_ids = {m.product_id.id for m in group_moves}
                        active_lines = group_so.order_line.filtered(
                            lambda l: (
                                l.availability_status not in [
                                    'cancelled', 'not_available', 'qc_fail',
                                    'payment_pending', 'payment_completed',
                                    'dispatched', 'return_of_order', 're_dispatched',
                                    'delivered', 'order_completed', 'in_qc_process',
                                ]
                                and l.product_id.id in moved_product_ids
                            )
                        )
                        if active_lines:
                            active_lines.with_context(from_quality_module=True).write({
                                'availability_status': 'in_qc_process'
                            })
                            _logger.info(
                                "📋 Updated %d lines to 'in_qc_process' for order %s "
                                "(product-filtered: %s)",
                                len(active_lines), group_so.name,
                                [l.order_number for l in active_lines],
                            )

                    # ── Filter moves: only include products whose SO line is in_qc_process ──
                    if group_so:
                        qc_eligible_moves = [
                            m for m in group_moves
                            if group_so.order_line.filtered(
                                lambda l: l.product_id.id == m.product_id.id
                                and l.availability_status == 'in_qc_process'
                            )
                        ]
                    else:
                        qc_eligible_moves = group_moves

                    if not qc_eligible_moves:
                        _logger.info(
                            "📋 No QC-eligible moves for order %s — skipping QC picking creation",
                            group_so.name if group_so else '?'
                        )
                        continue

                    # ── Check for an existing QC picking for this SO ──────────────
                    new_picking = None
                    existing_qc = self.env['stock.picking'].search([
                        ('picking_type_id', '=', PT_QC),
                        '|',
                        ('origin', '=', group_so.name if group_so else picking.origin),
                        ('origin', '=', picking.origin),
                        ('state', 'in', ['draft', 'confirmed', 'assigned']),
                    ], limit=1)

                    if not existing_qc:
                        picking_type = self.env['stock.picking.type'].browse(PT_QC)
                        picking_vals = {
                            'picking_type_id': picking_type.id,
                            'partner_id': (group_so.partner_id.id if group_so
                                           else picking.partner_id.id),
                            'location_id': LOC_QC,
                            'location_dest_id': LOC_INV,
                            'origin': group_so.name if group_so else picking.origin,
                            'sale_id': group_so.id if group_so else False,
                            'scheduled_date': picking.scheduled_date,
                            'move_ids_without_package': [
                                (0, 0, {
                                    'name': move.name,
                                    'product_id': move.product_id.id,
                                    'product_uom_qty': move.product_uom_qty,
                                    'product_uom': move.product_uom.id,
                                    'location_id': LOC_QC,
                                    'location_dest_id': LOC_INV,
                                    'purchase_line_id': (move.purchase_line_id.id
                                                         if move.purchase_line_id else False),
                                }) for move in qc_eligible_moves
                            ],
                        }
                        new_picking = self.env['stock.picking'].create(picking_vals)
                        new_picking.action_confirm()
                        new_picking.action_assign()

                        # notify QC group
                        self._send_notification_email(
                            new_picking,
                            "__export__.res_groups_78_640c10c2",
                            _("New Transfer %s created — follow up on material inward quality process"),
                            _("""<p>Dear Quality Team,</p><p>The Transfer <strong>%s</strong> has been created for QC.</p>""")
                        )
                        _logger.info(
                            "📋 ✅ Created QC picking %s for SO %s with %d move(s)",
                            new_picking.name,
                            group_so.name if group_so else '?',
                            len(group_moves)
                        )
                    else:
                        _logger.info(
                            "📋 Existing QC picking %s found for order %s — "
                            "adding moves from logistics picking %s into it.",
                            existing_qc.name,
                            group_so.name if group_so else '?',
                            picking.name,
                        )
                        moves_added = 0
                        for move in qc_eligible_moves:
                            already_in_qc = existing_qc.move_ids_without_package.filtered(
                                lambda m: m.product_id.id == move.product_id.id
                            )
                            if not already_in_qc:
                                self.env['stock.move'].create({
                                    'name': move.name,
                                    'product_id': move.product_id.id,
                                    'product_uom_qty': move.product_uom_qty,
                                    'product_uom': move.product_uom.id,
                                    'picking_id': existing_qc.id,
                                    'location_id': LOC_QC,
                                    'location_dest_id': LOC_INV,
                                    'purchase_line_id': (move.purchase_line_id.id
                                                         if move.purchase_line_id else False),
                                })
                                moves_added += 1
                            else:
                                _logger.info(
                                    "📋 Product %s already in QC picking %s — skipping.",
                                    move.product_id.name, existing_qc.name,
                                )
                        if moves_added:
                            try:
                                existing_qc.action_confirm()
                                existing_qc.action_assign()
                            except Exception as ex:
                                _logger.warning(
                                    "📋 Could not re-confirm QC picking %s: %s",
                                    existing_qc.name, ex,
                                )
                            _logger.info(
                                "📋 ✅ Added %d move(s) to existing QC picking %s",
                                moves_added, existing_qc.name,
                            )
                        new_picking = existing_qc

                    # Merge any stray duplicate QC pickings for this SO
                    if group_so:
                        self._merge_qc_pickings_for_sale(group_so, preferred_master=new_picking)

                    if group_so:
                        _logger.info(
                            "📋 [COMPLETE] QC picking %s | Order: %s | "
                            "Status: %s",
                            new_picking.name if new_picking else '(reused)',
                            group_so.name,
                            group_so.sdk_augmont_status,
                        )
                        if new_picking:
                            group_so.message_post(
                                body=Markup(
                                    "QC Transfer <b>%s</b> created. "
                                    "Lines moved to 'In QC process'."
                                ) % new_picking.name,
                                subject="Quality Check Started"
                            )

                        # ── Push "In QC process" to the connected website ─────────
                        # SaleOrderLine.write() always runs _compute with skip_api_sync=True
                        # so the compute path never fires the website API for this transition.
                        # We must call it explicitly here, AFTER the chatter post, so the
                        # website order-level status reflects the QC start immediately.
                        try:
                            group_so._call_augmont_status_api('In QC process')
                            _logger.info(
                                "✅ [API] 'In QC process' pushed to website | Order: %s",
                                group_so.name
                            )
                        except Exception as _api_err:
                            _logger.error(
                                "❌ [API] Failed to push 'In QC process' for %s: %s",
                                group_so.name, _api_err
                            )

            # QC -> Inventory (18→10): QC validation, API sync, LGD picking creation
            if picking.location_id.id == 18 and picking.location_dest_id.id == 10:
                passed_moves = moves.filtered(lambda m: m.qc_status == 'pass')
                failed_moves = moves.filtered(lambda m: m.qc_status == 'fail')

                # ── Fallback SO lookup ──────────────────────────────────
                # When a QC picking was created from a vendor PO (sale_id not set),
                # we must still find the sale order to correctly create the LGD
                # picking and set the procurement_reference (invoice number).
                if not sale_order and passed_moves:
                    # Try via purchase_line_id → direct sale_order_id or PO.origin
                    for mv in passed_moves:
                        if mv.purchase_line_id:
                            if mv.purchase_line_id.sale_order_id:
                                sale_order = mv.purchase_line_id.sale_order_id
                                _logger.info(
                                    "✅ [FALLBACK] SO found via purchase_line.sale_order_id | SO: %s",
                                    sale_order.name
                                )
                                break
                            elif mv.purchase_line_id.order_id.origin:
                                so = self._find_sale_order_from_origin(
                                    mv.purchase_line_id.order_id.origin, mv.product_id
                                )
                                if so:
                                    sale_order = so
                                    _logger.info(
                                        "✅ [FALLBACK] SO found via purchase_line origin | PO: %s | SO: %s",
                                        mv.purchase_line_id.order_id.name, sale_order.name
                                    )
                                    break

                    if not sale_order:
                        # Build extra domain constraint if QC picking origin = SO name
                        origin_domain = []
                        if picking.origin:
                            so_by_origin = self.env['sale.order'].search(
                                [('name', '=', picking.origin)], limit=1)
                            if so_by_origin:
                                origin_domain = [('order_id', '=', so_by_origin.id)]
                        for mv in passed_moves:
                            domain = [
                                ('product_id', '=', mv.product_id.id),
                                ('availability_status', 'in',
                                 ['in_qc_process', 'payment_pending']),
                            ] + origin_domain
                            so_line = self.env['sale.order.line'].search(domain, limit=1)
                            if so_line and so_line.order_id:
                                sale_order = so_line.order_id
                                _logger.info(
                                    "✅ [FALLBACK] SO found via product match | Product: %s | SO: %s",
                                    mv.product_id.display_name, sale_order.name
                                )
                                break
                    # Also set sale_id on the picking so downstream works
                    if sale_order:
                        picking.sudo().write({'sale_id': sale_order.id})

                _logger.info(
                    "📋 QC Result: Order %s | Passed: %d | Failed: %d | Status handled by compute method",
                    sale_order.name if sale_order else 'N/A',
                    len(passed_moves), len(failed_moves)
                )

                if passed_moves:
                    resolved_location = picking.location
                    if not resolved_location and sale_order and sale_order.location:
                        resolved_location = sale_order.location
                    if not resolved_location and picking.origin:
                        po = self.env['purchase.order'].search([('name', '=', picking.origin)], limit=1)
                        if po and po.location:
                            resolved_location = po.location
                    if not resolved_location:
                        resolved_location = 'mumbai'
                        _logger.warning(
                            "⚠️ [LGD] Location not resolved anywhere for %s — defaulting to Mumbai",
                            picking.name
                        )

                    _logger.info(
                        "📦 [LGD] Location resolve | QC picking: %s | picking.location=%s | resolved=%s",
                        picking.name, picking.location or 'EMPTY', resolved_location
                    )

                    if resolved_location == 'mumbai':
                        dest_loc_id = 20
                        picking_type_id = 12
                    elif resolved_location == 'surat':
                        dest_loc_id = 21
                        picking_type_id = 13
                    else:
                        dest_loc_id = 20
                        picking_type_id = 12

                    # From Quality onwards, records must be grouped by Invoice
                    # Number (sdk_augmont_number), not by vendor/SO name.
                    # Multiple vendor QC pickings for the same invoice must all
                    # go into ONE LGD Inventory picking identified by invoice.
                    invoice_number = sale_order.sdk_augmont_number if sale_order else None

                    if invoice_number:
                        # Search for an existing LGD picking for this invoice
                        existing_inv = self.env['stock.picking'].search([
                            ('location_id', '=', 10),
                            ('location_dest_id', '=', dest_loc_id),
                            ('procurement_reference', '=', invoice_number),
                            ('state', 'in', ['draft', 'confirmed', 'assigned']), 
                        ], limit=1)
                    else:
                        # Fallback: search by SO name (original behaviour)
                        existing_inv = self.env['stock.picking'].search([
                            ('location_id', '=', 10),
                            ('location_dest_id', '=', dest_loc_id),
                            ('origin', '=', sale_order.name if sale_order else False),
                            ('state', 'in', ['draft', 'confirmed', 'assigned']), 
                        ], limit=1)

                    if existing_inv:
                        # ── Add passed moves into the existing invoice LGD picking ──
                        _logger.info(
                            "📦 [LGD] Adding moves to existing invoice picking | Ref: %s | Invoice: %s",
                            existing_inv.name, invoice_number
                        )
                        for mv in passed_moves:
                            self.env['stock.move'].create({
                                'name': mv.name,
                                'product_id': mv.product_id.id,
                                'product_uom_qty': mv.product_uom_qty,
                                'product_uom': mv.product_uom.id,
                                'location_id': 10,
                                'location_dest_id': dest_loc_id,
                                'picking_id': existing_inv.id,
                                'purchase_line_id': mv.purchase_line_id.id if mv.purchase_line_id else False,
                            })
                        existing_inv.action_confirm()
                        existing_inv._force_picking_ready()
                        new_picking = existing_inv
                    else:
                        ptype = self.env['stock.picking.type'].browse(picking_type_id)
                        picking_vals = {
                            'picking_type_id': ptype.id,
                            'partner_id': sale_order.partner_id.id if sale_order else picking.partner_id.id,
                            'location_id': 10,
                            'location_dest_id': dest_loc_id,
                            'origin': sale_order.name if sale_order else False,
                            'sale_id': sale_order.id if sale_order else False,
                            'scheduled_date': picking.scheduled_date,
                            'location': resolved_location,
                            'procurement_reference': invoice_number or (sale_order.name if sale_order else False),
                            'move_ids_without_package': [
                                (0, 0, {
                                    'name': move.name,
                                    'product_id': move.product_id.id,
                                    'product_uom_qty': move.product_uom_qty,
                                    'product_uom': move.product_uom.id,
                                    'location_id': 10,
                                    'location_dest_id': dest_loc_id,
                                    'purchase_line_id': move.purchase_line_id.id if move.purchase_line_id else False,
                                }) for move in passed_moves
                            ]
                        }
                        new_picking = self.env['stock.picking'].with_context(
                            no_recompute=True,
                            from_pack_wizard=True,
                        ).create(picking_vals)
                        
                        _logger.info(
                            "📦 [LGD] Picking created | Ref: %s | Type: %s | Origin: %s | ProcRef: %s",
                            new_picking.name, picking_type_id,
                            sale_order.name if sale_order else 'N/A',
                            invoice_number or (sale_order.name if sale_order else 'N/A')
                        )
                        
                        new_picking.with_context(
                            dispatch_validation=True,
                            no_recompute=True,
                            from_pack_wizard=True,
                        ).action_confirm()
                        
                        # Ensure Validate button is visible (creates move_lines,
                        # forces picking+move state to 'assigned' via SQL).
                        new_picking._force_picking_ready()
                        
                        _logger.info(
                            "✅ [LGD] Ready for validation | Ref: %s | State: %s | Route: loc %s → %s",
                            new_picking.name, new_picking.state, 10, dest_loc_id
                        )
                        
                        self._send_notification_email(
                            new_picking,
                            "__export__.res_groups_84_c1e3c450",
                            _("New Transfer %s created — received from QC (passed items)"),
                            _("""<p>Dear Inventory Team,</p><p>The New Transfer <strong>%s</strong> has been created for passed QC items.</p>""")
                        )

            if picking.location_id.id == 10 and picking.location_dest_id.id in [20, 21]:
                _logger.info(
                    "📦 [LGD VALIDATION] Detected | Picking: %s | From loc 10 → To loc %s",
                    picking.name, picking.location_dest_id.id
                )
                
                # Find the sale order
                sale_order = picking.sale_id
                if not sale_order and picking.origin:
                    # Try via PO
                    po = self.env['purchase.order'].search([('name', '=', picking.origin)], limit=1)
                    if po and po.origin:
                        sale_order = self._find_sale_order_from_origin(po.origin)
                    # Try direct
                    if not sale_order:
                        sale_order = self.env['sale.order'].search([('name', '=', picking.origin)], limit=1)
                
                if sale_order:
                    _logger.info("📦 [LGD] Found Sale Order: %s", sale_order.name)
                    
                    # Get all moves
                    all_moves = picking.move_ids_without_package
                    eligible_moves = self.env['stock.move']
                    
                    # Filter moves based on sale order line availability
                    for move in all_moves:
                        # Match move to sale order line
                        so_line = sale_order.order_line.filtered(
                            lambda l: l.lgd_stock_number == move.lgd_stock_number
                        )[:1] if move.lgd_stock_number else None
                        
                        if not so_line:
                            so_line = sale_order.order_line.filtered(
                                lambda l: l.product_id.id == move.product_id.id
                            )[:1]
                        
                        if so_line:
                            _logger.info(
                                "🔍 [LGD] Checking Move: %s | Line Availability: %s",
                                move.product_id.name, so_line.availability_status
                            )
                            
                            # Only include if payment_pending
                            if so_line.availability_status == 'payment_pending':
                                eligible_moves |= move
                                _logger.info("✅ [LGD] ELIGIBLE for Dispatch: %s", move.product_id.name)
                            else:
                                _logger.info(
                                    "🚫 [LGD] FILTERED OUT: %s (Status: %s - NOT payment_pending)",
                                    move.product_id.name, so_line.availability_status
                                )
                        else:
                            _logger.warning("⚠️ [LGD] No matching line for move: %s", move.product_id.name)
                    
                    _logger.info(
                        " [LGD] Filter Results: %d eligible / %d total moves",
                        len(eligible_moves), len(all_moves)
                    )
                    
                    # Dispatch creation is handled by standard Odoo delivery
                    # (created at SO confirmation). The button_validate guard
                    # ensures only payment_pending+ items can be dispatched.
                    _logger.info(
                        "📦 [LGD] Skipping custom dispatch creation — "
                        "using standard Odoo delivery. Eligible: %d / Total: %d",
                        len(eligible_moves), len(all_moves)
                    )
                else:
                    _logger.warning("⚠️ [LGD] No sale order found for picking: %s", picking.name)


            if picking.location_id.id in [20, 21] and picking.location_dest_id.id == 5:
                _logger.info(
                    "🚚 Dispatch validated | Picking: %s | Finding sale order...",
                    picking.name
                )

                so = picking.sale_id
                if not so and picking.origin:
                    po = self.env['purchase.order'].search(
                        [('name', '=', picking.origin)], limit=1
                    )
                    if po and po.origin:
                        so = self._find_sale_order_from_origin(po.origin)
                    if not so:
                        so = self.env['sale.order'].search(
                            [('name', '=', picking.origin)], limit=1
                        )

                if not so:
                    _logger.warning(
                        "⚠️ No sale order found for Dispatch picking %s",
                        picking.name
                    )
                    continue

                _logger.info(
                    "🚚 Sale Order resolved: %s", so.name
                )


                payment_completed_lines = so.order_line.filtered(
                    lambda l: l.availability_status == 'payment_completed'
                )
                if payment_completed_lines:
                    _logger.info(
                        "🔄 Updating %d lines: payment_completed → dispatched",
                        len(payment_completed_lines)
                    )
                    payment_completed_lines.with_context(from_pack_wizard=True).write({
                        'availability_status': 'dispatched'
                    })

                # Pull tracking details from picking → sale order
                tracking_vals = {}
                if picking.tracker_name:
                    tracking_vals['courier_partner_name'] = picking.tracker_name
                if picking.tracking_number:
                    tracking_vals['tracking_number'] = picking.tracking_number
                if picking.tracking_url:
                    tracking_vals['tracking_url'] = picking.tracking_url
                if tracking_vals:
                    so.with_context(from_website_api=True).write(tracking_vals)

                # Mark picking as dispatched
                picking.is_dispatched = True


                new_status = so.sdk_augmont_status
                _logger.info(
                    "✅ Dispatch complete | Order: %s | Final Status: %s",
                    so.name, new_status
                )

                # ── Push Dispatched / Re-Dispatched to the connected website ──
                # The SaleOrderLine.write() at the top of this block ALWAYS runs
                # _compute with skip_api_sync=True, so the compute path never
                # fires the website API.  _compute_order_status_from_lines() also
                # skips it because SaleOrderLine.write() already SQL-updated
                # sdk_augmont_status (_db_old_status == new_status → False).
                try:
                    so._call_augmont_status_api(new_status)
                    _logger.info(
                        "✅ [DISPATCH→WEBSITE] '%s' pushed to website | Order: %s",
                        new_status, so.name
                    )
                except Exception as _api_err:
                    _logger.error(
                        "❌ [DISPATCH→WEBSITE] Failed to push '%s' for %s: %s",
                        new_status, so.name, _api_err
                    )

                so.message_post(
                    body=_(
                        "Order dispatched from picking <b>%s</b>.<br/>"
                        "Status updated to: <b>%s</b>"
                    ) % (picking.name, new_status),
                    subject="Order Dispatched"
                )


        for picking in self:
            if picking.state == 'done' and picking.location_dest_id.id == 5:
                sale_order = picking.sale_id
                if not sale_order and picking.origin:
                    po = self.env['purchase.order'].search(
                        [('name', '=', picking.origin)], limit=1
                    )
                    if po and po.origin:
                        sale_order = self._find_sale_order_from_origin(po.origin)
                    if not sale_order:
                        sale_order = self.env['sale.order'].search(
                            [('name', '=', picking.origin)], limit=1
                        )

                if sale_order:
                    spurious = sale_order.order_line.filtered(
                        lambda l: l.availability_status == 'diamond_booked'
                                  and not l.order_number
                    )
                    if spurious:
                        _logger.warning(
                            "🧹 [POST-DISPATCH] Neutralising %d spurious 'diamond_booked' lines "
                            "from order %s: %s",
                            len(spurious),
                            sale_order.name,
                            spurious.mapped('product_id.name')
                        )
                        moves_to_cancel = self.env['stock.move'].search([
                            ('sale_line_id', 'in', spurious.ids),
                            ('state', 'not in', ['done', 'cancel']),
                        ])
                        if moves_to_cancel:
                            moves_to_cancel._action_cancel()
                            _logger.info(
                                "   ↳ Cancelled %d stock moves for spurious lines",
                                len(moves_to_cancel)
                            )

                        spurious.with_context(
                            from_quality_module=True,
                            from_pack_wizard=True,
                            no_recompute=True,
                        ).write({
                            'product_uom_qty': 0,
                            'availability_status': 'cancelled',
                        })
                        _logger.info(
                            "✅ [POST-DISPATCH] Neutralised %d spurious lines "
                            "(qty=0, status=cancelled) on order %s",
                            len(spurious), sale_order.name
                        )

                    sale_order.invalidate_recordset(['sdk_augmont_status'])
                    sale_order._compute_order_status_from_lines()
                    _logger.info(
                        "📊 [POST-DISPATCH] Order %s → status: %s",
                        sale_order.name, sale_order.sdk_augmont_status
                    )

        return res


    def _get_next_transfers(self):
        self.ensure_one()
        
        transfers = self.env['stock.picking'].search([
            ('id', '!=', self.id),
            ('origin', '=', self.origin),
            ('location_id', '=', self.location_dest_id.id),
        ])
        return transfers

    
    def action_print_product_labels(self):
        """Directly print labels without showing wizard"""
        self.ensure_one()

        products = self.move_ids_without_package.mapped('product_id')
        if not products:
            raise UserError(_("No products found in this picking."))

        wizard = self.env['product.label.layout'].create({
            'product_ids': [(6, 0, products.ids)],   
            'custom_quantity': 1,                    
            'print_format': 'dymo',                  
        })

        return wizard.process()

    def print_report_1(self):
        return self.env.ref('contact_stage_bar.action_material_tag').report_action(self)


    def action_create_second_delivery(self):
        for picking in self:
            _logger.info("Checking picking: %s (Location: %s -> %s)", picking.name, picking.location_id.id, picking.location_dest_id.id)

            if picking.location_id.id == 8 and picking.location_dest_id.id == 17:
                _logger.info("Condition matched for picking: %s", picking.name)

                sales = self.env['sale.order'].search([('name', '=', picking.origin)], limit=1)
                if sales:
                    _logger.info("Found Sale Order: %s with Partner: %s", sales.name, sales.partner_id.name)
                    if sales.partner_id:
                        picking.partner_id = sales.partner_id
                else:
                    _logger.warning("No Sale Order found with origin: %s", picking.origin)

                picking_type = self.env['stock.picking.type'].browse(2)  
                if not picking_type.exists():
                    _logger.error("Picking Type with ID 2 not found!")
                    raise UserError("Picking Type with ID 2 not found!")

                new_picking_vals = {
                    'picking_type_id': picking_type.id,
                    'partner_id': picking.partner_id.id,
                    'location_id': 17,
                    'location_dest_id': 9,
                    'origin': picking.origin,
                    'scheduled_date': picking.scheduled_date,
                    'move_type':'direct',
                    'sale_id': sales.id,
                    'move_ids_without_package': [],
                }

                _logger.info("Preparing move lines for new picking...")
                for move in picking.move_ids_without_package:
                    _logger.debug("Adding move: %s (Product: %s, Qty: %s)", move.name, move.product_id.name, move.product_uom_qty)

                    move_vals = {
                        'name': move.name,
                        'product_id': move.product_id.id,
                        'product_uom_qty': move.product_uom_qty,
                        'product_uom': move.product_uom.id,
                        'location_id': 17,
                        'location_dest_id': 9,
                        'sale_line_id': move.sale_line_id.id,
                    }
                    new_picking_vals['move_ids_without_package'].append((0, 0, move_vals))

                if picking_type.sequence_id:
                    sequence_name = picking_type.sequence_id.next_by_id()
                    new_picking_vals['name'] = sequence_name
                    _logger.info("Generated sequence name for new picking: %s", sequence_name)

                _logger.info("Creating new delivery with values: %s", new_picking_vals)
                new_delivery = self.env['stock.picking'].create(new_picking_vals)
                subject = _("New Transfer %s created  follow up on material outward quality process") % new_delivery.name
                body_html = Markup("""
                <p>Dear Logistics Team,</p>
                <p>The New Transfer <strong>%s</strong> has been created.</p>
                <p>Please follow up on the material outward quality process.</p>
            """ % (new_delivery.name))
            
                mail_values = {
                    'subject': subject,
                    'body_html': body_html,
                    #'email_from': 'dayanujam@gmail.com',
                    #'email_to': 'suresh@sdkinfinity.com',
                    # 'email_to': ','.join(recipients),
                    # 'author_id': self.env.user.partner_id.id,
                }
                # print(mail_values,"mail_values")
                self.env['mail.mail'].create(mail_values).send()
                new_delivery.message_post(
                    subject=subject,
                    body=body_html,
                    message_type='notification' )

                _logger.info("Confirming and assigning new delivery: %s", new_delivery.name)
                new_delivery.action_confirm()
                new_delivery.action_assign()
                
            elif picking.location_id.id == 17 and picking.location_dest_id.id == 9:
                _logger.info("Condition matched for picking: %s", picking.name)

                sales = self.env['sale.order'].search([('name', '=', picking.origin)], limit=1)
                if sales:
                    _logger.info("Found Sale Order: %s with Partner: %s", sales.name, sales.partner_id.name)
                    if sales.partner_id:
                        picking.partner_id = sales.partner_id
                else:
                    _logger.warning("No Sale Order found with origin: %s", picking.origin)

                picking_type = self.env['stock.picking.type'].browse(2)  
                if not picking_type.exists():
                    _logger.error("Picking Type with ID 2 not found!")
                    raise UserError("Picking Type with ID 2 not found!")

                new_picking_vals = {
                    'picking_type_id': picking_type.id,
                    'partner_id': picking.partner_id.id,
                    'location_id': 9,
                    'location_dest_id': 5,
                    'origin': picking.origin,
                    'scheduled_date': picking.scheduled_date,
                    'move_type':'direct',
                    'sale_id': sales.id,
                    'move_ids_without_package': [],
                }

                _logger.info("Preparing move lines for new picking...")
                for move in picking.move_ids_without_package:
                    _logger.debug("Adding move: %s (Product: %s, Qty: %s)", move.name, move.product_id.name, move.product_uom_qty)

                    move_vals = {
                        'name': move.name,
                        'product_id': move.product_id.id,
                        'product_uom_qty': move.product_uom_qty,
                        'product_uom': move.product_uom.id,
                        'location_id': 9,
                        'location_dest_id': 5,
                        'sale_line_id': move.sale_line_id.id,
                    }
                    new_picking_vals['move_ids_without_package'].append((0, 0, move_vals))

                if picking_type.sequence_id:
                    sequence_name = picking_type.sequence_id.next_by_id()
                    new_picking_vals['name'] = sequence_name
                    _logger.info("Generated sequence name for new picking: %s", sequence_name)

                _logger.info("Creating new delivery with values: %s", new_picking_vals)
                new_delivery = self.env['stock.picking'].create(new_picking_vals)
                new_delivery.action_confirm()
                new_delivery.action_assign()



    def action_open_redispatch_wizard(self):
        """Re-Dispatch two-step flow:

        Step 1 (this method):
          - Find the related sale order.
          - Update all logistics-cycle lines → 'return_of_order'.
          - Set order status → 'Return of Order'.
          - Push 'Return of Order' to the website API immediately.
          - Clear tracking fields on the picking.
          - Open the dispatch-details wizard with is_redispatch=True in context.

        Step 2 (StockPickingWizard.action_apply with is_redispatch=True):
          - User enters new Delivered By / Courier Number / Tracking URL.
          - Lines are set → 're_dispatched'.
          - Order status → 'Re - Dispatched'.
          - Website API called with 'Re - Dispatched'.

        This matches the required sequence:
          Dispatched → [Re-Dispatch button] → Return of Order → [fill details] → Re - Dispatched
        """
        self.ensure_one()

        # ── 1. Resolve the related sale order ───────────────────────────────
        sale_order = self.sale_id
        if not sale_order and self.origin:
            po = self.env['purchase.order'].search([('name', '=', self.origin)], limit=1)
            if po and po.origin:
                sale_order = self._find_sale_order_from_origin(po.origin)
            if not sale_order:
                sale_order = self.env['sale.order'].search(
                    [('name', '=', self.origin)], limit=1
                )

        if sale_order:
            # ── 2. Update lines in the logistics cycle → return_of_order ────
            LOGISTICS_CYCLE = frozenset(['dispatched', 're_dispatched', 'return_of_order', 'delivered'])
            logistics_lines = sale_order.order_line.filtered(
                lambda l: l.availability_status in LOGISTICS_CYCLE
            )
            if logistics_lines:
                logistics_lines.with_context(
                    from_quality_module=True,
                    skip_api_sync=True,
                ).write({'availability_status': 'return_of_order'})
                _logger.info(
                    "🔄 [RE-DISPATCH] Step 1: %d line(s) → 'return_of_order' for order %s",
                    len(logistics_lines), sale_order.name
                )

            _logger.info(
                "📊 [RE-DISPATCH] Step 1: invoice status for order %s = %s "
                "(computed — 'return_of_order' in LOGISTICS → C2 → 'Dispatched')",
                sale_order.name, sale_order.sdk_augmont_status,
            )

            # ── 4. Push 'Return of Order' to the website API ─────────────────
            try:
                sale_order._call_augmont_status_api('Return of Order')
                _logger.info(
                    "✅ [RE-DISPATCH API] 'Return of Order' sent to website for order %s",
                    sale_order.name
                )
            except Exception as _api_err:
                _logger.error(
                    "❌ [RE-DISPATCH API] Failed | Order: %s | Error: %s",
                    sale_order.name, _api_err
                )

        # ── 5. Clear tracking fields so dispatch wizard button re-activates ──
        self.write({
            'tracker_name':    False,
            'tracking_number': False,
            'tracking_url':    False,
        })
        _logger.info(
            "🔄 [RE-DISPATCH] Cleared tracking fields on picking %s | opening wizard",
            self.name
        )

        # ── 6. Open the dispatch-details wizard (Step 2) ─────────────────────
        return {
            'type':      'ir.actions.act_window',
            'name':      'Re-Dispatch — Enter New Delivery Details',
            'res_model': 'stock.picking.wizard',
            'view_mode': 'form',
            'target':    'new',
            'context':   {
                'active_id':     self.id,
                'is_redispatch': True,
            },
        }


class StockPickingWizard(models.TransientModel):
    _name = 'stock.picking.wizard'
    _description = 'Wizard to Update Stock Picking'

    tracker_name = fields.Char(string="Delivered By")
    tracking_number = fields.Char(string="Tracking Number")
    tracking_url = fields.Char(string="Tracking Url")
    is_dispatched = fields.Boolean(string="Is Dispatched", default=False)


    def action_apply(self):
        """Store the field value into stock.picking and sale order, then update status to Dispatched"""
        picking = self.env['stock.picking'].browse(self.env.context.get('active_id'))
        if picking:
            # Update tracking information in stock.picking
            picking.write({
                'tracking_number': self.tracking_number,
                'tracking_url': self.tracking_url,
                'tracker_name': self.tracker_name,
            })
            
            # Get sale order
            sale_order = picking.sale_id
            if not sale_order and picking.origin:
                # Try to find sale order through purchase order
                purchase_order = self.env['purchase.order'].search([
                    ('name', '=', picking.origin)
                ], limit=1)
                if purchase_order and purchase_order.origin:
                    sale_order = self._find_sale_order_from_origin(purchase_order.origin)

            if sale_order:
                # First: Update tracking fields in sale order
                sale_order.write({
                    'courier_partner_name': self.tracker_name,
                    'tracking_number': self.tracking_number,
                    'tracking_url': self.tracking_url,
                })
                self.env.cr.commit()
                

                if self.tracker_name or self.tracking_number or self.tracking_url:
                    _logger.info(
                        "📦 [TRACKING ENTERED] Order: %s | Tracker: %s",
                        sale_order.name, self.tracker_name or 'N/A'
                    )
                    
                    # Find all payment_completed lines
                    payment_completed_lines = sale_order.order_line.filtered(
                        lambda l: l.availability_status == 'payment_completed'
                    )
                    
                    if payment_completed_lines:
                        _logger.info(
                            "📦 [TRACKING] Updating %d lines: payment_completed → dispatched",
                            len(payment_completed_lines)
                        )
                        
                        # Update lines
                        payment_completed_lines.write({'availability_status': 'dispatched'})
                        
                        # Force recompute
                        sale_order.invalidate_recordset(['sdk_augmont_status'])
                        sale_order._compute_order_status_from_lines()
                        
                        _logger.info(
                            "📦 [TRACKING] Order Status after tracking: %s",
                            sale_order.sdk_augmont_status
                        )

                # Second: Update sale order status to 'Dispatched'
                if picking.state == 'done':
                    _logger.info(
                        "🚚 DISPATCH confirmed | Picking %s | Sale Order %s | Current Status %s",
                        picking.name,
                        sale_order.name,
                        sale_order.sdk_augmont_status
                    )

                    # This prevents doubling of Payment Pending entries in Procurement view
                    dispatch_eligible_lines = sale_order.order_line.filtered(
                        lambda l: l.availability_status in ['payment_completed', 'dispatched']
                    )
                    if dispatch_eligible_lines:
                        dispatch_eligible_lines.write({'availability_status': 'dispatched'})
                        _logger.info(
                            "🚚 Updated %d lines to 'dispatched' for order %s",
                            len(dispatch_eligible_lines), sale_order.name
                        )
                    else:
                        _logger.warning(
                            "⚠️ No payment_completed lines found to dispatch for order %s",
                            sale_order.name
                        )
                    
                    # Detect if this is a re-dispatch (context set by
                    # action_open_redispatch_wizard) and use the correct status.
                    is_redispatch = self.env.context.get('is_redispatch', False)
                    target_status = 'Re - Dispatched' if is_redispatch else 'Dispatched'

                    # Update eligible lines to the correct logistics status
                    REDISPATCH_ELIGIBLE = ['dispatched', 're_dispatched', 'return_of_order', 'delivered']
                    redispatch_lines = sale_order.order_line.filtered(
                        lambda l: l.availability_status in REDISPATCH_ELIGIBLE
                    ) if is_redispatch else dispatch_eligible_lines

                    if is_redispatch and redispatch_lines:
                        redispatch_lines.write({'availability_status': 're_dispatched'})
                        _logger.info(
                            "🔄 [RE-DISPATCH] Updated %d line(s) to 're_dispatched' for %s",
                            len(redispatch_lines), sale_order.name
                        )

                    # Update Order Status
                    sale_order.write({'sdk_augmont_status': target_status})

                    _logger.info(
                        "🚚 Sale Order %s moved to %s",
                        sale_order.name, target_status
                    )

                    try:
                        sale_order._call_augmont_status_api(target_status)
                    except Exception as e:
                        _logger.error(
                            "Failed to sync %s for %s: %s",
                            target_status, sale_order.name, e
                        )

                    self.is_dispatched = True
                    self.env.cr.commit()
            
        return {'type': 'ir.actions.act_window_close'}


class StockReturnPicking(models.TransientModel):
    _inherit = 'stock.return.picking'

    
    @api.model
    def _prepare_stock_return_picking_line_vals_from_move(self, stock_move):
        """ Override to set default quantity = 1 instead of 0 """
        return {
            'product_id': stock_move.product_id.id,
            'quantity': 1,  # default quantity to 1
            'move_id': stock_move.id,
            'uom_id': stock_move.product_id.uom_id.id,
        }


    def _update_sale_order_status(self, picking):
        """Update sale order status to 'Re - Dispatched'"""
        sale_order = picking.sale_id
        if not sale_order and picking.origin:
            purchase_order = self.env['purchase.order'].search([
                ('name', '=', picking.origin)
            ], limit=1)
            if purchase_order and purchase_order.origin:
                sale_order = self._find_sale_order_from_origin(purchase_order.origin)

        if sale_order:
            # ── Step 1: Update line availability_status to 're_dispatched' ────
            # The lines must be updated BEFORE calling _safe_write_augmont_status
            # (which is triggered by sale_order.write) so that the API call
            # inside _safe_write_augmont_status sends the correct per-line status
            # ('Re - Dispatched') rather than the stale 'dispatched' status.
            #
            # Update lines that are in the logistics cycle — these are the lines
            # that are being re-dispatched after a return.
            LOGISTICS_CYCLE = frozenset(['dispatched', 're_dispatched', 'return_of_order'])
            logistics_lines = sale_order.order_line.filtered(
                lambda l: l.availability_status in LOGISTICS_CYCLE
            )
            if logistics_lines:
                _logger.info(
                    "🔄 [RE-DISPATCH] Updating %d line(s) to 're_dispatched' for order %s",
                    len(logistics_lines), sale_order.name
                )
                logistics_lines.with_context(
                    from_quality_module=True,
                    skip_api_sync=True,
                ).write({'availability_status': 're_dispatched'})
            
            # ── Step 2: Write order status + tracking fields ───────────────────
            # sdk_augmont_status is handled by _safe_write_augmont_status inside
            # SaleOrder.write(), which also calls the website API with per-line
            # statuses (now correctly 're_dispatched' → 'Re - Dispatched').
            sale_order.write({
                'courier_partner_name': self.picking_id.tracker_name,
                'tracking_number': self.picking_id.tracking_number,
                'tracking_url': self.picking_id.tracking_url,
                'sdk_augmont_status': 'Re - Dispatched',
            })
            _logger.info("✅ [RE-DISPATCH] Sale order %s → 'Re - Dispatched'", sale_order.name)

            # ── Step 3: Belt-and-suspenders API call ──────────────────────────
            # Explicitly push per-line statuses to the website so the website
            # always receives 'Re - Dispatched' for each order_number, even if
            # _safe_write_augmont_status's API call failed for any reason.
            try:
                sale_order._call_augmont_status_api('Re - Dispatched')
                _logger.info(
                    "✅ [RE-DISPATCH API] Synced | Order: %s | 'Re - Dispatched' sent to website",
                    sale_order.name
                )
            except Exception as _api_err:
                _logger.error(
                    "❌ [RE-DISPATCH API] Failed | Order: %s | Error: %s",
                    sale_order.name, _api_err
                )

    def action_create_returns(self):
        """Override to update sale order status"""
        result = super().action_create_returns()
        
        # Get the original picking from context
        picking_id = self.env.context.get('active_id')
        if picking_id:
            picking = self.env['stock.picking'].browse(picking_id)
            self._update_sale_order_status(picking)
        
        return result

    def action_create_returns_all(self):
        """Override to update sale order status for return all"""
        result = super().action_create_returns_all()
        
        # Get the original picking from context
        picking_id = self.env.context.get('active_id')
        if picking_id:
            picking = self.env['stock.picking'].browse(picking_id)
            self._update_sale_order_status(picking)
        
        return result

    def action_create_exchange(self):
        """Override to update sale order status for exchange"""
        result = super().action_create_exchange()
        
        # Get the original picking from context
        picking_id = self.env.context.get('active_id')
        if picking_id:
            picking = self.env['stock.picking'].browse(picking_id)
            self._update_sale_order_status(picking)
        
        return result


class StockReturnPickingLine(models.TransientModel):
    _inherit = 'stock.return.picking.line'

    @api.model
    def default_get(self, fields_list):
        res = super(StockReturnPickingLine, self).default_get(fields_list)
        # Set default quantity to 1 if field is present
        if 'quantity' in fields_list:
            res['quantity'] = 1
        return res

    # commented code ////////////////////////////////////////////////////////////////////////////////////////////
    
    
    # def button_validate(self):
    #     """Override to add QC checks and auto-create transfers/POs"""
    #     scrap_location_id = 19

    #     for picking in self:
    #         if picking.location_id.id == 18 and picking.location_dest_id.id == 10:
    #             moves = picking.move_ids_without_package
    #             failed_moves = moves.filtered(lambda m: m.qc_status == 'fail')
    #             passed_moves = moves.filtered(lambda m: m.qc_status == 'pass')

    #             # Block validation if any move has no QC status
    #             no_qc_moves = moves.filtered(lambda m: not m.qc_status)
    #             if no_qc_moves:
    #                 raise UserError(_("Please complete the QC check for all products before proceeding."))

    #             # Reroute failed moves to scrap — BEFORE validation
    #             if failed_moves:
    #                 failed_moves.write({'location_dest_id': scrap_location_id})

    #                 # If ALL moves failed, update picking destination
    #                 if all(m.location_dest_id.id == scrap_location_id for m in moves):
    #                     picking.write({'location_dest_id': scrap_location_id})

    #     res = super().button_validate()

    #     for picking in self:
    #         if picking.state != 'done':
    #             continue

    #         moves = picking.move_ids_without_package

    #         # 🚚 Case 1: From GRN (4) to Quality (18) → Create QC Transfer (Type 11)
    #         if picking.location_id.id == 4 and picking.location_dest_id.id == 18:
    #             existing_picking = self.env['stock.picking'].search([
    #                 ('location_id', '=', 4),
    #                 ('location_dest_id', '=', 18),
    #                 ('origin', '=', picking.origin),
    #                 ('state', 'in', ['draft', 'assigned']),
    #             ], limit=1)

    #             if not existing_picking:
    #                 picking_type = self.env['stock.picking.type'].browse(PT_QC)
    #                 picking_vals = {
    #                     'picking_type_id': picking_type.id,
    #                     'partner_id': picking.partner_id.id,
    #                     'location_id': LOC_QC,
    #                     'location_dest_id': LOC_INV,
    #                     'origin': picking.origin,
    #                     'scheduled_date': picking.scheduled_date,
    #                     'location': picking.location,
    #                     'move_ids_without_package': [
    #                         (0, 0, {
    #                             'name': move.name,
    #                             'product_id': move.product_id.id,
    #                             'product_uom_qty': move.product_uom_qty,
    #                             'quantity': move.product_uom_qty,
    #                             'product_uom': move.product_uom.id,
    #                             'location_id': LOC_QC,
    #                             'location_dest_id': LOC_INV,
    #                             'purchase_line_id': move.purchase_line_id.id if move.purchase_line_id else False,
    #                         }) for move in moves
    #                     ]
    #                 }

    #                 new_picking = self.env['stock.picking'].create(picking_vals)
    #                 new_picking.action_confirm()
    #                 new_picking.action_assign()

    #                 self._send_notification_email(
    #                     new_picking,
    #                     "__export__.res_groups_78_640c10c2",
    #                     _("New Transfer %s created — follow up on material outward quality process"),
    #                     _("""
    #                     <p>Dear Quality Team,</p>
    #                     <p>The New Transfer <strong>%s</strong> has been created.</p>
    #                     <p>Please follow up on the material inward Quality process.</p>
    #                     """)
    #                 )

    #         # 🧪 Case 2 & 3: From Quality (18) to Approved (10) → Create Inventory Transfer for PASSED moves only
    #         elif picking.location_id.id == 18 and picking.location_dest_id.id == 10:
    #             passed_moves = moves.filtered(lambda m: m.qc_status == 'pass')
    #             if not passed_moves:
    #                 continue  # Nothing passed QC, skip

    #             # Determine destination by location
    #             if picking.location == 'mumbai':
    #                 dest_loc_id = 20
    #                 picking_type_id = 12
    #             elif picking.location == 'surat':
    #                 dest_loc_id = 21
    #                 picking_type_id = 13
    #             else:
    #                 continue

    #             existing_picking = self.env['stock.picking'].search([
    #                 ('location_id', '=', 18),
    #                 ('location_dest_id', '=', 10),
    #                 ('origin', '=', picking.origin),
    #                 ('state', 'in', ['draft', 'assigned']),
    #             ], limit=1)

    #             if not existing_picking:
    #                 picking_type = self.env['stock.picking.type'].browse(picking_type_id)
    #                 picking_vals = {
    #                     'picking_type_id': picking_type.id,
    #                     'partner_id': picking.partner_id.id,
    #                     'location_id': 10,
    #                     'location_dest_id': dest_loc_id,
    #                     'origin': picking.origin,
    #                     'scheduled_date': picking.scheduled_date,
    #                     'location': picking.location,
    #                     'move_ids_without_package': [
    #                         (0, 0, {
    #                             'name': move.name,
    #                             'product_id': move.product_id.id,
    #                             'product_uom_qty': move.product_uom_qty,
    #                             'quantity': move.product_uom_qty,
    #                             'product_uom': move.product_uom.id,
    #                             'location_id': 10,
    #                             'location_dest_id': dest_loc_id,
    #                             'purchase_line_id': move.purchase_line_id.id if move.purchase_line_id else False,
    #                         }) for move in passed_moves  # ✅ ONLY PASSED ITEMS
    #                     ]
    #                 }

    #                 new_picking = self.env['stock.picking'].create(picking_vals)
    #                 new_picking.action_confirm()
    #                 new_picking.action_assign()

    #                 # Send notification
    #                 group_xml_id = "__export__.res_groups_84_c1e3c450"
    #                 self._send_notification_email(
    #                     new_picking,
    #                     group_xml_id,
    #                     _("New Transfer %s created — follow up on material outward quality process"),
    #                     _("""
    #                     <p>Dear Inventory Team,</p>
    #                     <p>The New Transfer <strong>%s</strong> has been created.</p>
    #                     <p>Please follow up on the material inward Quality process.</p>
    #                     """)
    #                 )

    #                 # Create PO for any failed moves (if not done already — optional double-check)
    #                 failed_moves = self.move_ids.filtered(lambda m: m.qc_status == 'fail')
    
    #                 if failed_moves:
    #                     # First create POs for failed moves
    #                     for move in failed_moves:
    #                         self._create_purchase_order_for_failed_qc(move)
                        
    #                     # Then show notification (but don't return it)
    #                     # failed_products = []
    #                     # for move in failed_moves:
    #                     #     product_name = move.product_id.display_name or move.product_id.name
    #                     #     failed_products.append(product_name)
                        
    #                     # Show notification using bus notification (non-blocking)
    #                     # message = _("The following products have failed QC and POs have been created:\n\n") + \
    #                     #      "\n".join(f"• {product}" for product in failed_products)
    #                     # self.env['bus.bus']._sendone(
    #                     #         self.env.user.partner_id,
    #                     #         'ir.actions.client',
    #                     #         {
    #                     #             'type': 'ir.actions.client',
    #                     #             'tag': 'display_notification',
    #                     #             'params': {
    #                     #                 'title': 'QC Failed - Purchase Orders Created',
    #                     #                 'message': message,
    #                     #                 'type': 'warning',
    #                     #                 'sticky': True,
    #                     #             }
    #                     #         }
    #                     #     )
                                                                
                        
    #     return res

    # def button_validate(self):
    #     """Override to add QC checks and auto-create transfers/POs"""
    #     scrap_location_id = 19

    #     # =============================================
    #     # PRE-VALIDATION: QC CHECKS & FAILED ITEMS HANDLING
    #     # =============================================
    #     for picking in self:
    #         if picking.location_id.id == 18 and picking.location_dest_id.id == 10:
    #             moves = picking.move_ids_without_package
    #             failed_moves = moves.filtered(lambda m: m.qc_status == 'fail')
    #             passed_moves = moves.filtered(lambda m: m.qc_status == 'pass')

    #             # Block validation if any move has no QC status
    #             no_qc_moves = moves.filtered(lambda m: not m.qc_status)
    #             if no_qc_moves:
    #                 raise UserError(_("Please complete the QC check for all products before proceeding."))

    #             # Reroute failed moves to scrap — BEFORE validation
    #             if failed_moves:
    #                 failed_moves.write({'location_dest_id': scrap_location_id})

    #                 # If ALL moves failed, update picking destination
    #                 if all(m.location_dest_id.id == scrap_location_id for m in moves):
    #                     picking.write({'location_dest_id': scrap_location_id})

    #     # =============================================
    #     # MAIN VALIDATION: ORIGINAL ODOO VALIDATION
    #     # =============================================
    #     res = super().button_validate()

    #     # =============================================
    #     # POST-VALIDATION: AUTO-CREATE NEXT TRANSFERS & UPDATE SALE ORDER STATUS
    #     # =============================================
    #     for picking in self:
    #         if picking.state != 'done':
    #             continue

    #         moves = picking.move_ids_without_package
            
    #         # 🎯 GET RELATED SALE ORDER THROUGH PURCHASE ORDER
    #         sale_order = picking.sale_id
            
    #         if not sale_order and picking.origin:
    #             # Method 1: Find Purchase Order first, then get related Sale Order
    #             purchase_order = self.env['purchase.order'].search([
    #                 ('name', '=', picking.origin)  # P00063
    #             ], limit=1)
                
    #             if purchase_order:
    #                 # Get sale order from purchase order origin
    #                 if purchase_order.origin:
    #                     sale_order = self.env['sale.order'].search([
    #                         ('name', '=', purchase_order.origin)  # S00140
    #                     ], limit=1)
                    
    #                 # Alternative: Get sale order through purchase order lines
    #                 if not sale_order:
    #                     order_lines = purchase_order.order_line
    #                     for line in order_lines:
    #                         if line.move_dest_ids and line.move_dest_ids[0].sale_line_id:
    #                             sale_order = line.move_dest_ids[0].sale_line_id.order_id
    #                             if sale_order:
    #                                 break
            
    #         # Method 2: Try direct search as fallback
    #         if not sale_order and picking.origin:
    #             sale_order = self.env['sale.order'].search([
    #                 ('name', '=', picking.origin)
    #             ], limit=1)

    #         # 🚚 CASE 1: LOGISTICS RECEIPT → QUALITY TRANSFER (AUG/QC/00052)
    #         # Update sale order status to 'In QC process'
    #         if picking.location_id.id == 4 and picking.location_dest_id.id == 18:
    #             if sale_order:
    #                 sale_order.write({
    #                     'sdk_augmont_status': 'In QC process'
    #                 })

    #             existing_picking = self.env['stock.picking'].search([
    #                 ('location_id', '=', 4),
    #                 ('location_dest_id', '=', 18),
    #                 ('origin', '=', picking.origin),
    #                 ('state', 'in', ['draft', 'assigned']),
    #             ], limit=1)

    #             if not existing_picking:
    #                 picking_type = self.env['stock.picking.type'].browse(PT_QC)
    #                 picking_vals = {
    #                     'picking_type_id': picking_type.id,
    #                     'partner_id': picking.partner_id.id,
    #                     'location_id': LOC_QC,
    #                     'location_dest_id': LOC_INV,
    #                     'origin': picking.origin,
    #                     'scheduled_date': picking.scheduled_date,
    #                     'location': picking.location,
    #                     'move_ids_without_package': [
    #                         (0, 0, {
    #                             'name': move.name,
    #                             'product_id': move.product_id.id,
    #                             'product_uom_qty': move.product_uom_qty,
    #                             'quantity': move.product_uom_qty,
    #                             'product_uom': move.product_uom.id,
    #                             'location_id': LOC_QC,
    #                             'location_dest_id': LOC_INV,
    #                             'purchase_line_id': move.purchase_line_id.id if move.purchase_line_id else False,
    #                         }) for move in moves
    #                     ]
    #                 }

    #                 # 🎯 CREATES: (Quality Transfer)
    #                 new_picking = self.env['stock.picking'].create(picking_vals)
    #                 new_picking.action_confirm()
    #                 new_picking.action_assign()

    #                 self._send_notification_email(
    #                     new_picking,
    #                     "__export__.res_groups_78_640c10c2",
    #                     _("New Transfer %s created — follow up on material outward quality process"),
    #                     _("""
    #                     <p>Dear Quality Team,</p>
    #                     <p>The New Transfer <strong>%s</strong> has been created.</p>
    #                     <p>Please follow up on the material inward Quality process.</p>
    #                     """)
    #                 )

    #         # 🧪 CASE 2 & 3: QUALITY TRANSFER → INVENTORY TRANSFER 
    #         # Update sale order status to 'Payment Pending'
    #         elif picking.location_id.id == 18 and picking.location_dest_id.id == 10:
    #             if sale_order:
    #                 sale_order.write({
    #                     'sdk_augmont_status': 'Payment Pending'
    #                 })

    #             passed_moves = moves.filtered(lambda m: m.qc_status == 'pass')
    #             if not passed_moves:
    #                 continue  # Nothing passed QC, skip

    #             # Determine destination by location
    #             if picking.location == 'mumbai':
    #                 dest_loc_id = 20
    #                 picking_type_id = 12
    #             elif picking.location == 'surat':
    #                 dest_loc_id = 21
    #                 picking_type_id = 13
    #             else:
    #                 continue

    #             existing_picking = self.env['stock.picking'].search([
    #                 ('location_id', '=', 18),
    #                 ('location_dest_id', '=', 10),
    #                 ('origin', '=', picking.origin),
    #                 ('state', 'in', ['draft', 'assigned']),
    #             ], limit=1)

    #             if not existing_picking:
    #                 picking_type = self.env['stock.picking.type'].browse(picking_type_id)
    #                 picking_vals = {
    #                     'picking_type_id': picking_type.id,
    #                     'partner_id': picking.partner_id.id,
    #                     'location_id': 10,
    #                     'location_dest_id': dest_loc_id,
    #                     'origin': picking.origin,
    #                     'scheduled_date': picking.scheduled_date,
    #                     'location': picking.location,
    #                     'move_ids_without_package': [
    #                         (0, 0, {
    #                             'name': move.name,
    #                             'product_id': move.product_id.id,
    #                             'product_uom_qty': move.product_uom_qty,
    #                             'quantity': move.product_uom_qty,
    #                             'product_uom': move.product_uom.id,
    #                             'location_id': 10,
    #                             'location_dest_id': dest_loc_id,
    #                             'purchase_line_id': move.purchase_line_id.id if move.purchase_line_id else False,
    #                         }) for move in passed_moves  # ✅ ONLY PASSED ITEMS
    #                     ]
    #                 }

    #                 # 🎯 CREATES: AUG/INV/00038 (Inventory Transfer)
    #                 new_picking = self.env['stock.picking'].create(picking_vals)
    #                 new_picking.action_confirm()
    #                 new_picking.action_assign()


    #                 # Send notification
    #                 group_xml_id = "__export__.res_groups_84_c1e3c450"
    #                 self._send_notification_email(
    #                     new_picking,
    #                     group_xml_id,
    #                     _("New Transfer %s created — follow up on material outward quality process"),
    #                     _("""
    #                     <p>Dear Inventory Team,</p>
    #                     <p>The New Transfer <strong>%s</strong> has been created.</p>
    #                     <p>Please follow up on the material inward Quality process.</p>
    #                     """)
    #                 )

    #                 # 🛒 AUTO PO CREATION FOR FAILED ITEMS
    #                 failed_moves = self.move_ids.filtered(lambda m: m.qc_status == 'fail')
        
    #                 if failed_moves:
    #                     # Create POs for failed moves
    #                     for move in failed_moves:
    #                         self._create_purchase_order_for_failed_qc(move)

    #         return res


# class StockPickingWizard(models.TransientModel):
#     _name = 'stock.picking.wizard'
#     _description = 'Wizard to Update Stock Picking'

#     tracker_name = fields.Char(string="Delivered By")
#     tracking_number = fields.Char(string="Tracking Number")
#     tracking_url = fields.Char(string="Tracking Url")

#     # def action_apply(self):
#     #     """Store the field value into stock.picking"""
#     #     picking = self.env['stock.picking'].browse(self.env.context.get('active_id'))
#     #     if picking:
#     #         picking.write({
#     #             'tracking_number': self.tracking_number,
#     #             'tracking_url': self.tracking_url,
#     #             'tracker_name' : self.tracker_name,
#     #             })
#     #     return {'type': 'ir.actions.act_window_close'}

#     def action_apply(self):
#         """Store the field value into stock.picking and update sale order status"""
#         picking = self.env['stock.picking'].browse(self.env.context.get('active_id'))
#         if picking:
#             # Update tracking information
#             picking.write({
#                 'tracking_number': self.tracking_number,
#                 'tracking_url': self.tracking_url,
#                 'tracker_name': self.tracker_name,
#             })
            
#             # ✅ UPDATE SALE ORDER STATUS: Order Completed
#             # Only update if picking is in done state
#             if picking.state == 'done':
#                 sale_order = picking.sale_id
#                 if not sale_order and picking.origin:
#                     # Try to find sale order through purchase order
#                     purchase_order = self.env['purchase.order'].search([
#                         ('name', '=', picking.origin)
#                     ], limit=1)
#                     if purchase_order and purchase_order.origin:
#                         sale_order = self.env['sale.order'].search([
#                             ('name', '=', purchase_order.origin)
#                         ], limit=1)
                
#                 if sale_order:
#                     sale_order.write({
#                         'sdk_augmont_status': 'Order Completed'
#                     })
            
#         return {'type': 'ir.actions.act_window_close'}
    
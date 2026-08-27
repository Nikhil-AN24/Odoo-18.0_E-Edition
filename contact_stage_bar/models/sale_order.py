from odoo import models, fields,api,_
from markupsafe import Markup
from datetime import datetime, timedelta
from odoo.exceptions import UserError, ValidationError
import requests
import json
import time
import psycopg2
from markupsafe import Markup
#from odoo.addons.queue_job.tests.common import trap_jobs

import  logging
_logger = logging.getLogger(__name__)


ALLOWED_AUGMONT_STATUS_TRANSITIONS = {
    "Diamond Booked": ["Confirmed", "Not available"],
    "Confirmed": ["In QC process", "Cancelled"],
    "Not available": ["Cancelled"],
    "In QC process": ["Payment Pending", "QC Fail"],
    "QC Fail": ["Cancelled"],
    "Payment Pending": ["Payment Completed", "Cancelled"],
    "Payment Completed": ["Dispatched"],
    "Dispatched": ["Delivered", "Return of Order"],
    "Return of Order": ["Re - Dispatched", "Delivered"],
    "Re - Dispatched": ["Delivered", "Return of Order"],
    "Delivered": ["Order Completed"],
    "Cancelled": [],
    "Order Completed": [],
}


class SaleOrder(models.Model):
    _inherit = 'sale.order'
    email = fields.Char(related='partner_id.email', string="Email")

# Dynamic Selection field to show Flag + Name + Code
    @api.model
    def _get_country_code_selection(self):
        countries = self.env['res.country'].sudo().search([])
        selection = []
        for c in countries:
            flag = ''
            if c.code and len(c.code) == 2:
                # Convert ISO country code to Unicode Emoji Flag
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
        string="Country Code",
    )

    @api.onchange('partner_id')
    def _onchange_country_code_custom(self):
        for order in self:
            if order.partner_id and order.partner_id.country_id:
                order.country_code = str(order.partner_id.country_id.id)
            else:
                order.country_code = False

    # ── AUTO-SALESPERSON ASSIGNMENT ON CUSTOMER CHANGE ───────────────────────
    @api.onchange('partner_id')
    def _onchange_partner_id_auto_salesperson(self):
        for order in self:
            if order.partner_id and order.partner_id.user_id:
                sp = order.partner_id.user_id
                if sp.active:
                    order.user_id = sp
                    _logger.info(
                        "🧑‍💼 [AUTO-SALESPERSON] Order for customer '%s' → "
                        "pre-assigned salesperson: '%s' (from partner.user_id)",
                        order.partner_id.name, sp.name
                    )
                else:
                    _logger.warning(
                        "⚠️ [AUTO-SALESPERSON] Salesperson '%s' on customer '%s' "
                        "is inactive/archived. Leaving user_id unchanged.",
                        sp.name, order.partner_id.name
                    )

    def action_print_the_invoice(self):
        """Create the invoice (if not already created) and hand back the
        Augmont Tax Invoice PDF, in one click.

        Replaces the standard "Create Invoice" button, which is hidden on this
        view: the business wants a single "Print an Invoice" action rather
        than the advance-payment wizard's regular/down-payment choice.
        """
        self.ensure_one()
        try:
            self._create_invoices()
        except Exception:
            pass
        return self.env.ref(
            'contact_stage_bar.action_report_augmont_tax_invoice'
        ).report_action(self, config=False)

    mobile_number = fields.Char(related='partner_id.phone', string="Mobile Number")
    shipping_charges = fields.Float(string="Shipping Charges", digits=(16, 2))

    # --- CUSTOM SHIPPING MATH OVERRIDES ---
    @api.depends('order_line.price_subtotal', 'order_line.price_tax', 'order_line.price_total', 'shipping_charges')
    def _compute_amounts(self):
        super(SaleOrder, self)._compute_amounts()
        for order in self:
            if order.shipping_charges:
                order.amount_untaxed += order.shipping_charges
                order.amount_total += order.shipping_charges

    @api.depends('order_line.tax_id', 'order_line.price_unit', 'amount_total', 'amount_untaxed', 'currency_id', 'shipping_charges')
    def _compute_tax_totals(self):
        super(SaleOrder, self)._compute_tax_totals()
        for order in self:
            if order.shipping_charges and order.tax_totals:
                tax_totals = order.tax_totals.copy() if isinstance(order.tax_totals, dict) else order.tax_totals
                if isinstance(tax_totals, dict):
                    if 'base_amount_currency' in tax_totals:
                        tax_totals['base_amount_currency'] += order.shipping_charges
                        tax_totals['formatted_amount_untaxed'] = order.currency_id.format(tax_totals['base_amount_currency'])
                        
                    if 'total_amount_currency' in tax_totals:
                        tax_totals['total_amount_currency'] += order.shipping_charges
                        tax_totals['formatted_amount_total'] = order.currency_id.format(tax_totals['total_amount_currency'])
                    
                    if 'amount_untaxed' in tax_totals:
                        tax_totals['amount_untaxed'] += order.shipping_charges
                    if 'amount_total' in tax_totals:
                        tax_totals['amount_total'] += order.shipping_charges
                    
                    if 'subtotals' in tax_totals:
                        for subtotal in tax_totals['subtotals']:
                            if 'amount' in subtotal:
                                subtotal['amount'] += order.shipping_charges
                                subtotal['formatted_amount'] = order.currency_id.format(subtotal['amount'])
                            if 'base_amount_currency' in subtotal:
                                subtotal['base_amount_currency'] += order.shipping_charges
                            
                    order.tax_totals = tax_totals

    payment_count = fields.Integer(string="Payment Count", compute="_compute_payment_count", store=True)
    gst_treatment = fields.Selection([
        ('within_maharashtra', 'Within Maharashtra'),
        ('outside_maharashtra', 'Outside Maharashtra'),
    ], string='GST Treatment')
    
    payment_ids = fields.Many2many(
        'account.payment',
        string="Payments",
        readonly=True,
    )
    state = fields.Selection([
        ('draft', 'Draft Order'),
        ('sent', 'Verification Email Sent'),
        ('sale', 'Confirmed Order'),
        ('cancel', 'Cancelled'),
    ], string='Status', readonly=True, copy=False, tracking=True)
    vat = fields.Char(string="GST Number")
    ein_number = fields.Char(string="EIN Number")
    location = fields.Selection([('mumbai', 'India'), ('surat', 'USA')], string='Location')
    sdk_augmont_number = fields.Char(string="Invoice Number")
    shipping_address = fields.Text(string="Shipping Address")
    billing_address = fields.Text(string="Billing Address")
    default_qc_requirements = fields.Char(string="Default QC Requirements")
    item_notes = fields.Char(string="General Order Notes")


    is_website = fields.Boolean(string="Is Website Order", default=False)
    order_source = fields.Selection(
        [('website', 'Website'), ('offline', 'Offline')],
        string="Order Source", copy=False,
        help="Where this order originated. Website orders sync status to the "
             "Augmont website API; offline orders are handled entirely in Odoo.")
    sdk_augmont_status = fields.Selection([
        ('Order Received', 'Order Received'),
        ('Diamond Booked', 'Diamond Booked'), 
        ('Confirmed', 'Confirmed'),
        ('Order Confirmed', 'Order Confirmed'), 
        ('Not available', 'Not available'),
        ('Cancelled', 'Cancelled'),
        ('Confirmation Pending', 'Confirmation Pending'),
        ('Availability Check', 'Availability Check'),
        ('In QC process', 'In QC process'),
        ('QC Fail', 'QC Fail'),
        ('Payment Pending', 'Payment Pending'),
        ('Payment Completed', 'Payment Completed'),
        ('Dispatched', 'Dispatched'),
        ('Return of Order', 'Return of Order'),
        ('Re - Dispatched', 'Re - Dispatched'),
        ('Delivered', 'Delivered'),
        ('Order Completed', 'Order Completed'),
    ], string='Order Status', compute='_compute_order_status_from_lines', store=True, 
       readonly=False, tracking=True, default='Order Received')
    
    is_fully_paid = fields.Boolean(string="Fully Paid", compute="_compute_is_fully_paid", store=True,default=False)

    #   1. Hide the {Save} button once the user has finalised all line statuses.
    #   2. Freeze the Availability column so no further edits are possible.
    all_lines_final = fields.Boolean(
        string='All Lines Final',
        compute='_compute_all_lines_final',
        store=True,
    )

    @api.depends('order_line.availability_status')
    def _compute_all_lines_final(self):
        """True when every order line with a product is 'confirmed' or 'cancelled'.
        MELEE child parcel lines (those with melee_parent_line_id set) are excluded
        from this check — only the parent summary line represents the MELEE request.
        """
        for order in self:
            # Exclude MELEE child parcel lines; only parent lines and LGD lines count
            lines = order.order_line.filtered(lambda l: l.product_id and not l.melee_parent_line_id)
            if not lines:
                order.all_lines_final = False
            else:
                order.all_lines_final = all(
                    l.availability_status in ('confirmed', 'cancelled')
                    for l in lines
                )

    utr_number = fields.Char('UTR Number', tracking=True)
    courier_partner_name = fields.Char('Courier Partner Name', tracking=True)
    tracking_number = fields.Char('Tracking Number', tracking=True)
    tracking_url = fields.Char(string='Tracking Url' , tracking=True)
    
    @api.depends('order_line.availability_status', 'state')
    def _compute_order_status_from_lines(self):
        """
        Compute Invoice-level Order Status from order line availability_status values.
        PRIORITY (highest → lowest):
          C1   Order Completed  – C1-active ⊆ {delivered, order_completed}
          C2   Dispatched       – at least 1 in {dispatched, re_dispatched, return_of_order}
          C3   Payment Completed – C3-active == {payment_completed}
          C4   In QC process    – at least 1 in_qc_process
          C5   Availability Check – at least 1 not_available OR at least 1 qc_fail
          C6   Order Confirmed  – at least 1 payment_pending AND at least 1 confirmed
          C7   Payment Pending  – C7-active == {payment_pending}
          C8   Confirmation Pending – at least 1 confirmed AND at least 1 diamond_booked
          C9   Order Confirmed  – C9-active == {confirmed}
          C10  Order Received   – at least 1 diamond_booked
          C11  Cancelled        – all statuses == {cancelled}

        ANTI-RECURSION GUARD (_in_compute_augmont_status)
        ─────────────────────────────────────────────────
        Assigning order.sdk_augmont_status triggers SaleOrder.write(), which
        normally calls _safe_write_augmont_status() and re-syncs lines, which
        triggers this compute again → infinite recursion.
        We run with _in_compute_augmont_status=True so write() takes a
        fast SQL-only path and skips the line sync.
        """

        C1_IGNORE = frozenset([
            'diamond_booked', 'confirmed', 'not_available', 'cancelled',
            'in_qc_process', 'qc_fail', 'payment_pending', 'payment_completed',
            'dispatched', 're_dispatched',
        ])
        # C3: ignore diamond_booked / confirmed / qc_fail / not_available / cancelled
        C3_IGNORE = frozenset([
            'diamond_booked', 'confirmed', 'qc_fail', 'not_available', 'cancelled',
        ])
        # C7: ignore cancelled / not_available / qc_fail / diamond_booked / confirmed
        C7_IGNORE = frozenset([
            'cancelled', 'not_available', 'qc_fail', 'diamond_booked', 'confirmed',
            'payment_completed',
        ])
        # C9: ignore everything except confirmed (and diamond_booked)
        C9_IGNORE = frozenset([
            'not_available', 'cancelled', 'in_qc_process', 'qc_fail',
            'payment_completed', 'dispatched', 'return_of_order',
            're_dispatched', 'delivered', 'order_completed', 'diamond_booked',
        ])
        # Logistics statuses used in C2
        LOGISTICS = frozenset(['dispatched', 'return_of_order', 're_dispatched'])

        for order in self.with_context(_in_compute_augmont_status=True):

            if isinstance(order.id, int):
                order.env.cr.execute(
                    "SELECT sdk_augmont_status FROM sale_order WHERE id = %s",
                    (order.id,)
                )
                _row = order.env.cr.fetchone()

                _db_old_status = _row[0] if _row else False
            else:
                _db_old_status = False

            # MELEE child parcel lines (those with melee_parent_line_id set) are excluded
            # from invoice-level status computation. Only the parent MELEE summary line
            # (is_melee_parent=True) represents the full MELEE request for status purposes.
            lines = order.order_line.filtered(lambda l: l.availability_status and not l.melee_parent_line_id)
            if not lines:
                order.sdk_augmont_status = 'Order Received'
                _logger.info("📊 Order %s: no lines → Order Received", order.name)
                continue

            all_statuses = set(lines.mapped('availability_status'))
            all_statuses.discard(False)

            if not all_statuses:
                order.sdk_augmont_status = 'Order Received'
                continue

            # ── Derived active sets ───────────────────────────────────────
            def active_set(ignore):
                """Return set of unique statuses after filtering out ignore-set."""
                return set(
                    lines.filtered(lambda l: l.availability_status not in ignore)
                         .mapped('availability_status')
                ) - {False}

            c1_active = active_set(C1_IGNORE)   # only delivered / order_completed remain
            c3_active = active_set(C3_IGNORE)   # payment flow 
            c7_active = active_set(C7_IGNORE)   # payment_pending + in-flight only
            c9_active = active_set(C9_IGNORE)   # only confirmed remains

            _logger.info(
                "📊 Order %s: All=%s | C1=%s | C3=%s | C7=%s | C9=%s",
                order.name, all_statuses, c1_active, c3_active, c7_active, c9_active
            )

            # ── Odoo cancel early exit ────────────────────────────────────
            if order.state == 'cancel':
                order.sdk_augmont_status = 'Cancelled'
                _logger.info("❌ Odoo cancel state → Cancelled")
                continue

            # ── C1: Order Completed ───────────────────────────────────────
            # All lines (ignoring non-terminal statuses) are delivered/order_completed
            if c1_active and c1_active <= {'delivered', 'order_completed'}:
                order.sdk_augmont_status = 'Order Completed'
                _logger.info("✅ C1: Order Completed")

            # ── C2: Dispatched ────────────────────────────────────────────
            # At least one line is in a logistics status
            elif all_statuses & LOGISTICS:
                order.sdk_augmont_status = 'Dispatched'
                _logger.info("✅ C2: Dispatched (logistics=%s)", all_statuses & LOGISTICS)

            # ── C3: Payment Completed ─────────────────────────────────────
            # All payment-flow lines = payment_completed
            elif c3_active and c3_active == {'payment_completed'}:
                order.sdk_augmont_status = 'Payment Completed'
                _logger.info("✅ C3: Payment Completed")

            # ── C4: In QC process ─────────────────────────────────────────
            # At least one line is still in QC — takes priority over C5-C11.
            # Invoice stays "In QC process" until every QC line moves forward.
            elif 'in_qc_process' in all_statuses:
                order.sdk_augmont_status = 'In QC process'
                _logger.info("✅ C4: In QC process")

            # ── C5: Availability Check ────────────────────────────────────
            # At least one not_available OR at least one qc_fail
            elif 'not_available' in all_statuses or 'qc_fail' in all_statuses:
                order.sdk_augmont_status = 'Availability Check'
                _logger.info("✅ C5: Availability Check (not_available/qc_fail present)")

            # ── C6: Order Confirmed (mixed payment_pending + confirmed) ────
            # At least 1 payment_pending AND at least 1 confirmed
            elif 'payment_pending' in all_statuses and 'confirmed' in all_statuses:
                order.sdk_augmont_status = 'Order Confirmed'
                _logger.info("✅ C6: Order Confirmed (payment_pending + confirmed mix)")

            # ── C7: Payment Pending ───────────────────────────────────────
            # All active  = payment_pending
            elif c7_active and c7_active == {'payment_pending'}:
                order.sdk_augmont_status = 'Payment Pending'
                _logger.info("✅ C7: Payment Pending")

            # ── C8: Confirmation Pending ──────────────────────────────────
            # At least 1 confirmed AND at least 1 diamond_booked
            elif 'confirmed' in all_statuses and 'diamond_booked' in all_statuses:
                order.sdk_augmont_status = 'Confirmation Pending'
                _logger.info("✅ C8: Confirmation Pending (confirmed + diamond_booked)")

            # ── C9: Order Confirmed ───────────────────────────────────────
            # After ignoring all non-confirmed/non-diamond statuses, only confirmed remains
            # (covers: all-confirmed, or confirmed + cancelled, or confirmed + not_available)
            elif c9_active and c9_active == {'confirmed'}:
                order.sdk_augmont_status = 'Order Confirmed'
                _logger.info("✅ C9: Order Confirmed (all active = confirmed)")

            # ── C10: Order Received ───────────────────────────────────────
            # At least 1 diamond_booked (and nothing triggers earlier conditions)
            elif 'diamond_booked' in all_statuses:
                order.sdk_augmont_status = 'Order Received'
                _logger.info("✅ C10: Order Received (diamond_booked present)")

            # ── C11: Cancelled ────────────────────────────────────────────
            # Every single line is cancelled
            elif all_statuses == {'cancelled'}:
                order.sdk_augmont_status = 'Cancelled'
                _logger.info("✅ C11: Cancelled (all lines cancelled)")

            # ── Fallback ──────────────────────────────────────────────────
            else:
                order.sdk_augmont_status = 'Order Received'
                _logger.info("⚠️ Fallback → Order Received (unmatched: %s)", all_statuses)

            _logger.info("   📊 Final Status: %s", order.sdk_augmont_status)

            # ── Odoo → Website sync from within compute ───────────────────
            # Uses _db_old_status read at the TOP of this loop iteration
            # (before any assignment could overwrite the DB value).
            if (not order.env.context.get('from_website_api')
                    and not order.env.context.get('no_recompute')
                    and not order.env.context.get('dispatch_validation')
                    and not order.env.context.get('skip_api_sync')
                    and not order.env.context.get('install_mode')
                    and isinstance(order.id, int)):
                try:
                    new_status = order.sdk_augmont_status
                    if new_status and new_status != _db_old_status:
                        _logger.info(
                            "🌐 [COMPUTE→WEBSITE] Order %s: %s → %s",
                            order.name, _db_old_status, new_status
                        )
                        order._call_augmont_status_api(new_status, _db_old_status)
                except Exception as _api_err:
                    _logger.error(
                        "❌ [COMPUTE→WEBSITE] API sync error for %s: %s",
                        order.name, _api_err
                    )

        
    def action_update_utr_to_augmont(self):
        """
        Send UTR number to Augmont API
        """
        for order in self:
            if not order.sdk_augmont_number:
                raise UserError(f"No Augmont invoice number found for order {order.name}")
            
            if not order.utr_number:
                raise UserError(f"No UTR number to update for order {order.name}")

            
            Param = self.env['ir.config_parameter'].sudo()
            url = Param.get_param('base_augmont_url')
            url = f"{url}/api/v1/odoo/utr-tracking/update"
            headers = {
                "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJtZXJjaGFudElkIjoiZjA3NDA3YzItZTI1ZS00YjI2LTk1MWUtODliZGQxZjI4YmQ2Iiwic2hvcnROYW1lIjoib2RvbyIsImlhdCI6MTc2MDYxNjU3OCwiZXhwIjoyMDc2MTkyNTc4fQ.XpKKN78VVCezg6BrEhQYxvTs1FTmES59pyrp3zFpmdI",
                "Content-Type": "application/json"
            }
            
            current_user = self.env.user
            if order.sdk_augmont_status == "Payment Completed":
                payload = {
                    "invoiceNumber": order.sdk_augmont_number,
                    "trackingUpdates": {
                        "utrNumber": order.utr_number
                    },
                    "user": {
                        "firstName": current_user.partner_id.name.split()[0] if current_user.partner_id.name else "Admin",
                        "lastName": current_user.partner_id.name.split()[-1] if len(current_user.partner_id.name.split()) > 1 else "User",
                        "email": current_user.email or "admin@example.com",
                        "mobileNumber": current_user.partner_id.phone or "9876543210"
                    }
                }
            
            elif order.sdk_augmont_status == "Dispatched" or "Re - Dispatched":
                payload = {
                    "invoiceNumber": order.sdk_augmont_number,
                    "trackingUpdates": {
                        "courierPartnerName": order.courier_partner_name,
                        "trackingNumber": order.tracking_number,
                    },
                    "user": {
                        "firstName": current_user.partner_id.name.split()[0] if current_user.partner_id.name else "Admin",
                        "lastName": current_user.partner_id.name.split()[-1] if len(current_user.partner_id.name.split()) > 1 else "User",
                        "email": current_user.email or "admin@example.com",
                        "mobileNumber": current_user.partner_id.phone or "9876543210"
                    }
                }
            
            _logger.info("Updating UTR %s for invoice %s", order.utr_number, order.sdk_augmont_number)
            
            try:
                response = requests.post(url,headers=headers, json=payload, timeout=15)
                
                if response.status_code == 200:
                    res_json = response.json()
                    _logger.info("UTR updated successfully: %s", res_json)
                    order.message_post(
                        body=f"UTR number {order.utr_number} successfully sent to Augmont",
                        subject="UTR Update Success"
                    )
                    return res_json
                else:
                    error_msg = f"API error: {response.status_code} - {response.text}"
                    _logger.error(error_msg)
                    raise UserError(error_msg)
                    
            except requests.RequestException as e:
                error_msg = f"Failed to update UTR: {str(e)}"
                _logger.exception(error_msg)
                raise UserError(error_msg)
    
    @api.onchange('gst_treatment')
    def _onchange_gst_treatment(self):
        for order in self:
            if order.gst_treatment == 'within_maharashtra':
                fiscal_position = self.env['account.fiscal.position'].search([('name', '=', 'Within Maharashtra')],limit=1)
                order.fiscal_position_id = fiscal_position.id if fiscal_position else False
                # order.action_update_taxes()
            elif order.gst_treatment == 'outside_maharashtra':
                fiscal_position = self.env['account.fiscal.position'].search([('name', '=', 'Inter State')], limit=1)
                order.fiscal_position_id = fiscal_position.id if fiscal_position else False
                # order.action_update_taxes()
            else:
                order.fiscal_position_id = False
    
    
    def action_open_add_product_wizard(self):
        self.ensure_one()
        # context = ({
        #     'default_order_id': self.id,
        #     'default_name': '[NEW]',
        #     'default_is_storable': True,
        #     'default_route_ids': [(6, 0, [1, 5])],
        # })
        return {
            'name': "Add Product by Number",
            'type': 'ir.actions.act_window',
            'res_model': 'sale.order.add.product.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_order_id': self.id,
                'default_name': '[NEW]',
                'default_is_storable': True,
                'default_route_ids': [(6, 0, [1, 5])],
            },
        }
    
    def _track_subtype(self, init_values):
        price_only_fields = {'amount_total', 'amount_untaxed', 'amount_tax'}
        if init_values and set(init_values.keys()) <= price_only_fields:
            return False  # suppress the amount tracking message
        return super()._track_subtype(init_values)

    def _lock_order_lines_or_raise(self):
        """Take a row-level lock on this order's lines so two Cancel operations
        (or any concurrent status change) cannot interleave. NOWAIT => fail fast
        with a friendly message instead of blocking the request."""
        self.ensure_one()
        try:
            self.env.cr.execute(
                "SELECT id FROM sale_order_line WHERE order_id = %s FOR UPDATE NOWAIT",
                (self.id,),
            )
        except psycopg2.OperationalError:
            # LockNotAvailable (someone else holds the lock)
            raise UserError(_(
                "This order is being updated by another user right now. "
                "Please wait a moment and try again."
            ))

    def _relevant_status_lines(self):
        """Lines the order-status compute considers (skip MELEE child parcels
        and lines with no availability status)."""
        return self.order_line.filtered(
            lambda l: l.availability_status and not l.melee_parent_line_id
        )

    def action_cancel_unavailable_lines(self):
        """Header 'Cancel' button (draft only).

        Every order line must be 'Diamond Booked' or 'Not available', else we
        refuse. Augmont's API rejects a direct Diamond Booked -> Cancelled
        transition, so we go in two committed steps:

          1. Diamond Booked -> Not available, then flush + commit. The commit
             persists the change so the status recompute fires the Augmont API
             ('Not available') AND the chatter records this as its own stage.
          2. Not available -> Cancelled for every line, then flush + commit,
             firing the Augmont API ('Cancelled') and a second chatter stage.

        Race handling: because each cr.commit() releases row locks, we (re)take
        a FOR UPDATE lock on the lines and (re)validate their statuses from the
        DB before every write. If a concurrent process changed a line in
        between, we abort cleanly instead of pushing an invalid transition.
        """
        self.ensure_one()

        # ── Guard: only from draft (view hides it elsewhere; block direct calls) ──
        if self.state != 'draft':
            raise UserError(_("Cancel is only available while the order is in Draft."))

        # ── Lock + validate the starting state ──
        self._lock_order_lines_or_raise()
        lines = self._relevant_status_lines()
        if not lines or any(
            l.availability_status not in ('diamond_booked', 'not_available')
            for l in lines
        ):
            raise UserError(_(
                "Cancel is only allowed when ALL order lines are "
                "'Diamond Booked' or 'Not available'.\n\nThis order has "
                "line(s) in another status, so it cannot be cancelled."
            ))

        # ── Step 1: Diamond Booked -> Not available (a transition Augmont accepts) ──
        diamond_lines = lines.filtered(
            lambda l: l.availability_status == 'diamond_booked'
        )
        if diamond_lines:
            diamond_lines.write({'availability_status': 'not_available'})
            self.env.flush_all()
            # Line writes suppress the automatic push ("API via {Save} only"),
            # so push the intermediate 'Not available' to Augmont explicitly —
            # this is the transition Augmont accepts from 'Diamond Booked'.
            self.invalidate_recordset(['sdk_augmont_status'])
            self._call_augmont_status_api(self.sdk_augmont_status)
            self.env.cr.commit()   # persist the intermediate 'Not available' step
            # commit released our lock — re-lock and re-read fresh DB state.
            self.env.invalidate_all()
            self._lock_order_lines_or_raise()
            lines = self._relevant_status_lines()
            # After step 1 every relevant line should be 'not_available'. If a
            # concurrent process moved one somewhere else (e.g. re-booked), abort
            # rather than firing a wrong transition. Already-'cancelled' lines are
            # fine and simply skipped below.
            unexpected = lines.filtered(
                lambda l: l.availability_status not in ('not_available', 'cancelled')
            )
            if unexpected:
                raise UserError(_(
                    "The order changed while it was being cancelled "
                    "(a line is no longer 'Not available'). No further changes "
                    "were made — please review and try again."
                ))

        # ── Step 2: Not available -> Cancelled ──
        to_cancel = lines.filtered(lambda l: l.availability_status == 'not_available')
        if to_cancel:
            to_cancel.write({'availability_status': 'cancelled'})
            self.env.flush_all()
            # Explicitly push the final 'Cancelled' status to Augmont.
            self.invalidate_recordset(['sdk_augmont_status'])
            self._call_augmont_status_api(self.sdk_augmont_status)
            self.env.cr.commit()
        return True

    def action_confirm(self):
        import time
        import datetime as _dt
        start_time = time.time()
        _logger.info(
            "=" * 70 + "\n"
            "✅ [SO CONFIRM] action_confirm called | %s | Orders: %s",
            _dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            ', '.join(o.name for o in self),
        )
        _logger.info("🔹 Starting action_confirm for %d sale orders", len(self))

        _logger.info("🔹 {Confirm} called — skipping status forcing")

        parent_start = time.time()
        # Pass skip_api_sync=True so that Odoo's internal state change (draft → sale)
        # does NOT trigger _compute_order_status_from_lines() → API call with
        # intermediate/wrong statuses (e.g. "Availability Check", "Order Received").
        # The API will be called exactly ONCE at the end of this method with "Confirmed".
        res = super(SaleOrder, self.with_context(skip_api_sync=True)).action_confirm()
        parent_time = time.time() - parent_start
        _logger.info("⏱ Parent action_confirm took %.2fs", parent_time)

        # Preload references once (avoid repeated env.ref lookups)
        ref_start = time.time()
        delivery_group = self.env.ref("__export__.res_groups_80_412e5ec9", raise_if_not_found=False)
        procurement_group = self.env.ref("__export__.res_groups_79_862f912f", raise_if_not_found=False)
        activity_type = self.env.ref("mail.mail_activity_data_todo")
        _logger.info("⏱ Reference loading took %.2fs", time.time() - ref_start)

        delivery_recipients, procurement_recipients = [], []
        delivery_partners, procurement_partners = [], []

        if delivery_group:
            delivery_partners = delivery_group.users.mapped("partner_id")
            delivery_recipients = [e for e in delivery_partners.mapped("email") if e]

        if procurement_group:
            procurement_partners = procurement_group.users.mapped("partner_id")
            procurement_recipients = [e for e in procurement_partners.mapped("email") if e]

        for order in self:
            order_start = time.time()
            _logger.info("⚙️ Processing Sale Order %s", order.name)

            # Send Custom Order Confirmation (PDF Only) Email to Customer
            template = self.env['mail.template'].search([
                ('name', '=', 'Custom Order Confirmation (PDF Only)'),
                ('model_id.model', '=', 'sale.order')
            ], limit=1)
            
            if template:
                try:
                    # ⚠️ CRITICAL: DO NOT add recipient_ids here.
                    email_values = {
                        'email_to': order.partner_id.email,
                        'email_from': self.env.user.email or self.env.company.email,
                    }
                    
                    # 1. Generate the email record in Odoo and capture its ID
                    mail_id = template.send_mail(order.id, force_send=False, email_values=email_values)
                    
                    if mail_id:
                        # 2. Instantly push ONLY this specific email to the SMTP server
                        self.env['mail.mail'].sudo().browse(mail_id).send()
                        _logger.info("✉️ Auto-Confirmation Email sent successfully to %s for Order: %s", order.partner_id.email, order.name)
                        
                except Exception as e:
                    _logger.error("❌ Failed to send Auto-Confirmation Email for Order: %s. Error: %s", order.name, str(e))

            order.action_send_product_to_api()
            _logger.info("🌐 [CONFIRM] Sending current statuses to API for order %s", order.name)
            try:
                order._call_augmont_status_api(order.sdk_augmont_status)
            except Exception as _api_err:
                _logger.error(
                    "❌ [action_confirm] Augmont API call failed for %s: %s",
                    order.name, _api_err
                )


            # 1️⃣ Assign picking type
            picking_start = time.time()
            picking_type_map = {"mumbai": 2, "surat": 14}
            picking_type_id = picking_type_map.get(order.location)
            if picking_type_id:
                valid_pickings = order.picking_ids.filtered(lambda p: p.state not in ("done", "cancel"))
                valid_pickings.write({'picking_type_id': picking_type_id})
                for picking in valid_pickings:
                    _logger.info(
                        "✅ Updated picking %s to operation type %s for SO %s",
                        picking.name, picking_type_id, order.name
                    )
            _logger.info("⏱ Picking type assignment took %.2fs", time.time() - picking_start)

            # 2️⃣ Remove unwanted moves
            moves_start = time.time()
            unwanted_moves = order.picking_ids.move_ids_without_package.filtered(
                lambda m: m.sale_line_id and m.sale_line_id.is_not_available
            )
            if unwanted_moves:
                move_ids = unwanted_moves.ids
                _logger.info("🗑 Removing %d unwanted stock moves for SO %s", len(move_ids), order.name)
                unwanted_moves._action_cancel()
                unwanted_moves.unlink()
            _logger.info("⏱ Unwanted moves removal took %.2fs", time.time() - moves_start)

            # 3️⃣ Notify delivery team (async mail queue)
            delivery_start = time.time()
            if delivery_recipients:
                subject = _("Sale Order %s confirmed — Follow up on material outward") % order.name
                body_html = Markup(f"""
                    <p>Dear Delivery Team,</p>
                    <p>The sale order <strong>{order.name}</strong> has been confirmed.</p>
                    <p>Please follow up on the material outward process.</p>
                """)

                #Sending bulk emails to the internal team.
                # self.env["mail.mail"].create({
                #     "subject": subject,
                #     "body_html": body_html,
                #     "email_from": self.env.user.partner_id.email,
                #     "email_to": ",".join(delivery_recipients),
                # })

                # Post message and create activities on picking
                if order.picking_ids:
                    first_picking = order.picking_ids[0]
                    first_picking.message_post(
                        subject=subject, 
                        body=body_html,
                        subtype_xmlid='mail.mt_note' 
                    )

                    # Create activities for delivery team users on picking record
            #         for partner in delivery_partners:
            #             self.env['mail.activity'].create({
            #                 'res_model_id': self.env['ir.model']._get_id('stock.picking'),
            #                 'res_id': first_picking.id,
            #                 'activity_type_id': activity_type.id,
            #                 'summary': "Follow up on outward process",
            #                 'note': body_html,
            #                 'user_id': partner.user_ids[:1].id if partner.user_ids else False,
            #                 'date_deadline': fields.Date.today(),
            #             })
            # _logger.info("⏱ Delivery team notification took %.2fs", time.time() - delivery_start)

            # 4️⃣ Handle zero stock products
            stock_start = time.time()
            zero_stock_products = order.order_line.filtered(
                lambda l: l.product_id.type == "consu" and l.product_id.qty_available <= 0
            ).mapped("product_id")

            if zero_stock_products and procurement_recipients:
                product_info = "".join(
                    f"<li><strong>{p.display_name}</strong> — Qty: {p.qty_available}</li>"
                    for p in zero_stock_products
                )
                body_html = Markup(f"""
                    <p>Dear Procurement Team,</p>
                    <p>The following products have zero or insufficient stock:</p>
                    <ul>{product_info}</ul>
                """)
                subject = "⚠️ Stock Alert: Zero On-hand Quantity"

                
                #Sending bulk emails to the internal team.
                # self.env["mail.mail"].create({
                #     "mail_server_id": 9,
                #     "email_from": self.env.user.partner_id.email,
                #     "subject": subject,
                #     "body_html": body_html,
                #     "email_to": ",".join(procurement_recipients),
                # })

                order.message_post(
                    subject=subject, 
                    body=body_html,
                    subtype_xmlid='mail.mt_note'
                )

                # Create activities for procurement team on sale order
                # for partner in procurement_partners:
                #     self.env["mail.activity"].sudo().create({
                #         "res_model_id": self.env["ir.model"]._get_id("sale.order"),
                #         "res_id": order.id,
                #         "activity_type_id": activity_type.id,
                #         "summary": "Stock Alert — Restock Required",
                #         "note": body_html,
                #         "user_id": partner.user_ids[:1].id if partner.user_ids else False,
                #         "date_deadline": fields.Date.today(),
                #     })
            _logger.info("⏱ Zero stock handling took %.2fs", time.time() - stock_start)

            _logger.info("⏱ Finished SO %s in %.2fs", order.name, time.time() - order_start)

        # Send all queued emails after loop
        # mail_start = time.time()
        # queued_mails = self.env["mail.mail"].search([("state", "=", "outgoing")])
        # if queued_mails:
        #     _logger.info("📧 Sending %d queued emails...", len(queued_mails))
        #     queued_mails.send()
        #     _logger.info("⏱ Email sending took %.2fs", time.time() - mail_start)

        total_time = time.time() - start_time
        _logger.info("✅ Completed action_confirm for %d SOs in %.2fs", len(self), total_time)
        
        # Log if there's a significant time difference
        if total_time > 5:
            _logger.warning("⚠️ action_confirm took longer than expected. Check for:")
            _logger.warning("   - Database commit time")
            _logger.warning("   - Other workflow triggers")
            _logger.warning("   - Automated actions")
        
        return res


    def action_mark_not_available(self):
        """
        Mark order as 'Not available' when products cannot be procured.
        Allowed from: Diamond Booked, Confirmed
        Result: Status changes to 'Not available', then auto-cancels
        """
        for order in self:
            _logger.info(
                "⚠️ Marking as Not available | Order: %s | Current: %s",
                order.name,
                order.sdk_augmont_status
            )
            
            # Validate current status
            if order.sdk_augmont_status not in ['Diamond Booked', 'Confirmed']:
                raise UserError(
                    _(
                        "Cannot mark as 'Not available'\n\n"
                        "Current status: %s\n\n"
                        "This action is only allowed from:\n"
                        "  • Diamond Booked\n"
                        "  • Confirmed"
                    ) % order.sdk_augmont_status
                )
            
            # Update to Not available
            order.write({'sdk_augmont_status': 'Not available'})
            
            order.message_post(
                body=_("Order marked as <b>Not available</b> by %s<br/>Reason: Products cannot be procured") % self.env.user.name,
                subject="Order Not Available"
            )
            
            # Sync with API
            try:
                order._call_augmont_status_api('Not available')
                _logger.info("✅ Augmont API synced | Order: %s | Status: Not available", order.name)
            except Exception as e:
                _logger.error(
                    "❌ Augmont API sync failed | Order: %s | Status: Not available | Error: %s",
                    order.name, e
                )
                # Create retry record
                self.env['augmont.api.retry'].create({
                    'sale_order_id': order.id,
                    'status': 'Not available',
                    'error_message': str(e),
                })
            
            # Auto-cancel after marking as not available
            _logger.info("🔄 Auto-cancelling order after 'Not available' | Order: %s", order.name)
            order.write({'sdk_augmont_status': 'Cancelled'})
            
            order.message_post(
                body=_("Order automatically <b>Cancelled</b> as products are not available"),
                subject="Order Cancelled"
            )
            
            try:
                order._call_augmont_status_api('Cancelled')
                _logger.info("✅ Augmont API synced | Order: %s | Status: Cancelled", order.name)
            except Exception as e:
                _logger.error("❌ Augmont API sync failed | Order: %s | Status: Cancelled | Error: %s", order.name, e)
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Order Marked as Not Available'),
                'message': _('The order has been marked as not available and cancelled.'),
                'type': 'warning',
                'sticky': False,
            }
        }

    def action_cancel(self):
        """
        Override action_cancel to:
        1. Update ALL order line availability_status to 'cancelled'
        2. Call Odoo parent cancel (sets state to 'cancel')
        3. Set sdk_augmont_status to 'Cancelled'
        
        NOTE: In Odoo 18, clicking Cancel button calls sale.order/action_cancel directly
        (no wizard). This override handles that path.
        """
        for order in self:
            _logger.info("🚨 CANCEL ACTION TRIGGERED for order %s", order.name)
            
            # Update ALL line statuses to 'cancelled'
            for line in order.order_line:
                old_status = line.availability_status
                line.write({'availability_status': 'cancelled'})
                _logger.info(
                    "❌ CANCEL: Line %s | %s → 'cancelled'",
                    line.order_number or line.product_id.name,
                    old_status
                )
        
        # Call parent to handle Odoo's cancel logic (sets state → 'cancel')
        result = super(SaleOrder, self).action_cancel()
        
        # Explicitly set order status to Cancelled after parent cancel
        for order in self:
            # Use invalidate + direct write to ensure compute doesn't overwrite
            order.invalidate_recordset(['sdk_augmont_status'])
            order.write({'sdk_augmont_status': 'Cancelled'})
            _logger.info("❌ CANCEL: Order %s → sdk_augmont_status: Cancelled", order.name)
        
        return result

    
    @api.depends('payment_ids')
    def _compute_payment_count(self):
        for order in self:
            order.payment_count = len(order.payment_ids)
    
    @api.depends('payment_ids.state', 'payment_ids.amount', 'amount_total')
    def _compute_is_fully_paid(self):
        for order in self:
            # Filter only 'done' state payments
            paid_payments = order.payment_ids.filtered(lambda p: p.state == 'in_process') 
            total_paid = sum(paid_payments.mapped('amount'))
            order.is_fully_paid = total_paid >= order.amount_total and bool(paid_payments)
            print(order.is_fully_paid,"order.is_fully_paid")
    
    def create_sale_payment(self):
        for order in self:
            payment_vals = {
                'partner_id': order.partner_id.id,
                'amount': order.amount_total,
                'payment_type': 'inbound',
                'payment_method_id': order.env.ref('account.account_payment_method_manual_in').id,
                'partner_type': 'customer',
                'journal_id': order.env['account.journal'].search([('type', '=', 'bank')], limit=1).id,
                'date': fields.Date.today(),
                'sale_order_id': order.id,
            }
            print(payment_vals,"payment_vals")
            payment = self.env['account.payment'].create(payment_vals)
            print(payment,"payment")
            # payment.action_post()
            order.write({'payment_ids': [(4, payment.id)]})
            print(order.payment_ids)
    
    def action_view_payments(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Payments',
            'res_model': 'account.payment',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.payment_ids.ids)],
            'context': {'default_partner_id': self.partner_id.id}
        }
        

    def action_update_kyc_gst(self):
        """
        Update KYC (GST Update) via API
        """
        self.ensure_one()

        Param = self.env['ir.config_parameter'].sudo()
        url = Param.get_param('base_augmont_url')

        if not url:
            raise UserError(_('KYC API Base URL is not configured. Please configure it in System Parameters with key: kyc_api.base_url'))
        
        # Validate invoice number
        if not self.name:
            raise UserError(_('Sale Order number is required for KYC update.'))
        
        # Prepare GST update data
        # gst_updates = self._prepare_gst_updates()
        ###################################################

        """
        Prepare GST updates data with validation
        Only send GST (SGST/CGST) OR IGST, not both
        Sends tax rates, not tax amounts
        """
        self.ensure_one()
        
        gst_updates = {}
        # Get GST tax rates from order lines
        sgst_rate = 0.0
        cgst_rate = 0.0
        igst_rate = 0.0
        
        for line in self.order_line:
            for tax in line.tax_id:
                tax_name = tax.description.upper()
                
                if 'GST' in tax_name:
                    sgst_rate = tax.amount / 2
                    cgst_rate = tax.amount /2 

                # elif 'GST' in tax_name: 
                elif 'IGST' in tax_name:
                    igst_rate = tax.amount

                else:
                    pass
            
            # Break after finding taxes (assuming same tax rate for all lines)
            if sgst_rate or cgst_rate or igst_rate:
                break
        
        # Get country code from partner
        country_code = self.partner_id.country_id.phone_code or "91"
        
        # Validation: Send either GST (SGST+CGST) OR IGST, not both
        # if igst_rate > 0 and (sgst_rate > 0 or cgst_rate > 0):
        #     raise ValidationError(_(
        #         'Invalid GST configuration: Cannot send both IGST and GST (SGST/CGST) together. '
        #         'Please verify the tax configuration on order lines.'
        #     ))
        
        # If IGST is present, send only IGST rate
        if igst_rate > 0:
            gst_updates["igst"] = round(igst_rate, 2)
            gst_updates["countryCode"] = country_code
            _logger.info(f"Sending IGST rate: {igst_rate}% for inter-state transaction")
        
        # If SGST/CGST is present, send only GST rates
        elif sgst_rate > 0 or cgst_rate > 0:
            gst_updates["sgst"] = round(sgst_rate, 2)
            gst_updates["cgst"] = round(cgst_rate, 2)
            gst_updates["igst"] = 0
            gst_updates["countryCode"] = country_code
            _logger.info(f"Sending SGST rate: {sgst_rate}%, CGST rate: {cgst_rate}% for intra-state transaction")
        
        else:
            # No GST applicable
            gst_updates["sgst"] = 0
            gst_updates["cgst"] = 0
            gst_updates["igst"] = 0
            gst_updates["countryCode"] = country_code
            _logger.warning(f"No GST rates found for Sale Order: {self.name}")
        
        # return gst_updates
        
        # Prepare user data
        # user_data = self._prepare_user_data()

        """
        Prepare user data from partner information
        """
        # self.ensure_one()
        
        # Fetch current logged-in user and partner
        current_user = self.env.user
        partner = current_user.partner_id

        user_data = {
            "firstName": current_user.name or "N/A",
            "lastName": "",
            "email": current_user.login or "noreply@example.com",
            # "email": "john.doe@example.com",
            "mobileNumber": current_user.partner_id.mobile or current_user.partner_id.phone or "",
            "idNumber": current_user.operator_user_id or "",
            
        }
        
        # return user_data
        
        # Prepare request payload
        payload = {
            "invoiceNumber": self.sdk_augmont_number,
            "gstUpdates": gst_updates,
            "user": user_data
        }
        
        _logger.info(f"Sending KYC GST Update for Sale Order: {self.sdk_augmont_number}")
        _logger.info(f"Payload>>>>>>>>>: {payload}")
        
        try:
            # Make API call
            endpoint = f"{url}/api/v1/odoo/gst/update"
            headers = {
            "Authorization": (
                "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJtZXJjaGFudElkIjoiZjA3NDA3YzItZTI1ZS00YjI2LTk1MWUtODliZGQxZjI4YmQ2Iiwic2hvcnROYW1lIjoib2RvbyIsImlhdCI6MTc2MDYxNjU3OCwiZXhwIjoyMDc2MTkyNTc4fQ.XpKKN78VVCezg6BrEhQYxvTs1FTmES59pyrp3zFpmdI"
            ),
            "Content-Type": "application/json",
        }
            
            response = requests.post(endpoint, json=payload, headers=headers, timeout=30)
            
            # Handle response
            if response.status_code == 200:
                response_data = response.json()
                _logger.info(f"GST Response Data: {response_data}")

                # self._handle_success_response(response_data)
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Success'),
                        'message': _('KYC GST updated successfully!'),
                        'type': 'success',
                        'sticky': False,
                    }
                }
            else:
                # self._handle_error_response(response)
                raise UserError(_(f'API Error: {response.status_code} - {response.text}'))
                
        except requests.exceptions.RequestException as e:
                raise ValidationError(f"API connection failed: {str(e)}")
    
    def action_send_product_to_api(self):
        Param = self.env['ir.config_parameter'].sudo()
        url = Param.get_param('base_augmont_url')
        url = f"{url}/api/v1/odoo/product/add"
        _logger.info(f"🔹URL: {url}")
        headers = {
            "Authorization": (
                "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJtZXJjaGFudElkIjoiZjA3NDA3YzItZTI1ZS00YjI2LTk1MWUtODliZGQxZjI4YmQ2Iiwic2hvcnROYW1lIjoib2RvbyIsImlhdCI6MTc2MDYxNjU3OCwiZXhwIjoyMDc2MTkyNTc4fQ.XpKKN78VVCezg6BrEhQYxvTs1FTmES59pyrp3zFpmdI"
            ),
            "Content-Type": "application/json",
        }
        current_user = self.env.user


        for order in self:
            if not order.order_line:
                raise ValidationError(_("No order lines found."))

            # Separate lines by is_custom_product flag (only active lines: diamond_booked or confirmed)
            normal_lines = order.order_line.filtered(lambda l: l.product_template_id.website_product_id and l.availability_status in ['diamond_booked', 'confirmed'] and l.is_custom_product == True)
            custom_lines = order.order_line.filtered(lambda l: not l.product_template_id.website_product_id and l.availability_status in ['diamond_booked', 'confirmed'] and l.is_custom_product == True)
            _logger.info(f"🔹normal_linesssss: {normal_lines}")
            _logger.info(f"🔹custom_linesssss: {custom_lines}")


            # -------------------------
            # PAYLOAD 1 → Existing Products (is_custom_product = False)
            # -------------------------
            payload1_data = []
            for line in normal_lines:
                if not line.product_id.website_product_id:
                    raise ValidationError(_("Website product ID not found for line: %s") % (line.product_id.display_name))
                payload1_data.append({
                    "id": str(line.product_id.website_product_id),
                })

            # -------------------------
            # PAYLOAD 2 → Custom Products (is_custom_product = True)
            # -------------------------
            payload2_data = []
            for line in custom_lines:
                payload2_data.append({
                    "stockNum": line.stock_number or "",
                    # "stockCertNum": "",
                    "certNumber": line.certificate or "",
                    "shape": line.shapes or "",
                    "weight": float(line.carat_weight or 0),
                    "clarity": line.clarity or "",
                    "cut": line.cut or "",
                    "color": line.color or "",
                    "pricePerCarat": float(line.final_price or 0),
                    "finalPrice": float(line.price_unit or 0),
                    "lab": line.product_id.labs or "",
                    "country": line.product_id.country_id.name or "",
                })

            # -------------------------
            # Combine both product lists in a single payload
            # -------------------------
            product_data_list = payload1_data + payload2_data

            if not product_data_list:
                # raise ValidationError(_("No valid product data found to send."))
                return

            payload = {
                "invoiceNumber": order.sdk_augmont_number,
                "productData": product_data_list,
                "user": {
                   "firstName": order.partner_id.name or "",
                    "lastName": "",
                     "email": order.partner_id.email or "",
                      "mobileNumber": order.partner_id.phone or "",
                },
                "customer": {
                   "firstName": order.partner_id.name or "",
                    "lastName": "",
                     "email": order.partner_id.email or "",
                      "mobileNumber": order.partner_id.phone or "",
                },
            }

            # raise ValidationError(str(payload))
            try:
                _logger.info(f"🔹 Sending Augmont product payload: {json.dumps(payload)}")
                response = requests.post(url, headers=headers, data=json.dumps(payload), timeout=20)

                if response.status_code == 200:
                    response_data = response.json()
                    _logger.info(f"✅ API Success for order {order.name}: {response_data}")

                    # ✅ Extract all line-level orderUniqueIds
                    data_list = response_data.get("data", {}).get("data", [])
                    if data_list and isinstance(data_list, list):
                        for item in data_list:
                            order_unique_id = item.get("orderUniqueId")
                            product_details = item.get("productDetails", {})

                            pricePerCaratMargin = product_details.get("pricePerCaratMargin")
                            finalPriceMargin = product_details.get("finalPriceMargin")
                            _logger.info("pricePerCaratMargin>>>" ,pricePerCaratMargin)
                            _logger.info("finalPriceMargin>>>" ,finalPriceMargin)

                            
                            stock_num = item.get("productDetails", {}).get("stockNum")

                            # Match each API item with Odoo line item (based on your field)
                            line = order.order_line.filtered(lambda l: l.stock_number == stock_num)
                            if line and order_unique_id:
                                if not line.order_number:
                                    line.order_number = str(order_unique_id)
                                    line.price_unit = finalPriceMargin
                                    line.final_price = pricePerCaratMargin 

                                    _logger.info(f"🔸 Stored orderUniqueId {order_unique_id} for line {line.name}")

                    _logger.info(f"✅ API Success for order {order.name}: {response.text}")
                    order.message_post(
                        body="✅ Product data successfully sent to Augmont API.",
                        subject=f"API Response: {response.text}",
                    )
                else:
                    _logger.error(f"❌ API Error {response.status_code}: {response.text}")
                    raise ValidationError(f"API Error {response.status_code}: {response.text}")

            except requests.exceptions.RequestException as e:
                raise ValidationError(f"API connection failed: {str(e)}")

        return True
    
    @api.depends('state', 'invoice_status', 'payment_ids.state')
    def _compute_sdk_augmont_status(self):
        for order in self:
            old_status = order.sdk_augmont_status
            status = False
            
            # Define status based on conditions (order matters - most specific first)
            if order.state == 'cancel':
                status = 'Cancelled'
            elif order.state == 'draft':
                status = 'Diamond Booked'
            elif order.invoice_status == 'invoiced' and order.delivery_status == 'full':
                status = 'Order Completed'
            elif order.picking_ids and any(pick.return_id for pick in order.picking_ids):
                status = 'Return of Order'
            elif order.delivery_status == 'full':
                status = 'Delivered'
            elif order.payment_ids and any(payment.state == 'paid' for payment in order.payment_ids):
                status = 'Payment Completed'
            elif order.payment_ids and any(payment.state in ['draft', 'in_process'] for payment in order.payment_ids):
                status = 'Payment Pending'
            elif order.delivery_status == 'started':
                status = 'In QC process'
            elif order.state == 'sale':
                status = 'Confirmed'
            
            # Update status
            order.sdk_augmont_status = status
            
            # Trigger API call if status changed
            if old_status and old_status != status:
                # order.previous_sdk_augmont_status = old_status
                order._call_augmont_status_api(status)
    
 
    def _prepare_augmont_status_data(self, status, comment=None, order_number=None):
        """Prepare data for Augmont API for a specific sale order line"""
        self.ensure_one()

        # Get current login user
        user = self.env.user

        # Ensure UTR is always a string (API requires string, not None/False)
        utr_value = str(self.utr_number) if self.utr_number else ""
        
        # Only include UTR when status is Payment Completed
        include_utr = (status == "Payment Completed")
        
        # Only include courier fields when status is Dispatched or Re - Dispatched
        include_courier = (status in ["Dispatched", "Re - Dispatched"])

        data = {
            "ordersStatus": [{
                "id": order_number,  # fetched from sale.order.line
                "status": status,
                "utr": utr_value if include_utr else "",
                "courierPartnerName": str(self.courier_partner_name) if (include_courier and self.courier_partner_name) else "",
                "trackingNumber": str(self.tracking_number) if (include_courier and self.tracking_number) else "",
                "comment": comment or f"Order status updated to {status} by Odoo system",
                "user": {
                    "firstName": user.name.split(' ')[0] if user.name else '',
                    "lastName": ' '.join(user.name.split(' ')[1:]) if user.name and len(user.name.split(' ')) > 1 else '',
                    "email": user.email or '',
                    "mobileNumber": user.partner_id.mobile or user.partner_id.phone or '',
                }
            }]
        }
        return data

    def _call_augmont_status_api(self, new_status, old_status=None):
        self.ensure_one()

        # Offline orders live entirely inside Odoo — they must never sync to the
        # Augmont website API (their AUG-OFF invoice number is unknown there).
        if self.order_source == 'offline':
            _logger.info(
                "🔕 [API SKIP] Order: %s | offline order — no website sync",
                self.name
            )
            return

        AUGMONT_VALID_API_STATUSES = frozenset([
            'Diamond Booked', 'Confirmed', 'Not available', 'Cancelled',
            'In QC process', 'QC Fail', 'Payment Pending', 'Payment Completed',
            'Dispatched', 'Return of Order', 'Re - Dispatched', 'Delivered',
            'Order Completed',
        ])
        if not new_status:
            _logger.info(
                "🔕 [API SKIP] Order: %s | Empty status — nothing to send",
                self.name
            )
            return
        # Log a note when the aggregate is internal (informational only — do NOT return)
        if new_status not in AUGMONT_VALID_API_STATUSES:
            _logger.info(
                "📊 [API NOTE] Order: %s | Aggregate '%s' is Odoo-internal. "
                "Proceeding with per-line status updates so every order_number "
                "gets its own correct status sent to the website.",
                self.name, new_status
            )

        # ── LINE-STATUS → AUGMONT API STATUS MAP ────────────────────────────────
 
        _LINE_TO_AUGMONT = {
            'diamond_booked':    'Diamond Booked',
            'confirmed':         'Confirmed',
            'not_available':     'Not available',
            'cancelled':         'Cancelled',
            'in_qc_process':     'In QC process',
            'qc_fail':           'QC Fail',
            'payment_pending':   'Payment Pending',
            'payment_completed': 'Payment Completed',
            'dispatched':        'Dispatched',
            'return_of_order':   'Return of Order',
            're_dispatched':     'Re - Dispatched',
            'delivered':         'Delivered',
            'order_completed':   'Order Completed',
        }

        sale_order_lines = self.order_line.filtered(lambda l: l.order_number)
        if not sale_order_lines:
            _logger.info(
                "[API SKIP] Order: %s | No order lines with order_number",
                self.name
            )
            return

        try:
            Param = self.env['ir.config_parameter'].sudo()
            base_url = Param.get_param('base_augmont_url')
            api_url = f"{base_url}/api/v1/odoo/orderStatusUpdate"

            headers = {
                "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJtZXJjaGFudElkIjoiZjA3NDA3YzItZTI1ZS00YjI2LTk1MWUtODliZGQxZjI4YmQ2Iiwic2hvcnROYW1lIjoib2RvbyIsImlhdCI6MTc2MDYxNjU3OCwiZXhwIjoyMDc2MTkyNTc4fQ.XpKKN78VVCezg6BrEhQYxvTs1FTmES59pyrp3zFpmdI",
                "Content-Type": "application/json",
            }

            _logger.info("=" * 70)
            _logger.info(
                "▶▶ ODOO → WEBSITE | %s | STATUS UPDATE | Order: %s | "
                "Invoice: %s | Aggregate: %s → %s",
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                self.name,
                self.sdk_augmont_number or 'N/A',
                old_status or 'None',
                new_status,
            )

            for line in sale_order_lines:
                order_number = line.order_number

                # ── Derive per-line Augmont status from the LINE's own availability ──
                mapped = _LINE_TO_AUGMONT.get(line.availability_status)
                if mapped:
                    final_status = mapped
                    _logger.info(
                        "  └─ Line %s: line.availability_status='%s' → API='%s'",
                        order_number, line.availability_status, final_status
                    )
                else:
                    # line.availability_status is unrecognised (edge case); use
                    # the explicitly passed new_status as the fallback only if it
                    # is a valid API status — otherwise skip this line.
                    if new_status in AUGMONT_VALID_API_STATUSES:
                        final_status = new_status
                        _logger.info(
                            "  └─ Line %s: no mapping for '%s' — using passed status '%s'",
                            order_number, line.availability_status, final_status
                        )
                    else:
                        _logger.info(
                            "🔕 [LINE SKIP] Line %s | availability_status='%s' has no mapping "
                            "and fallback '%s' is also internal — skipping this line",
                            order_number, line.availability_status, new_status
                        )
                        continue

                # ── PER-LINE GUARD: Skip lines whose final status is internal ──────
                # This is the safety net that was previously an ORDER-LEVEL guard.
                # Moving it here allows all other lines (with valid statuses) to still
                # be sent even when one line has an unusual/internal status.
                if final_status not in AUGMONT_VALID_API_STATUSES:
                    _logger.info(
                        "🔕 [LINE SKIP] Line %s | final_status='%s' not a valid API status — skipping",
                        order_number, final_status
                    )
                    continue

                data = self._prepare_augmont_status_data(final_status, order_number=order_number)

                _logger.info(
                    "▶▶ ODOO → WEBSITE | Line: %s | %s → %s",
                    order_number,
                    line.availability_status,
                    final_status
                )
                _logger.info(
                    "   METHOD: POST | URL: %s | Payload: %s",
                    api_url, json.dumps(data)
                )

                response = requests.post(api_url, json=data, headers=headers, timeout=30)

                if response.status_code == 200:
                    response_data = response.json()
                    _logger.info(
                        "▶▶ ODOO → WEBSITE | STATUS UPDATE RESPONSE | Line: %s | HTTP 200 ✅ | Response: %s",
                        order_number,
                        json.dumps(response_data)
                    )
                    _logger.info("=" * 70)

                    status_change = f"{old_status} → {final_status}" if old_status else final_status

                    self.message_post(
                        body=Markup(
                            f"<p>Augmont status updated for line {order_number}:</p>"
                            f"<ul><li>Status: {status_change}</li>"
                            f"<li>Response: {response_data.get('message', 'Success')}</li></ul>"
                        ),
                        subject="Augmont Status Update"
                    )
                else:
                    _logger.error(
                        "▶▶ ODOO → WEBSITE | STATUS UPDATE RESPONSE | Line: %s | HTTP %s ❌ | Response: %s",
                        order_number,
                        response.status_code,
                        response.text
                    )
                    _logger.info("=" * 70)
                    error_text = response.text or ""

                    # 1. Same-status transition: website already has the correct status.
                    # 2. Diamond Booked → Diamond Booked (any error): when an order is
                    #    first created from the website, Odoo echoes "Diamond Booked"
                    #    back to the website, which the website may reject ("not found"
                    #    or same-status). The website already has Diamond Booked;
                    #    no action is needed and no chatter noise should appear.
                    is_same_status_error = (
                        "Status transition from" in error_text
                        and "is not allowed" in error_text
                        and f"from '{final_status}' to '{final_status}'" in error_text
                    )
                    is_db_to_db = (
                        final_status == "Diamond Booked"
                        and (not old_status or old_status == "Diamond Booked")
                    )
                    if is_same_status_error or is_db_to_db:
                        _logger.info(
                            "🔕 [SKIP] Line %s: suppressed — %s",
                            order_number,
                            "same-status transition" if is_same_status_error else "Diamond Booked → Diamond Booked (initial echo)"
                        )
                    else:
                        # Save failed API call for retry
                        retry_rec = self.env["augmont.api.retry"].create({
                            "order_id": self.id,
                            "order_line_id": line.id,
                            "sdk_augmont_number": self.sdk_augmont_number,
                            "order_number": order_number,
                            "payload_json": json.dumps(data),
                            "headers_json": json.dumps(headers),
                            "old_status": old_status or "",
                            "new_status": final_status,
                            "error_message": response.text,
                            "status_code": response.status_code,
                            "state": "pending",
                        })

                        _logger.info(
                            "🔄 [RETRY CREATED] ID: %s | Line: %s",
                            retry_rec.id,
                            order_number
                        )

                        # Log in chatter — Markup for proper HTML render
                        self.message_post(
                            body=Markup(
                                f"<p style='color:red;'><b>Augmont API FAILED</b> for line {order_number}</p>"
                                f"<p>Error: {response.text}</p>"
                            ),
                            subject="Augmont API Failure"
                        )

        except Exception as e:
            _logger.error(
                "💥 [API EXCEPTION] Order: %s | Error: %s",
                self.name,
                str(e)
            )

            # CREATE RETRY RECORD ON EXCEPTION
            retry_rec = self.env["augmont.api.retry"].create({
                "order_id": self.id,
                "order_line_id": line.id,
                "sdk_augmont_number": self.sdk_augmont_number,
                "order_number": order_number,
                "payload_json": json.dumps(data),
                "headers_json": json.dumps(headers),
                "old_status": old_status or "",
                "new_status": new_status or "",
                "error_message": str(e),
                "status_code": "EXCEPTION",
                "state": "pending",
            })

            _logger.info(
                f"[AUGMONT RETRY CREATED FROM EXCEPTION] ID: {retry_rec.id}"
            )

            # CHATTER LOG FOR EXCEPTION — Markup() so HTML renders correctly
            self.message_post(
                body=Markup(
                    f"<p style='color:red;'><b>Augmont API EXCEPTION</b></p>"
                    f"<p>Order Number: {order_number}</p>"
                    f"<p>Status: {old_status} → {new_status}</p>"
                    f"<p>Error: {str(e)}</p>"
                ),
                subject="Augmont API Connection Error"
            )

    def action_retry_augmont_failed(self):
        for order in self:
            retry_records = self.env["augmont.api.retry"].search([
                ("order_id", "=", order.id),
                ("state", "=", "pending")
            ])

            if not retry_records:
                order.message_post(body="<p>No pending Augmont retry records.</p>")
                continue

            _logger.info(f"[MANUAL RETRY] Triggered from Sale Order {order.name}")

            retry_records.retry_api_call()

            order.message_post(
                body=Markup(f"<p>Manual Augmont retry triggered for {len(retry_records)} records.</p>")
            )
    
    def action_update_augmont_status_manually(self):
        """Manual action to update status in Augmont"""
        self.ensure_one()
        if self.sdk_augmont_status:
            self._call_augmont_status_api(self.sdk_augmont_status)  # old_status not passed → treated as manual resend

    def action_confirm_wrapper(self):

        self.ensure_one()
        # Block confirmation when ANY line is still in
        # [Diamond Booked] OR [Not available] status.
        blocked_lines = self.order_line.filtered(
            lambda l: l.availability_status in ('diamond_booked', 'not_available')
        )
        if blocked_lines:
            order_numbers = ', '.join(
                l.order_number or '(no number)' for l in blocked_lines
            )
            raise UserError(
                f"Cannot confirm order {self.name}.\n\n"
                f"The following order number(s) are still in "
                f"[Diamond Booked] or [Not available] status:\n"
                f"{order_numbers}\n\n"
                f"Please update each order number to [Confirmed] or "
                f"[Cancelled] and click {{Save}} before confirming."
            )
        return self.action_confirm()

    def action_save_statuses(self):
        self.ensure_one()

        # ── Auto-QC: Check for lines needing confirmed → in_qc_process flow ──
        if not self.env.context.get('skip_auto_procurement'):
            lines_for_qc = self.order_line.filtered(
                lambda l: l.availability_status == 'in_qc_process'
                and l.vendor_id
                and not self.env['purchase.order'].search([
                    ('origin', '=', self.name),
                    ('partner_id', '=', l.vendor_id.id),
                    ('order_line.product_id', '=', l.product_id.id),
                ], limit=1)
            )
            if lines_for_qc:
                _logger.info(
                    "🔄 [AUTO-QC via Save] %d line(s) at in_qc_process without PO — "
                    "triggering auto-procurement for order %s",
                    len(lines_for_qc), self.name
                )
                lines_for_qc.with_context(
                    skip_auto_procurement=True,
                )._auto_create_procurement_for_qc()

        # This is the ONLY path that pushes availability_status changes
        # to the connected website. The native Odoo {Save manually} (floppy icon)
        # only persists to the Odoo database and does NOT call the website API.
        _logger.info(
            "🖱️ [SAVE BUTTON] {Save} button clicked for order %s | "
            "Pushing status '%s' to website API.",
            self.name, self.sdk_augmont_status
        )
        if self.sdk_augmont_status:
            try:
                self._call_augmont_status_api(self.sdk_augmont_status)
            except Exception as e:
                _logger.error(
                    "❌ [SAVE BUTTON] API call failed for %s: %s — "
                    "status changes are saved locally but NOT synced to website",
                    self.name, e
                )

        # When the admin manually sets ALL lines to [Cancelled] in the
        # Procurement module and clicks {Save}, the Order Status becomes
        # 'Cancelled' (computed by C11).  At that point Odoo's stage bar
        # should automatically advance to the "Cancelled" stage by calling
        # action_cancel() — exactly as it does via the {Cancel} button.
        #
        # Guard conditions:
        #  - sdk_augmont_status must be 'Cancelled'   (all lines are cancelled)
        #  - state must NOT already be 'cancel'       (avoid double-cancel)
        #  - state must NOT be 'done'                 (completed orders untouched)
        # ─────────────────────────────────────────────────────────────────────
        if (self.sdk_augmont_status == 'Cancelled'
                and self.state not in ('cancel', 'done')):
            try:
                _logger.info(
                    "🔄 [AUTO-CANCEL via Save] Order %s: all lines Cancelled → "          "calling action_cancel() to advance stage bar.",
                    self.name
                )
                self.sudo().with_context(from_website_api=False).action_cancel()
                _logger.info(
                    "✅ [AUTO-CANCEL via Save] Order %s: state → %s | "                   "Stage bar moved to Cancelled.",
                    self.name, self.state
                )
            except Exception as e:
                _logger.error(
                    "❌ [AUTO-CANCEL via Save] Failed to auto-cancel %s: %s",
                    self.name, e
                )

    @api.model_create_multi
    def create(self, vals_list):
        """Override create to set initial status.
        Do NOT call the API when the order is created from the
        website (is_website=True or from_website_api context).  The website
        ALREADY has the 'Diamond Booked' status — calling the API to set it
        again causes:
          HTTP 400 "Status transition from 'Diamond Booked' to 'Diamond Booked'
                    is not allowed for order order_unique_id <id>"
        and creates a spurious retry record that the cron then replays every
        5 minutes forever.
        """
        # ── AUTO-SALESPERSON: Pre-fill user_id from partner's existing Salesperson field ──
        for vals in vals_list:
            if not vals.get('user_id') and vals.get('partner_id'):
                partner = self.env['res.partner'].browse(vals['partner_id'])
                sp = partner.user_id
                if sp and sp.active:
                    vals['user_id'] = sp.id
                    _logger.info(
                        "🧑‍💼 [AUTO-SALESPERSON/CREATE] Injecting salesperson '%s' "
                        "from partner.user_id for customer '%s' into new order vals.",
                        sp.name, partner.name
                    )

        orders = super().create(vals_list)
        for order, vals in zip(orders, vals_list):
            from_website = (
                self.env.context.get('from_website_api')
                or vals.get('is_website', False)
            )
            if order.sdk_augmont_status and order.sdk_augmont_number and not from_website:
                _logger.info(
                    "🌐 [CREATE] Calling API for new order %s | Status: %s",
                    order.name, order.sdk_augmont_status
                )
                order._call_augmont_status_api(order.sdk_augmont_status)
            elif from_website:
                _logger.info(
                    "⏭️ [CREATE] Skipping API call for website-originated order %s "
                    "(website already has the status — no need to echo it back)",
                    order.name
                )
        return orders


    def _safe_write_augmont_status(self, new_status, skip_api=False, old_status=None):
        """
        Write sdk_augmont_status to the DB via direct SQL (bypasses the
        Odoo 18 sale_stock pre_order_line_qty UnboundLocalError).

        old_status can be:
          - a string: used for all orders in self
          - a dict {order.id: status}: per-order old status (from write() method)
          - None: reads current value from DB

        Also:
        - Updates corresponding line availability_status values (so the
          Availability column in Procurement always reflects the Order Status).
        - Calls the Augmont website API unless skip_api=True.
        """
        # Mapping: Order Status → individual line availability_status
        ORDER_TO_LINE_STATUS = {
            'Confirmed':          'confirmed',
            'Not available':      'not_available',
            'In QC process':      'in_qc_process',
            'QC Fail':            'qc_fail',
            'Payment Pending':    'payment_pending',
            'Payment Completed':  'payment_completed',
            'Dispatched':         'dispatched',
            'Return of Order':    'return_of_order',
            'Re - Dispatched':    're_dispatched',
            'Delivered':          'delivered',
            'Order Completed':    'order_completed',
            'Cancelled':          'cancelled',
        }

        # Workflow order used to prevent regressing lines to an earlier status
        STATUS_WORKFLOW_ORDER = [
            'diamond_booked', 'confirmed', 'in_qc_process', 'payment_pending',
            'payment_completed', 'dispatched', 're_dispatched',
            'return_of_order', 'delivered', 'order_completed',
        ]
        # Statuses that must never be overwritten by a forward progression
        TERMINAL_LINE_STATUSES = {'cancelled', 'not_available', 'qc_fail'}

        from_website_api = self.env.context.get('from_website_api', False)

        for order in self:
            # Resolve old status for this order
            if isinstance(old_status, dict):
                _old = old_status.get(order.id) or order.sdk_augmont_status
            elif old_status is not None:
                _old = old_status
            else:
                _old = order.sdk_augmont_status

            # ── 1. Write sdk_augmont_status directly via SQL ─────────────────
            self.env.cr.execute(
                "UPDATE sale_order SET sdk_augmont_status = %s WHERE id = %s",
                (new_status, order.id)
            )
            order.invalidate_recordset(['sdk_augmont_status'])
            _logger.info(
                "💾 [SAFE STATUS] Order %s: %s → %s (SQL direct write)",
                order.name, _old, new_status
            )

            CYCLIC_LOGISTICS = frozenset(['dispatched', 're_dispatched', 'return_of_order'])

            target_line_status = ORDER_TO_LINE_STATUS.get(new_status)
            if target_line_status:
                if target_line_status in CYCLIC_LOGISTICS:
                    # For logistics cycle: update any line currently in the cycle
                    lines_to_update = order.order_line.filtered(
                        lambda l: (
                            l.availability_status not in TERMINAL_LINE_STATUSES
                            and l.availability_status in CYCLIC_LOGISTICS
                        )
                    )
                    _logger.info(
                        "🔄 [SAFE STATUS] Cyclic logistics update | "
                        "Target: '%s' | %d line(s) eligible (from %s) for order %s",
                        target_line_status, len(lines_to_update), CYCLIC_LOGISTICS, order.name
                    )
                else:
                    target_idx = (
                        STATUS_WORKFLOW_ORDER.index(target_line_status)
                        if target_line_status in STATUS_WORKFLOW_ORDER else -1
                    )
                    lines_to_update = order.order_line.filtered(
                        lambda l: (
                            l.availability_status not in TERMINAL_LINE_STATUSES
                            and (
                                target_idx == -1
                                or (
                                    l.availability_status in STATUS_WORKFLOW_ORDER
                                    and STATUS_WORKFLOW_ORDER.index(l.availability_status) < target_idx
                                )
                            )
                        )
                    )

                if lines_to_update:
                    _logger.info(
                        "🔄 [SAFE STATUS] Syncing %d line(s) → '%s' for order %s",
                        len(lines_to_update), target_line_status, order.name
                    )
                    lines_to_update.with_context(
                        from_quality_module=True,   # prevent auto-confirm loop
                        skip_api_sync=True,         # API called at order level below
                    ).write({'availability_status': target_line_status})

            # ──  Call the website API ──────────────────────────────────────
            if not skip_api and not from_website_api and _old != new_status:
                try:
                    order._call_augmont_status_api(new_status, _old)
                except Exception as _api_err:
                    _logger.error(
                        "❌ [SAFE STATUS] API sync failed | Order: %s | %s → %s | %s",
                        order.name, _old, new_status, _api_err
                    )

    def write(self, vals):

        in_compute_ctx = self.env.context.get('_in_compute_augmont_status', False)
        if in_compute_ctx:
            # Safe path: read from DB to avoid triggering ORM recompute
            ids = [o.id for o in self if isinstance(o.id, int)]
            if ids:
                self.env.cr.execute(
                    "SELECT id, sdk_augmont_status FROM sale_order WHERE id = ANY(%s)",
                    (ids,)
                )
                old_statuses = {row[0]: row[1] for row in self.env.cr.fetchall()}
            else:
                old_statuses = {}
        else:
            old_statuses = {order.id: order.sdk_augmont_status for order in self}
        from_website_api = self.env.context.get('from_website_api', False)
        skip_api_sync   = self.env.context.get('skip_api_sync', False)

        # ── Extract sdk_augmont_status from vals (prevents sale_stock crash) ──
        sdk_status_new = vals.pop('sdk_augmont_status', None)

        # ── UTR → auto payment_completed line update ─────────────────────────
        if 'utr_number' in vals and vals.get('utr_number') and not from_website_api:
            for order in self:
                _logger.info(
                    "💳 [UTR ENTERED] Order: %s | UTR: %s",
                    order.name, vals['utr_number']
                )
                payment_pending_lines = order.order_line.filtered(
                    lambda l: l.availability_status == 'payment_pending'
                )
                if payment_pending_lines:
                    _logger.info(
                        "💳 [UTR] Updating %d lines: payment_pending → payment_completed",
                        len(payment_pending_lines)
                    )
                    payment_pending_lines.with_context(from_pack_wizard=True).write({
                        'availability_status': 'payment_completed'
                    })
                    order._compute_order_status_from_lines()
                    _logger.info(
                        "💳 [UTR] Order Status after UTR: %s",
                        order.sdk_augmont_status
                    )
                    # ── Push "Payment Completed" to the connected website ─────
                    # SaleOrderLine.write() always calls _compute with
                    # skip_api_sync=True, so the compute path never fires the
                    # website API.  _compute_order_status_from_lines() above also
                    # skips it (_db_old_status == new_status after SQL update).
                    # We must call explicitly here so UTR-triggered Payment
                    # Completed status reaches the website immediately.
                    try:
                        order._call_augmont_status_api('Payment Completed')
                        _logger.info(
                            "✅ [UTR API] 'Payment Completed' pushed to website | Order: %s",
                            order.name
                        )
                    except Exception as _api_err:
                        _logger.error(
                            "❌ [UTR API] Failed to push 'Payment Completed' for %s: %s",
                            order.name, _api_err
                        )
                else:
                    _logger.warning(
                        "💳 [UTR] No payment_pending lines found for order: %s", order.name
                    )

        # ── Log status transition (non-blocking) ─────────────────────────────
        if sdk_status_new is not None:
            bypass_validation = self.env.context.get('bypass_status_validation', False)
            qc_statuses = ['In QC process', 'QC Fail', 'Payment Pending', 'Payment Completed']
            for order in self:
                old_st = old_statuses.get(order.id) or order.sdk_augmont_status
                if (not bypass_validation and old_st
                        and old_st in ALLOWED_AUGMONT_STATUS_TRANSITIONS):
                    allowed = ALLOWED_AUGMONT_STATUS_TRANSITIONS[old_st]
                    if old_st in qc_statuses or sdk_status_new in qc_statuses:
                        _logger.info(
                            "🔓 Flexible QC transition | Order: %s | %s → %s",
                            order.name, old_st, sdk_status_new
                        )
                    elif sdk_status_new not in allowed:
                        _logger.warning(
                            "[STATUS TRANSITION] Order: %s | %s → %s | Not in allowed map | Logged only",
                            order.name, old_st, sdk_status_new
                        )
                _logger.info(
                    "✅ Status transition | Order: %s | %s → %s",
                    order.name, old_st or 'None', sdk_status_new
                )

        # ── Capture line statuses BEFORE write for auto-QC detection ───────────
        _line_statuses_before = {}
        if 'order_line' in vals and not self.env.context.get('skip_auto_procurement'):
            for order in self:
                for line in order.order_line:
                    _line_statuses_before[line.id] = line.availability_status

        # ── Call super().write() only if there are remaining vals ─────────────
        # This avoids calling sale_stock's write() unnecessarily when the only
        # thing being written was sdk_augmont_status (which we extracted above).
        result = True
        if vals:
            result = super().write(vals)

        # ── AUTO-SALESPERSON: Sync manual salesperson change back to customer ──
        if 'user_id' in vals and vals.get('user_id') and \
                not self.env.context.get('skip_salesperson_sync'):
            for order in self:
                if order.partner_id and order.user_id:
                    try:
                        order.partner_id.with_context(skip_salesperson_sync=True).write({
                            'user_id': order.user_id.id
                        })
                        _logger.info(
                            "🔄 [AUTO-SALESPERSON] Customer '%s' → partner.user_id "
                            "updated to '%s' (triggered by Order: %s)",
                            order.partner_id.name, order.user_id.name, order.name
                        )
                    except Exception as e:
                        _logger.error(
                            "❌ [AUTO-SALESPERSON] Failed to update partner.user_id "
                            "for customer '%s': %s",
                            order.partner_id.name, e
                        )

        if _line_statuses_before and not self.env.context.get('skip_auto_procurement'):
            lines_needing_procurement = self.env['sale.order.line']
            for order in self:
                for line in order.order_line:
                    old_st = _line_statuses_before.get(line.id)
                    if old_st in ('diamond_booked', 'confirmed') and line.availability_status == 'in_qc_process':
                        lines_needing_procurement |= line
            if lines_needing_procurement:
                _logger.info(
                    "🔄 [AUTO-QC] Detected %d line(s) changed confirmed → in_qc_process via SO save",
                    len(lines_needing_procurement)
                )
                lines_needing_procurement.with_context(
                    skip_auto_procurement=True,
                )._auto_create_procurement_for_qc()

        # ── POST-WRITE: Invoice payment memo ──────────────────────────────────
        for order in self:
            if order.invoice_ids:
                for invoice in order.invoice_ids:
                    for payment in invoice.payment_ids:
                        payment.memo = f"Payment for Invoice {invoice.name}"

        # ── POST-WRITE: Mark activities done when state=done ──────────────────
        if 'state' in vals and vals['state'] == 'done':
            for order in self:
                activities = self.env['mail.activity'].search([
                    ('res_model', '=', 'sale.order'),
                    ('res_id', '=', order.id),
                    ('activity_type_id', '=',
                     self.env.ref('mail.mail_activity_data_todo').id),
                ])
                for act in activities:
                    act.action_done()


        if sdk_status_new is not None:
            in_compute = self.env.context.get('_in_compute_augmont_status', False)
            if in_compute:
                # PATH A: fast SQL-only persist, no line sync, no API
                _logger.info(
                    "⚡ [WRITE FAST PATH] Compute-triggered sdk_augmont_status write "
                    "(skipping _safe_write_augmont_status to prevent recursion)"
                )
                for order in self:
                    self.env.cr.execute(
                        "UPDATE sale_order SET sdk_augmont_status = %s WHERE id = %s",
                        (sdk_status_new, order.id)
                    )
                    order.invalidate_recordset(['sdk_augmont_status'])
            else:
                # PATH B: full helper — syncs lines + calls API
                self._safe_write_augmont_status(
                    sdk_status_new,
                    skip_api=skip_api_sync,
                    old_status=old_statuses, 
                )

        return result
            
    def _prepare_procurement_values(self, group_id=False):
        values = super()._prepare_procurement_values(group_id)
        print("working sale values")
        # Override dates
        values.update({
            'date_order': self.order_id.date_order,
            'date_planned': datetime.datetime.now() + timedelta(days=2),
            # 'date_planned': self.order_id.expected_date or self.order_id.date_order,
        })
        return values

    def action_export_order_lines_csv(self):
        """Return a download URL for a CSV export of this order's lines."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'url': '/sale_order/%s/export_order_lines_csv' % self.id,
            'target': 'self',
        }

class SaleOrderCancelInherited(models.TransientModel):
    _inherit = 'sale.order.cancel'

    def action_cancel(self):
        self.ensure_one()
        order = self.env['sale.order'].browse(self._context.get('active_id'))
        
        if order:
            _logger.info("🚨 CANCEL ACTION TRIGGERED for order %s", order.name)
            
            # First: Update ALL line statuses to 'cancelled'
            for line in order.order_line:
                old_status = line.availability_status
                line.write({'availability_status': 'cancelled'})
                _logger.info(
                    "❌ CANCEL: Line %s | %s → 'cancelled'",
                    line.order_number or line.product_id.name,
                    old_status
                )
            
            # Second: Call parent to handle Odoo's cancel logic
            result = super(SaleOrderCancelInherited, self).action_cancel()
            
            # Third: Explicitly update order status
            order.write({'sdk_augmont_status': 'Cancelled'})
            _logger.info("❌ CANCEL: Order %s → Status: Cancelled", order.name)
            
            # Fourth: Force recompute to ensure consistency
            order._compute_order_status_from_lines()
            _logger.info("✅ CANCEL: Recompute complete for %s", order.name)
            
            return result
        
        return {'type': 'ir.actions.act_window_close'}

class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'
    
    translated_product_name = fields.Char(string="Translated Product Name")
    
    order_number = fields.Char(string="Order Number")
    is_custom_product = fields.Boolean()
    certificate = fields.Char(related='product_template_id.certificate', string="Certificate Number")
    carat_weight = fields.Char(related='product_template_id.weight_carat', string="Carat Weight")
    polish = fields.Char(string='Polish', related='product_template_id.polish')
    symmetry = fields.Char(string='Symmetry', related='product_template_id.symmetry')
    color = fields.Char(related='product_template_id.color', string="Color")
    clarity = fields.Char(related='product_template_id.clarity', string="Clarity")
    shapes = fields.Char(related='product_template_id.shapes', string="Shapes")
    cut = fields.Char(string='Cut',related='product_template_id.cut')
    # image_augmont = fields.Char(related='product_template_id.image_augmont', string="Image", store=True)
    final_price = fields.Float(related='product_template_id.final_price',string="Vendor Price" ) 
    final_price_margin = fields.Float(related='product_template_id.final_price_margin', string="Final Price")
    stock_number = fields.Char(related='product_template_id.stock_number', string="Vendor SKU")
    lgd_stock_number = fields.Char(related='product_template_id.lgd_stock_number', string="LGD SKU")
    vendor_id = fields.Many2one('res.partner',string="Vendor Company")
    vendor_city = fields.Char(string="Vendor City",related='vendor_id.city')
    is_block = fields.Boolean(string="Block", default=False,readonly=True)
    is_available = fields.Boolean(string="Pass Check",default=False,copy=False)
    is_not_available = fields.Boolean(string="Fail Check",default=False,copy=False)
    fluorescence_color = fields.Char(string="Fluorescence Color",related='product_template_id.fluorescence_color')
    fluorescence_intensity = fields.Char(string="Fluorescence Intensity",related='product_template_id.fluorescence_intensity')
    treatments = fields.Char(string='Treatments',related='product_template_id.treatments')

    # Added for the Order Lines CSV export (Procurement > Orders)
    # These already existed on product.template but were not yet related onto the order line, so they're added here.
    labs = fields.Char(string="Lab", related='product_template_id.labs')
    measurements = fields.Char(string="Measurement", related='product_template_id.measurements')
    luster = fields.Char(string="Luster", related='product_template_id.luster')
    shade = fields.Char(string="Shade", related='product_template_id.shade')

    # ── Added for the Order Lines CSV export column reorder ──
    vendor_sku = fields.Char(string="Vendor SKU", related='product_template_id.stock_number')
    vendor_per_carat_price = fields.Float(string="Vendor Per Carat Price", related='product_template_id.price_per_carat')

    # ── Added for the "Sale Price per carat" export column ──
    sale_price_per_carat = fields.Float(string="Sale Price per carat", related='product_id.lst_price')

    # ─────────────────────────────────────────────────────────────────────────
    # MELEE (Non-Certified / Parcel) line fields
    # Used when the website sends orders with lineType = "MELEE".
    # The parent MELEE line represents the overall meleeDiamondRequest.
    # Each child parcel line represents one entry in meleeDiamondRequest.parcels.
    # ─────────────────────────────────────────────────────────────────────────

    line_type = fields.Selection([
        ('lgd', 'LGD (Certified)'),
        ('melee', 'MELEE (Non-Certified)'),
    ], string="Line Type", default='lgd',
       help="LGD = certified individual stone; MELEE = non-certified parcel order.")

    # True only on the summary/parent line that groups all parcel child-lines.
    is_melee_parent = fields.Boolean(
        string="Is MELEE Parent",
        default=False,
        help="Marks the top-level summary line for a MELEE (non-certified) request.",
    )

    # Child parcel lines point back to their parent MELEE summary line.
    melee_parent_line_id = fields.Many2one(
        'sale.order.line',
        string="MELEE Parent Line",
        ondelete='cascade',
        index=True,
        help="Links a parcel child-line to its parent MELEE summary line.",
    )

    # Reverse relation: parent line → all its parcel child-lines.
    melee_child_line_ids = fields.One2many(
        'sale.order.line',
        'melee_parent_line_id',
        string="MELEE Parcel Lines",
    )

    # ── MELEE parent-level data (stored on the parent summary line) ──────────

    # Unique request identifier from the website (e.g. "SR-4919416555")
    melee_request_number = fields.Char(string="Request Number")

    # Human-readable title (e.g. "purple VVS1 Princess Excellent")
    melee_summary_title = fields.Char(string="MELEE Summary")

    # Unit in which the total quantity is expressed "CARAT"
    melee_unit = fields.Char(string="MELEE Unit")

    # Total quantity (carats) for the entire MELEE request
    melee_total_qty = fields.Float(
        string="Total Qty (Carats)",
        digits=(16, 4),
    )

    # Accepted quote ID for this request
    melee_quote_id = fields.Char(string="Quote ID")

    # Growth/treatment type of the MELEE stones (e.g. "HPHT", "CVD")
    melee_treatment = fields.Char(string="MELEE Treatment")

    # Requested shape(s) (e.g. "Princess")
    melee_shapes = fields.Char(string="MELEE Shapes")

    # Requested clarity grade(s) (e.g. "VVS1")
    melee_clarities = fields.Char(string="MELEE Clarities")

    # Requested cut grade(s) (e.g. "Excellent")
    melee_cuts = fields.Char(string="MELEE Cuts")

    # Fancy colour (e.g. "purple"); blank for white/colourless stones
    melee_fancy_color = fields.Char(string="Fancy Color")

    # ── MELEE parcel-level data (stored on each child parcel line) ───────────

    # Sort order of this parcel within the parent request (1, 2, 3 …)
    melee_parcel_sort_order = fields.Integer(string="Parcel #")

    # Unit type for the parcel quantity ("PIECE", "CARAT", etc.)
    melee_parcel_unit_type = fields.Char(string="Parcel Unit")

    # Number of stones (pieces) in this parcel
    melee_parcel_quantity = fields.Float(string="Parcel Pieces", digits=(16, 2))

    # Physical dimensions of each stone in the parcel (in mm)
    melee_parcel_length_mm = fields.Float(string="Length (mm)", digits=(16, 2))
    melee_parcel_width_mm  = fields.Float(string="Width (mm)",  digits=(16, 2))
    melee_parcel_depth_mm  = fields.Float(string="Depth (mm)",  digits=(16, 2))

    # Average carat weight per individual stone in the parcel
    melee_parcel_avg_carat = fields.Float(
        string="Avg Carat/Stone",
        digits=(16, 6),
    )

    # Total quoted weight (carats) for this parcel line
    melee_parcel_qty_carats = fields.Float(
        string="Qty (Carats)",
        digits=(16, 6),
    )

    # Quoted price per carat for this parcel line
    melee_parcel_price_per_carat = fields.Float(string="Price/Carat")

    availability_status = fields.Selection([
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
    # ], string='Availability', copy=False, required=True, default='diamond_booked', tracking=True)
    ], string='Availability', copy=False, required=False, tracking=True)


    @api.onchange('product_template_id')
    def onchange_product_template_id(self):
        if self.product_template_id:
            self.vendor_id = self.product_template_id.seller_ids[0].partner_id if self.product_template_id.seller_ids else False
        # if self.final_price:
        #     self.price_subtotal = self.final_price

    def action_open_product_popup(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'product.product',
            'view_mode': 'form',
            'res_id': self.product_id.id,
            'target': 'new',  # popup instead of new page
        }


    @api.depends(
        'product_uom_qty', 'price_unit', 'tax_id',
        'currency_id', 'product_id', 'order_id',
        'availability_status',      
    )
    def _compute_amount(self):

        zero_lines = self.filtered(
            lambda l: l.availability_status in ('cancelled', 'not_available')
        )
        # Hard-zero all monetary fields for cancelled / not-available lines.
        zero_lines.update({
            'price_subtotal': 0.0,
            'price_tax':      0.0,
            'price_total':    0.0,
        })

        # Standard Odoo computation for every other line.
        active_lines = self - zero_lines
        if active_lines:
            super(SaleOrderLine, active_lines)._compute_amount()


    # Dependent dropdown — allowed transitions per UI status ─────
    # Defines which values a user is ALLOWED to pick from the Availability
    # dropdown based on the line's CURRENT (before-change) status.
    _AVAILABILITY_TRANSITIONS = {
        'diamond_booked': {'confirmed', 'not_available'},
        'confirmed':      {'confirmed', 'cancelled', 'in_qc_process'},
        'not_available':  {'cancelled'},
    }

    @api.onchange('availability_status')
    def _onchange_availability_status(self):
        """
        Two responsibilities:
        1. qty sync  — sets product_uom_qty to 0 or 1 based on new status
                       (existing behaviour, preserved unchanged)
        2. transition guard — resets invalid dropdown picks immediately and
                              shows a clear warning before the user can save
        """
        # ── qty sync (existing behaviour, unchanged) ──────────────────────
        if self.availability_status in ['not_available', 'cancelled']:
            self.product_uom_qty = 0
        elif self.availability_status in ['diamond_booked', 'confirmed']:
            self.product_uom_qty = 1

        # transition guard ────────────────────────────────────
        # self._origin holds the record values BEFORE this onchange fired,
        # so _origin.availability_status is the OLD (current DB) value.
        old_status = self._origin.availability_status
        new_status = self.availability_status

        # Only validate transitions that are in our UI-editable map.
        # In-flight statuses (QC, payment, dispatch) are set by the workflow and bypass this guard entirely.
        if old_status in self._AVAILABILITY_TRANSITIONS:
            allowed = self._AVAILABILITY_TRANSITIONS[old_status]
            if new_status not in allowed:
                # Reset to old value so the user sees no change in the cell.
                self.availability_status = old_status
                # Restore qty to match the reset status.
                if old_status in ['not_available', 'cancelled']:
                    self.product_uom_qty = 0
                elif old_status in ['diamond_booked', 'confirmed']:
                    self.product_uom_qty = 1

                labels = {
                    'diamond_booked': 'Diamond Booked',
                    'confirmed':      'Confirmed',
                    'not_available':  'Not available',
                    'cancelled':      'Cancelled',
                }
                allowed_labels = ' / '.join(
                    f'[{labels.get(a, a)}]' for a in sorted(allowed)
                )
                return {
                    'warning': {
                        'title': 'Invalid Status Transition',
                        'message': (
                            f"Cannot change Availability from "
                            f"[{labels.get(old_status, old_status)}] "
                            f"to [{labels.get(new_status, new_status)}].\n\n"
                            f"Allowed option(s) from "
                            f"[{labels.get(old_status, old_status)}]: "
                            f"{allowed_labels}"
                        ),
                    }
                }

    @api.constrains('availability_status')
    def _constrains_availability_status_transition(self):
        # Contexts that are allowed to make any transition freely.
        bypass_contexts = (
            'from_quality_module',
            'from_pack_wizard',
            'from_website_api',
            'dispatch_validation',
            'skip_availability_check',
        )
        if any(self.env.context.get(c) for c in bypass_contexts):
            return

        labels = {
            'diamond_booked': 'Diamond Booked',
            'confirmed':      'Confirmed',
            'not_available':  'Not available',
            'cancelled':      'Cancelled',
        }

        for line in self:
            new_status = line.availability_status

            # Read the previous value directly from the DB (pre-write).
            self.env.cr.execute(
                "SELECT availability_status FROM sale_order_line WHERE id = %s",
                (line.id,)
            )
            row = self.env.cr.fetchone()
            if not row:
                continue  # new record — no transition to validate
            old_status = row[0]

            if old_status == new_status:
                continue

            # Only validate transitions that start from a UI-editable status.
            if old_status not in self._AVAILABILITY_TRANSITIONS:
                continue

            allowed = self._AVAILABILITY_TRANSITIONS[old_status]
            if new_status not in allowed:
                allowed_labels = ', '.join(
                    f"[{labels.get(a, a)}]" for a in sorted(allowed)
                )
                raise ValidationError(
                    f"Order {line.order_id.name} — "
                    f"line {line.order_number or ''}:\n"
                    f"Cannot change Availability from "
                    f"[{labels.get(old_status, old_status)}] "
                    f"to [{labels.get(new_status, new_status)}].\n\n"
                    f"Allowed from "
                    f"[{labels.get(old_status, old_status)}]: "
                    f"{allowed_labels}"
                )

    # ── Server-side guard: only 3 values allowed from the UI ─────────────────
    @api.model_create_multi
    def create(self, vals_list):
        IN_FLIGHT_STATUSES = {
            'in_qc_process', 'qc_fail', 'payment_pending', 'payment_completed',
            'dispatched', 're_dispatched', 'return_of_order', 'delivered', 'order_completed',
        }

        filtered_vals = []
        for vals in vals_list:
            order_id = vals.get('order_id')
            new_status = vals.get('availability_status', 'diamond_booked')
            has_order_number = bool(vals.get('order_number'))

            # Only apply the guard to diamond_booked lines with no order_number.
            # Lines created from the website API always have order_number set, so
            # they will always pass through correctly.
            if order_id and new_status == 'diamond_booked' and not has_order_number:
                order = self.env['sale.order'].browse(order_id)
                # Check if ANY line on this order has already moved past 'confirmed'
                in_flight_lines = order.order_line.filtered(
                    lambda l: l.availability_status in IN_FLIGHT_STATUSES
                )
                if in_flight_lines:
                    _logger.warning(
                        "🛡️ Prevented creation of spurious 'diamond_booked' "
                        "line (no order_number) for order %s. "
                        "Order already has %d in-flight line(s): %s. "
                        "This was a duplicate record that would appear under the Invoice "
                        "in the Procurement module.",
                        order.name,
                        len(in_flight_lines),
                        list(in_flight_lines.mapped('availability_status'))
                    )
                    continue  

            filtered_vals.append(vals)

        if not filtered_vals:
            # All lines were spurious — return empty recordset
            _logger.info(
                "🛡️ All %d line(s) in this create() call were blocked as spurious.",
                len(vals_list)
            )
            return self.env['sale.order.line']

        return super(SaleOrderLine, self).create(filtered_vals)

    def _cancel_or_reduce_related_rfq(self):
        """
        When a SO line is cancelled/not_available:
        - Single source document (only this SO in RFQ origin) → cancel entire RFQ
        - Multi source document (multiple SOs merged in one RFQ) → unlink/reduce only
          this SO's PO line, keep other SOs' lines intact
        Only acts on draft/sent RFQs (not confirmed POs).
        """
        self.ensure_one()
        order = self.order_id
        if not order:
            return

        purchase_orders = self.env['purchase.order'].search([
            ('state', 'in', ['draft', 'sent']),
            ('origin', 'ilike', order.name),
        ])
        for po in purchase_orders:
            origin_names = [n.strip() for n in (po.origin or '').split(',') if n.strip()]
            if order.name not in origin_names:
                continue

            # Find PO lines for this SO line (prefer sale_line_id link)
            po_lines_for_so_line = po.order_line.filtered(
                lambda pl: (
                    (pl.sale_line_id and pl.sale_line_id.id == self.id)
                    or (not pl.sale_line_id
                        and pl.product_id.id == self.product_id.id
                        and pl.sale_order_id and pl.sale_order_id.id == order.id)
                )
            )
            # Fallback when origin is single-SO and no sale_line_id link
            if not po_lines_for_so_line and len(origin_names) == 1:
                po_lines_for_so_line = po.order_line.filtered(
                    lambda pl: pl.product_id.id == self.product_id.id
                )
            if not po_lines_for_so_line:
                continue

            # Scenario A: Single source document → cancel entire RFQ
            if len(origin_names) == 1 and origin_names[0] == order.name:
                other_so_lines = po.order_line.filtered(
                    lambda pl: pl.sale_order_id and pl.sale_order_id.id != order.id
                )
                if not other_so_lines:
                    _logger.info(
                        "🚫 [AUTO-RFQ-CANCEL] Cancelling RFQ %s (single source: %s)",
                        po.name, order.name
                    )
                    try:
                        po.button_cancel()
                    except Exception as e:
                        _logger.error(
                            "❌ [AUTO-RFQ-CANCEL] Failed to cancel %s: %s", po.name, e
                        )
                    continue

            # Scenario B: Multi-source RFQ → reduce/unlink this SO's lines
            _logger.info(
                "✂️ [AUTO-RFQ-REDUCE] Removing %s from RFQ %s (multi-source: %s)",
                self.product_id.display_name, po.name, po.origin
            )
            try:
                qty_to_remove = self.product_uom_qty or 1
                for pl in po_lines_for_so_line:
                    new_qty = (pl.product_qty or 0) - qty_to_remove
                    if new_qty <= 0:
                        pl.unlink()
                    else:
                        pl.product_qty = new_qty
                # Update origin: remove this SO's name
                remaining_origins = [n for n in origin_names if n != order.name]
                po.origin = ', '.join(remaining_origins) if remaining_origins else False

                # ── Re-trigger auto-QC if remaining SO lines are now all in_qc_process ──
                # Handles case: B → In QC (RFQ stays draft), then A cancelled — now
                # remaining RFQ has only B which is already in_qc_process → should confirm
                if po.exists() and po.state in ('draft', 'sent') and po.order_line:
                    in_qc_lines = self.env['sale.order.line']
                    for so_name in [n for n in origin_names if n != order.name] + [order.name]:
                        rel_so = self.env['sale.order'].search([('name', '=', so_name)], limit=1)
                        if rel_so:
                            for sol in rel_so.order_line:
                                if (sol.availability_status == 'in_qc_process'
                                        and sol.product_id.id in po.order_line.mapped('product_id.id')):
                                    in_qc_lines |= sol
                    if in_qc_lines:
                        _logger.info(
                            "🔁 [AUTO-RFQ-REDUCE] Re-triggering auto-QC for %d line(s) "
                            "after removing cancelled lines from RFQ %s",
                            len(in_qc_lines), po.name
                        )
                        in_qc_lines.with_context(
                            skip_auto_procurement=True,
                        )._auto_create_procurement_for_qc()
            except Exception as e:
                _logger.error(
                    "❌ [AUTO-RFQ-REDUCE] Failed for RFQ %s: %s", po.name, e
                )

    def _auto_create_procurement_for_qc(self):
        """
        Auto-QC flow when lines move from 'confirmed' to 'in_qc_process'.

        Steps:
        1. Confirm the SO (action_confirm) — this creates RFQ + Dispatch via standard Odoo
        2. Find the RFQ created for each line's vendor, confirm it
        3. Validate the Logistics receipt (loc 4→18)
        4. This triggers existing button_validate logic which creates QC picking (loc 18→10)
        """
        if not self:
            return

        # Group lines by order
        orders = set(self.mapped('order_id'))

        for order in orders:
            order_lines = self.filtered(lambda l: l.order_id == order)
            _logger.info(
                "🔄 [AUTO-QC] Starting for order %s | %d line(s)",
                order.name, len(order_lines)
            )

            # ── Step 1: Confirm the SO if still draft ──────────────────
            if order.state == 'draft':
                _logger.info("🔄 [AUTO-QC] Confirming SO %s...", order.name)
                order.with_context(
                    skip_auto_procurement=True,
                    skip_api_sync=True,
                ).action_confirm()
                _logger.info(
                    "✅ [AUTO-QC] SO confirmed: %s | State: %s",
                    order.name, order.state
                )

            # ── Step 2: Find RFQs created by action_confirm, confirm them ──
            purchase_orders = self.env['purchase.order'].search([
                ('origin', 'like', order.name),
                ('state', 'in', ['draft', 'sent']),
            ])
            for po in purchase_orders:
                # Only auto-confirm if ALL PO products have their SO lines
                # at 'in_qc_process'. This handles sequential API calls where
                # Line 1 was changed earlier and Line 2 is changed now.
                po_product_ids = po.order_line.mapped('product_id.id')
                all_ready = True
                for pid in po_product_ids:
                    so_line = order.order_line.filtered(
                        lambda l: l.product_id.id == pid
                    )[:1]
                    if not so_line or so_line.availability_status != 'in_qc_process':
                        all_ready = False
                        break
                if all_ready:
                    # Set PO line prices from SO line prices
                    for po_line in po.order_line:
                        so_line = order.order_line.filtered(
                            lambda l: l.product_id.id == po_line.product_id.id
                        )[:1]
                        if so_line and so_line.price_unit:
                            po_line.price_unit = so_line.price_unit

                    _logger.info(
                        "🔄 [AUTO-QC] Confirming PO %s (Vendor: %s)...",
                        po.name, po.partner_id.name
                    )
                    po.with_context(
                        skip_auto_procurement=True,
                    ).button_confirm()
                    _logger.info(
                        "✅ [AUTO-QC] PO confirmed: %s | State: %s",
                        po.name, po.state
                    )

                    # ── Step 3: Find and validate Logistics receipt ────
                    receipt = po.picking_ids.filtered(
                        lambda p: p.location_id.id == 4
                        and p.location_dest_id.id == 18
                        and p.state not in ('done', 'cancel')
                    )[:1]

                    if receipt:
                        for move in receipt.move_ids_without_package:
                            move.quantity = move.product_uom_qty
                        _logger.info(
                            "🔄 [AUTO-QC] Validating Logistics receipt: %s",
                            receipt.name
                        )
                        receipt.with_context(
                            skip_auto_procurement=True,
                        ).button_validate()
                        _logger.info(
                            "✅ [AUTO-QC] Logistics receipt validated: %s | State: %s",
                            receipt.name, receipt.state
                        )
                    else:
                        _logger.warning(
                            "⚠️ [AUTO-QC] No receipt found for PO %s",
                            po.name
                        )

            # Log in SO chatter
            product_names = ', '.join(order_lines.mapped('product_id.display_name'))
            order.message_post(
                body=Markup(
                    "Auto-QC flow triggered:<br/>"
                    "• SO confirmed<br/>"
                    "• RFQ(s) confirmed and Logistics receipt(s) validated<br/>"
                    "• QC picking(s) created for Quality team<br/>"
                    "• Products: <b>%s</b>"
                ) % product_names,
                subject="Auto QC Flow"
            )

    def write(self, vals):
        """
        Override write to:
        1. Save availability_status changes
        2. Recompute order status IMMEDIATELY (UI updates)
        3. Trigger auto-confirm ONLY for draft orders becoming confirmed (NOT for status updates)
        
        Prevent auto-confirm from running when updating payment_pending → payment_completed
        This was causing duplicate purchase orders to be created when UTR was entered.
        """
        # Save the old status before write
        old_statuses = {line.id: line.availability_status for line in self if line.id}
        
        # Check if this is coming from Quality module or Pack wizard
        from_quality_module = self.env.context.get('from_quality_module', False)
        from_pack_wizard = self.env.context.get('from_pack_wizard', False)
        from_website_api = self.env.context.get('from_website_api', False)
        dispatch_validation = self.env.context.get('dispatch_validation', False)

        # Perform the write
        result = super(SaleOrderLine, self).write(vals)
        
        # If availability_status changed, handle post-save logic
        if 'availability_status' in vals:
            # Log every changed line for traceability
            for line in self:
                if not line.order_id:
                    continue
                old_status = old_statuses.get(line.id)
                new_status = line.availability_status
                _logger.info(
                    "📝 Line %s (Order: %s): %s → %s | from_quality=%s, from_pack=%s",
                    line.order_number or 'N/A', line.order_id.name, old_status, new_status,
                    from_quality_module, from_pack_wizard
                )

            # ── Zero out qty for cancelled/not_available (mirrors onchange for API calls) ──
            for line in self:
                if line.availability_status in ('not_available', 'cancelled') and line.product_uom_qty != 0:
                    line.product_uom_qty = 0

            # ── Auto-cancel/reduce RFQ when line → cancelled/not_available ──
            if not self.env.context.get('skip_auto_procurement'):
                for line in self:
                    old_status = old_statuses.get(line.id)
                    if (old_status not in ('cancelled', 'not_available')
                            and line.availability_status in ('cancelled', 'not_available')
                            and line.order_id):
                        line.with_context(
                            skip_auto_procurement=True,
                        )._cancel_or_reduce_related_rfq()

            # ── Auto-QC sync: mark QC moves as pass/fail when SO line status changes ──
            for line in self:
                if line.availability_status in ('qc_fail', 'payment_pending') and line.order_id:
                    old_status = old_statuses.get(line.id)
                    _logger.info(
                        "🔍 [AUTO-QC-SYNC] Checking line %s (Product: %s) | "
                        "old=%s → new=%s | Order: %s",
                        line.order_number or line.id,
                        line.product_id.display_name,
                        old_status, line.availability_status,
                        line.order_id.name
                    )
                    if old_status == line.availability_status:
                        continue  # no change
                    locs = self.env['stock.picking']._get_augmont_locations()
                    # Clear cache to ensure fresh data after previous writes in same transaction
                    self.env['stock.move'].invalidate_model(['qc_status', 'is_pass', 'is_fail'])
                    # Find QC pickings for this SO (by sale_id or origin)
                    qc_pickings = self.env['stock.picking'].search([
                        '|',
                        ('sale_id', '=', line.order_id.id),
                        ('origin', '=', line.order_id.name),
                        ('location_id', '=', locs['quality_control']),
                        ('location_dest_id', '=', locs['inventory']),
                        ('state', 'not in', ['done', 'cancel']),
                    ])
                    qc_moves = self.env['stock.move']
                    if qc_pickings:
                        qc_moves = self.env['stock.move'].search([
                            ('product_id', '=', line.product_id.id),
                            ('picking_id', 'in', qc_pickings.ids),
                            ('qc_status', '=', False),
                        ])
                    if qc_moves:
                        if line.availability_status == 'qc_fail':
                            qc_moves.write({
                                'is_fail': True,
                                'is_pass': False,
                                'is_order_specific': True,
                                'is_item_notes': True,
                                'is_default_qc_requirements': True,
                                'is_weight_carat': True,
                            })
                            _logger.info(
                                "❌ [AUTO-QC-FAIL] Marked %d QC move(s) as failed for %s "
                                "(Order: %s, transition: %s → qc_fail)",
                                len(qc_moves), line.product_id.display_name,
                                line.order_id.name, old_status
                            )
                        elif line.availability_status == 'payment_pending':
                            qc_moves.write({
                                'is_pass': True,
                                'is_fail': False,
                                'is_order_specific': True,
                                'is_item_notes': True,
                                'is_default_qc_requirements': True,
                                'is_weight_carat': True,
                            })
                            _logger.info(
                                "✅ [AUTO-QC-PASS] Marked %d QC move(s) as passed for %s "
                                "(Order: %s, transition: %s → payment_pending)",
                                len(qc_moves), line.product_id.display_name,
                                line.order_id.name, old_status
                            )
                    else:
                        _logger.warning(
                            "⚠️ [AUTO-QC-FAIL] No QC moves found for %s (Order: %s) "
                            "— QC picking may not exist or already decided",
                            line.product_id.display_name, line.order_id.name
                        )
                    # ── Auto-validate QC picking if all moves are decided ──
                    if qc_pickings:
                        for qc_pick in qc_pickings:
                            undecided = qc_pick.move_ids_without_package.filtered(
                                lambda m: not m.qc_status and m.state not in ('done', 'cancel')
                            )
                            if not undecided:
                                _logger.info(
                                    "🔄 [AUTO-QC-VALIDATE] All moves decided in %s — auto-validating",
                                    qc_pick.name
                                )
                                try:
                                    for move in qc_pick.move_ids_without_package:
                                        if move.state not in ('done', 'cancel'):
                                            move.quantity = move.product_uom_qty
                                    qc_pick.with_context(
                                        skip_auto_procurement=True,
                                    ).button_validate()
                                    _logger.info(
                                        "✅ [AUTO-QC-VALIDATE] %s validated | State: %s",
                                        qc_pick.name, qc_pick.state
                                    )
                                except Exception as e:
                                    _logger.error(
                                        "❌ [AUTO-QC-VALIDATE] Failed to validate %s: %s",
                                        qc_pick.name, e
                                    )

            # ── Auto-create procurement for → in_qc_process ──
            if not self.env.context.get('skip_auto_procurement'):
                lines_needing_procurement = self.env['sale.order.line']
                for line in self:
                    old_status = old_statuses.get(line.id)
                    if (old_status in ('diamond_booked', 'confirmed')
                            and line.availability_status == 'in_qc_process'):
                        lines_needing_procurement |= line
                if lines_needing_procurement:
                    lines_needing_procurement.with_context(
                        skip_auto_procurement=True,
                    )._auto_create_procurement_for_qc()

            affected_orders = self.filtered(lambda l: l.order_id).mapped('order_id')
            for order in affected_orders:
                _logger.info(
                    "💾 [NATIVE SAVE / LINE WRITE] Order %s: availability_status "
                    "recomputed — API suppressed here. Use {Save} button to push "
                    "status to website.",
                    order.name
                )
                order.order_line.invalidate_recordset(['availability_status'])
                order.with_context(
                    from_website_api=from_website_api,
                    _in_compute_augmont_status=True,  # anti-recursion guard
                    skip_api_sync=True,               # API via {Save} only
                )._compute_order_status_from_lines()
                new_order_status = order.sdk_augmont_status

                _logger.info(
                    "🔄 [RECOMPUTE] Order: %s → sdk_augmont_status: %s",
                    order.name, new_order_status
                )
                
                if new_order_status:
                    self.env.cr.execute(
                        "UPDATE sale_order SET sdk_augmont_status = %s WHERE id = %s",
                        (new_order_status, order.id)
                    )
                    # Clear ORM cache so subsequent reads see the freshly written value
                    order.invalidate_recordset(['sdk_augmont_status'])
                    _logger.info(
                        "💾 [SQL PERSIST] Order %s sdk_augmont_status → %s (bypassing the sale_stock write)",
                        order.name, new_order_status
                    )


                if vals.get('availability_status') in ('cancelled', 'not_available'):
                    # Step 1 — refresh price_subtotal / price_tax on all lines
                    #           (our override zeros cancelled/not_available ones)
                    order.order_line._compute_amount()

                    # Step 2 — sum the freshly-computed line values
                    lines = order.order_line
                    new_untaxed = sum(lines.mapped('price_subtotal')) + (order.shipping_charges or 0.0)
                    new_tax     = sum(lines.mapped('price_tax'))
                    new_total   = new_untaxed + new_tax

                    # Step 3 — persist directly to DB 
                    self.env.cr.execute(
                        """
                        UPDATE sale_order
                           SET amount_untaxed = %s,
                               amount_tax     = %s,
                               amount_total   = %s
                         WHERE id = %s
                        """,
                        (new_untaxed, new_tax, new_total, order.id)
                    )

                    # Step 4 — drop stale ORM cache so next read hits the DB
                    order.invalidate_recordset(
                        ['amount_untaxed', 'amount_tax', 'amount_total']
                    )

                    _logger.info(
                        "💰 [TOTAL RECOMPUTE] Order %s → "
                        "untaxed: %.2f | tax: %.2f | total: %.2f",
                        order.name, new_untaxed, new_tax, new_total,
                    )


            if not from_pack_wizard and not from_quality_module and not from_website_api and not dispatch_validation:
                orders_to_confirm = set()
                for line in self:
                    if (line.order_id and 
                        line.order_id.sdk_augmont_status == 'Confirmed' and 
                        line.order_id.state == 'draft' and
                        line.order_id.id not in orders_to_confirm):
                        orders_to_confirm.add(line.order_id.id)
                
                # Now confirm all orders that need it
                for order_id in orders_to_confirm:
                    order = self.env['sale.order'].browse(order_id)
                    _logger.info(f"🔄 Auto-confirming {order.name} (after availability update)")
                    try:

                        order.with_context(
                            no_recompute=True,
                            skip_api_sync=True,
                        ).action_confirm()
                    except Exception as e:
                        _logger.error(f"Failed to auto-confirm {order.name}: {e}")
            else:
                _logger.info(
                    "⏭️ Skipping auto-confirm (from_quality=%s, from_pack=%s, from_website_api=%s, dispatch_validation=%s)",
                    from_quality_module, from_pack_wizard, from_website_api, dispatch_validation
                )
        
        return result
                
    def _action_launch_stock_rule(self, previous_product_uom_qty=False):
        """
        Only launch stock rules (create deliveries) for eligible order lines.

        PREVENTION — THREE-LAYER DEFENCE:

        Layer 1 — dispatch_validation context:
            When validating a Dispatch picking (loc 20/21 → Customer loc 5),
            super().button_validate() is called with dispatch_validation=True.
            This immediately blocks all procurement in that cycle.

        Layer 2 — confirmed-only filter:
            Only lines at 'confirmed' status ever trigger new stock rules.
            Lines at any in-flight status (payment_pending, payment_completed,
            dispatched, delivered, etc.) are always blocked here.

        Layer 3 — in-flight order check:
            If ANY line on the same order has already progressed beyond
            'confirmed', we skip stock rule launch entirely for the whole
            order. This prevents a rare race condition where the engine
            picks up a 'confirmed' line on an order that is already mid-flow.
        """
        IN_FLIGHT_STATUSES = {
            'in_qc_process', 'qc_fail', 'payment_pending', 'payment_completed',
            'dispatched', 're_dispatched', 'return_of_order', 'delivered', 'order_completed',
        }

        # -------------------------------------------------------
        # LAYER 1: dispatch_validation / no_recompute context check
        # -------------------------------------------------------
        if self.env.context.get('dispatch_validation') or self.env.context.get('no_recompute'):
            _logger.info(
                "⏭️ Skipping stock rule launch — "
                "dispatch_validation/no_recompute context active (%d lines blocked)",
                len(self)
            )
            return self.env['stock.move']

        # -------------------------------------------------------
        # LAYER 2: Only 'confirmed' lines trigger new procurement
        # -------------------------------------------------------
        if self.env.context.get('skip_auto_procurement'):
            # Auto-QC flow: also allow in_qc_process lines (they were just changed)
            allowed_lines = self.filtered(
                lambda l: l.availability_status in ['confirmed', 'in_qc_process']
            )
        else:
            allowed_lines = self.filtered(
                lambda l: l.availability_status in ['confirmed']
            )

        _logger.info(
            "🔍 Stock Rule Filter: Total lines=%d, Eligible (confirmed only)=%d",
            len(self), len(allowed_lines)
        )

        if not allowed_lines:
            _logger.info("⏭️ No 'confirmed' lines — skipping stock rule launch")
            return self.env['stock.move']

        # -------------------------------------------------------
        # LAYER 3: Skip if the order already has ANY in-flight line
        # This is a belt-and-suspenders guard against race conditions
        # Bypassed during auto-QC flow (skip_auto_procurement context)
        # -------------------------------------------------------
        if self.env.context.get('skip_auto_procurement'):
            final_lines = allowed_lines
        else:
            final_lines = self.env['sale.order.line']
            for line in allowed_lines:
                order = line.order_id
                if order:
                    in_flight = order.order_line.filtered(
                        lambda l: l.availability_status in IN_FLIGHT_STATUSES
                    )
                    if in_flight:
                        _logger.warning(
                            "⏭️ Skipping confirmed line '%s' on order %s — "
                            "order has %d in-flight line(s) (%s). "
                            "Procurement blocked to prevent duplicate record creation.",
                            line.product_id.name, order.name,
                            len(in_flight),
                            list(in_flight.mapped('availability_status'))
                        )
                        continue
                final_lines |= line
        final_lines = self.env['sale.order.line']
        for line in allowed_lines:
            order = line.order_id
            if order:
                in_flight = order.order_line.filtered(
                    lambda l: l.availability_status in IN_FLIGHT_STATUSES
                )
                if in_flight:
                    _logger.warning(
                        "⏭️ Skipping confirmed line '%s' on order %s — "
                        "order has %d in-flight line(s) (%s). "
                        "Procurement blocked to prevent duplicate record creation.",
                        line.product_id.name, order.name,
                        len(in_flight),
                        list(in_flight.mapped('availability_status'))
                    )
                    continue
            final_lines |= line

        if not final_lines:
            _logger.info(
                "⏭️ All %d confirmed line(s) blocked — "
                "their orders already have in-flight lines",
                len(allowed_lines)
            )
            return self.env['stock.move']

        for line in (self - final_lines):
            _logger.info(
                "🚫 Blocked line %s (Order: %s) - Availability: %s",
                line.product_id.name, line.order_number, line.availability_status
            )

        return super(SaleOrderLine, final_lines)._action_launch_stock_rule(previous_product_uom_qty)
        

class AccountPayment(models.Model):
    _inherit = "account.payment"

    sale_order_id = fields.Many2one('sale.order',  string="Sale Order")

 



#  ////////////////////////////////////////  --- Commented old Method -------------/////////////////////////////////////



       # def action_open_product_popup(self):
    #     print(self.id,"self.id")
    #     self.ensure_one()
    #     context = dict(self._context or {})
    #     print(context,"context")
        # context.update({
        #     'default_order_id': self.id,
        #     'default_name': '[NEW]',
        #     'default_is_storable': True,
        #     'default_route_ids': [(6, 0, [1, 5])],
        # })
    #     return {
    #         'name': "Create Product",
    #         'type': 'ir.actions.act_window',
    #         'res_model': 'product.template',
    #         'view_mode': 'form',
    #         'target': 'new',
    #         'context': context,
    #     }


   # def _prepare_augmont_status_data(self, status, comment=None):
    #     """Prepare data for Augmont API"""
    #     self.ensure_one()
        
    #     # Get customer information
    #     partner = self.partner_id
        
    #     # Auto-populate courier info from delivery if available
    #     if status == 'Dispatched' and self.picking_ids:
    #         picking = self.picking_ids.filtered(lambda p: p.state == 'done')[:1]
    #         # if picking and picking.carrier_id:
    #             # courier_name = picking.tracker_name or picking.carrier_id.name 
    #             # tracking_no = picking.tracking_number or picking.carrier_tracking_ref or ''
        
    #     data = {
    #         "ordersStatus": [{
    #             "id": self.sdk_augmont_number or self.name,  # Use  if available
    #             "status": status,
    #             "comment": comment or f"Order status updated to {status} by Odoo system",
    #             # "courierPartnerName": courier_name,
    #             # "trackingNumber": tracking_no,
    #             "user": {
    #                 "firstName": partner.name.split(' ')[0] if partner.name else '',
    #                 "lastName": ' '.join(partner.name.split(' ')[1:]) if partner.name and len(partner.name.split(' ')) > 1 else '',
    #                 "email": partner.email or '',
    #                 "mobileNumber": partner.mobile or partner.phone or '',
    #                 # "idNumber": partner.vat or partner.id or ''
    #             }
    #         }]
    #     }


        
    #     return data
    
    # def _call_augmont_status_api(self, new_status, old_status=None):
    #     self.ensure_one()
        
    #     if not self.sdk_augmont_number:
    #         _logger.info(f"No Augmont Order ID for {self.name}, skipping API call")
    #         return

    #     try:
    #         Param = self.env['ir.config_parameter'].sudo()
    #         url = Param.get_param('base_augmont_url')
    #         url = f"{url}/api/v1/odoo/orderStatusUpdate"
    #         data = self._prepare_augmont_status_data(new_status)
    #         _logger.info(f"Calling Augmont API for order {self.name}: {json.dumps(data)}")

    #         headers = {
    #             "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJtZXJjaGFudElkIjoiMjYyMGJkNjctZDdmMS00ZmUxLTg4OWYtYWNiZWY4ZTFmNDkwIiwic2hvcnROYW1lIjoib2RvbyIsImlhdCI6MTc1NjcxODUxOSwiZXhwIjoyMDcyMjk0NTE5fQ.eQu-VMyFyqrds7QSKLi1aq9Mls-3wc1jqsrSesDpPXU",
    #             "Content-Type": "application/json",
    #         }

    #         response = requests.post(url, json=data, headers=headers, timeout=30)

    #         if response.status_code == 200:
    #             response_data = response.json()
    #             _logger.info(f"Augmont API Success for {self.name}: {response_data}")

    #             # Build status display
    #             if old_status:
    #                 status_change = f"{old_status} → {new_status}"
    #             else:
    #                 status_change = new_status

    #             self.message_post(
    #                 body=f"<p>Order status updated in Augmont system:</p>"
    #                     f"<ul><li>Status: {status_change}</li>"
    #                     f"<li>Response: {response_data.get('message', 'Success')}</li></ul>",
    #                 subject="Augmont Status Update"
    #             )
    #         else:
    #             _logger.error(f"Augmont API Error for {self.name}: {response.status_code} - {response.text}")
    #             # Optionally post error message (uncomment if needed)
    #     except Exception as e:
    #         _logger.error(f"Unexpected error calling Augmont API for {self.name}: {str(e)}")
    #         self.message_post(
    #             body=f"<p style='color:red;'>Failed to connect to Augmont API: {str(e)}</p>",
    #             subject="Augmont API Connection Error"
    #         )


    #     return {
    #         'type': 'ir.actions.client',
    #         'tag': 'display_notification',
    #         'params': {
    #             'title': 'Success',
    #             'message': f'Augmont status update initiated for status: {self.sdk_augmont_status}',
    #             'type': 'success',
    #             'sticky': False,
    #         }
    #     }
    # else:
    #     return {
    #         'type': 'ir.actions.client',
    #         'tag': 'display_notification',
    #         'params': {
    #             'title': 'Warning',
    #             'message': 'No Augmont status to send.',
    #             'type': 'warning',
    #             'sticky': False,
    #         }
    #     }


    # Override the field definition
    # price_subtotal = fields.Monetary(
    #     compute='_compute_price_subtotal_from_final',
    #     string='Subtotal',
    #     store=True
    # )

    # @api.depends('final_price')
    # def _compute_price_subtotal_from_final(self):
    #     for line in self:
    #         if line.final_price:
    #             line.price_subtotal = line.final_price

    # @api.model_create_multi
    # def create(self, vals_list):
    #     for vals in vals_list:
    #         product_id = vals.get('product_template_id')
    #         # raise ValidationError('hi')
    #         if product_id:
    #             product = self.env['product.template'].browse(product_id)
    #             if product:
    #                 vals['price_subtotal'] = product.final_price_margin
    #     lines = super(SaleOrderLine, self).create(vals_list)
    #     return lines

    # @api.model_create_multi
    # def create(self, vals_list):
    #     lines = super(SaleOrderLine, self).create(vals_list)
    #     for line in lines:
    #         if line.product_template_id:
    #             line.price_subtotal = (
    #                                     line.product_template_id.final_price_margin
    #                                     if line.product_template_id.final_price_margin > 0
    #                                     else line.product_template_id.final_price
    #                                     )
    #     return lines

    # @api.depends('product_template_id', 'product_uom_qty', 'price_unit', 'tax_id')
    # def _compute_amount(self):
    #     """
    #     Custom subtotal calculation using product's final_price_margin instead of qty * price_unit.
    #     """
    #     for line in self:
    #         # Fetch your custom price from product template
    #         final_price = line.product_template_id.final_price_margin or 0.0

    #         # Compute taxes using that price
    #         taxes = line.tax_id.compute_all(
    #             final_price,                      # ← use your custom price
    #             currency=line.order_id.currency_id,
    #             quantity=1,                       # each line is treated as 1 item
    #             product=line.product_template_id,
    #             partner=line.order_id.partner_shipping_id
    #         )

    #         line.update({
    #             'price_subtotal': taxes['total_excluded'],
    #             'price_tax': taxes['total_included'] - taxes['total_excluded'],
    #             'price_total': taxes['total_included'],
    #         })

    # def write(self, vals):
    #     """
    #     Update price_unit from final_price when product changes
    #     """
    #     result = super(SaleOrderLine, self).write(vals)
        
    #     # If product changed, update price_unit from final_price
    #     if 'final_price' in vals:
    #         for line in self:
    #             if line.final_price and line.final_price != line.price_unit:
    #                 line.price_unit = line.final_price
                    
    #                 # Recalculate price_subtotal
    #                 price = line.final_price
    #                 if line.discount:
    #                     price = price * (1 - (line.discount / 100.0))
    #                 line.price_subtotal = price * line.product_uom_qty
        
    #     # Trigger order total recalculation
    #     orders = self.mapped('order_id')
    #     for order in orders:
    #         order._amount_all()
        
    #     return result



    # @api.onchange('price_unit')
    # def _onchange_final_price(self):
    #     if self.price_unit and self.carat_weight:
    #         self.final_price = self.price_unit * float(self.carat_weight)
    #     else:
    #         self.final_price = self.product_template_id.final_price or 0.0



    # def _action_launch_stock_rule(self, previous_product_uom_qty=False):
    #     # Only lines that are NOT blocked
    #     lines = self.filtered(lambda l: not l.is_block)
    #     _logger.info("Launching stock rule for lines: %s", lines.ids)
    #     return super(SaleOrderLine, lines)._action_launch_stock_rule(previous_product_uom_qty)


    # def action_confirm(self):
    #     res = super(SaleOrder, self).action_confirm()
    #     for order in self:
    #         if order.location:
    #             # Example: Map location → picking type
    #             picking_type = False
    #             if order.location == "mumbai":
    #                 picking_type = 2 
    #             elif order.location == "surat":
    #                 picking_type = 14  

    #             if picking_type:
    #                 for picking in order.picking_ids:
    #                     if picking.state not in ("done", "cancel"):
    #                         picking.picking_type_id = picking_type
    #                         _logger.info("Updated picking %s to operation type %s for SO %s", picking.name, picking.picking_type_id.display_name, order.name)

                            
    #         unwanted_moves = order.picking_ids.move_ids_without_package.filtered(
    #             lambda m: m.sale_line_id and m.sale_line_id.is_not_available
    #         )
    #         if unwanted_moves:
    #             move_ids = unwanted_moves.ids
    #             _logger.info(
    #                 "Removing unwanted stock moves for Sale Order %s: %s",
    #                 order.name, move_ids
    #             )
    #             print(f"[DEBUG] Removing unwanted stock moves for Sale Order {order.name}: {move_ids}")
                
    #             unwanted_moves._action_cancel()
    #             unwanted_moves.unlink()
        
    #         subject = _("Sale Order %s created — follow up on material outward process") % order.name
    #         body_html = Markup("""
    #             <p>Dear Delivery Team,</p>
    #             <p>The sale order <strong>%s</strong> has been confirmed.</p>
    #             <p>Please follow up on the material outward process.</p>
    #         """ % (order.name))

    #         email_from = self.env.user.partner_id.email

    #         # fetch the group by xml_id
    #         group = self.env.ref("__export__.res_groups_80_412e5ec9", raise_if_not_found=False)

    #         # collect all users' emails in that group
    #         recipients = []
    #         partners = []
    #         if group:
    #             recipients = group.users.mapped("partner_id.email")
    #             recipients = [email for email in recipients if email]  # remove empty emails
    #             partners = group.users.mapped("partner_id")

    #         # send mail
    #         mail_values = {
    #             'subject': subject,
    #             'body_html': body_html,
    #             'email_from': email_from,
    #             'email_to': ",".join(recipients),
    #         }
    #         self.env['mail.mail'].create(mail_values).send()

    #         # post message on picking if exists
    #         if order.picking_ids:
    #             order.picking_ids[0].message_post(
    #                 subject=subject,
    #                 body=body_html,
    #                 message_type='notification'
    #             )

    #             # create activities for group users
    #             activity_type = self.env.ref("mail.mail_activity_data_todo")  # Standard To-do activity
    #             for partner in partners:
    #                 self.env['mail.activity'].create({
    #                     'res_model_id': self.env['ir.model']._get_id('stock.picking'),
    #                     'res_id': order.picking_ids[0].id,
    #                     'activity_type_id': activity_type.id,
    #                     'summary': "Follow up on outward process",
    #                     'note': body_html,
    #                     'user_id': partner.user_ids[:1].id if partner.user_ids else False,
    #                     'date_deadline': fields.Date.today(),
    #                 })
            
    #         zero_stock_products = order.order_line.filtered(
    #             lambda l: l.product_id.type == 'consu' and l.product_id.qty_available <= 0
    #         ).mapped('product_id')

    #         if zero_stock_products:
    #             # Send email alert
    #             # procurement_emails = self.env.ref("__export__.res_groups_79_862f912f", raise_if_not_found=False)
    #             group = self.env.ref("__export__.res_groups_79_862f912f", raise_if_not_found=False)
    #             email_from = self.env.user.partner_id.email

    #             # collect all users' emails in that group
    #             recipients = []
    #             partners = []
    #             if group:
    #                 recipients = group.users.mapped("partner_id.email")
    #                 recipients = [email for email in recipients if email]  # remove empty emails
    #                 partners = group.users.mapped("partner_id")

    #             subject = "Stock Alert: Zero On-hand Quantity"

    #             body = """
    #                 <p>Dear Procurement Team,</p>
    #                 <p>The following product(s) have zero or insufficient stock at the time of confirming a Sales Order. Please take the necessary actions to restock or notify the concerned team.</p>
    #                 <p>Kindly initiate a purchase order from the corresponding vendor for the below listed product(s):</p>
    #                 <ul>
    #             """

    #             for product in zero_stock_products:
    #                 body += f"<li><strong>{product.display_name}</strong> - Current Available Quantity: <strong>{product.qty_available}</strong></li>"

    #             body += "</ul>"

    #             body = Markup(body)

    #             # Send Email
    #             self.env['mail.mail'].create({
    #                 'mail_server_id': 9,
    #                 'email_from': email_from,
    #                 'subject': subject,
    #                 'body_html': body,
    #                 'email_to': ",".join(recipients),
    #             }).send()

    #             # Post message in chatter
    #             order.message_post(
    #                 subject=subject,
    #                 body=body,
    #                 message_type='notification'
    #             )

    #             # Create activities for group users
    #             activity_type = self.env.ref("mail.mail_activity_data_todo")  # Standard To-Do activity
    #             for partner in partners:
    #                 self.env['mail.activity'].create({
    #                     'res_model_id': self.env['ir.model']._get_id('sale.order'),
    #                     'res_id': order.id,
    #                     'activity_type_id': activity_type.id,
    #                     'summary': "Stock Alert - Restock Required",
    #                     'note': body,
    #                     'user_id': partner.user_ids[:1].id if partner.user_ids else False,
    #                     'date_deadline': fields.Date.today(),
    #                 })

    #     return res

    # def action_confirm(self):
    #     start_time = time.time()
    #     _logger.info("🔹 Starting action_confirm for %d sale orders", len(self))

    #     res = super().action_confirm()

    #     # Preload references once (avoid repeated env.ref lookups)
    #     delivery_group = self.env.ref("__export__.res_groups_80_412e5ec9", raise_if_not_found=False)
    #     procurement_group = self.env.ref("__export__.res_groups_79_862f912f", raise_if_not_found=False)
    #     activity_type = self.env.ref("mail.mail_activity_data_todo")

    #     delivery_recipients, procurement_recipients = [], []
    #     delivery_partners, procurement_partners = [], []

    #     if delivery_group:
    #         delivery_partners = delivery_group.users.mapped("partner_id")
    #         delivery_recipients = [e for e in delivery_partners.mapped("email") if e]

    #     if procurement_group:
    #         procurement_partners = procurement_group.users.mapped("partner_id")
    #         procurement_recipients = [e for e in procurement_partners.mapped("email") if e]

    #     for order in self:
    #         order_start = time.time()
    #         _logger.info("⚙️ Processing Sale Order %s", order.name)

    #         # 1️⃣ Assign picking type
    #         picking_type_map = {"mumbai": 2, "surat": 14}
    #         picking_type_id = picking_type_map.get(order.location)
    #         if picking_type_id:
    #             valid_pickings = order.picking_ids.filtered(lambda p: p.state not in ("done", "cancel"))
    #             valid_pickings.write({'picking_type_id': picking_type_id})
    #             for picking in valid_pickings:
    #                 _logger.info(
    #                     "✅ Updated picking %s to operation type %s for SO %s",
    #                     picking.name, picking_type_id, order.name
    #                 )

    #         # 2️⃣ Remove unwanted moves
    #         unwanted_moves = order.picking_ids.move_ids_without_package.filtered(
    #             lambda m: m.sale_line_id and m.sale_line_id.is_not_available
    #         )
    #         if unwanted_moves:
    #             move_ids = unwanted_moves.ids
    #             _logger.info("🗑 Removing %d unwanted stock moves for SO %s", len(move_ids), order.name)
    #             unwanted_moves._action_cancel()
    #             unwanted_moves.unlink()

    #         # 3️⃣ Notify delivery team (async mail queue)
    #         if delivery_recipients:
    #             subject = _("Sale Order %s confirmed — Follow up on material outward") % order.name
    #             body_html = Markup(f"""
    #                 <p>Dear Delivery Team,</p>
    #                 <p>The sale order <strong>{order.name}</strong> has been confirmed.</p>
    #                 <p>Please follow up on the material outward process.</p>
    #             """)

    #             self.env["mail.mail"].create({
    #                 "subject": subject,
    #                 "body_html": body_html,
    #                 "email_from": self.env.user.partner_id.email,
    #                 "email_to": ",".join(delivery_recipients),
    #             })

    #             # Post message and create activities on picking
    #             if order.picking_ids:
    #                 first_picking = order.picking_ids[0]
    #                 first_picking.message_post(subject=subject, body=body_html)

    #                 # Create activities for delivery team users on picking record
    #                 for partner in delivery_partners:
    #                     self.env['mail.activity'].create({
    #                         'res_model_id': self.env['ir.model']._get_id('stock.picking'),
    #                         'res_id': first_picking.id,
    #                         'activity_type_id': activity_type.id,
    #                         'summary': "Follow up on outward process",
    #                         'note': body_html,
    #                         'user_id': partner.user_ids[:1].id if partner.user_ids else False,
    #                         'date_deadline': fields.Date.today(),
    #                     })

    #         # 4️⃣ Handle zero stock products
    #         zero_stock_products = order.order_line.filtered(
    #             lambda l: l.product_id.type == "consu" and l.product_id.qty_available <= 0
    #         ).mapped("product_id")

    #         if zero_stock_products and procurement_recipients:
    #             product_info = "".join(
    #                 f"<li><strong>{p.display_name}</strong> — Qty: {p.qty_available}</li>"
    #                 for p in zero_stock_products
    #             )
    #             body_html = Markup(f"""
    #                 <p>Dear Procurement Team,</p>
    #                 <p>The following products have zero or insufficient stock:</p>
    #                 <ul>{product_info}</ul>
    #             """)
    #             subject = "⚠️ Stock Alert: Zero On-hand Quantity"

    #             self.env["mail.mail"].create({
    #                 "mail_server_id": 9,
    #                 "email_from": self.env.user.partner_id.email,
    #                 "subject": subject,
    #                 "body_html": body_html,
    #                 "email_to": ",".join(procurement_recipients),
    #             })

    #             order.message_post(subject=subject, body=body_html)

    #             # Create activities for procurement team on sale order
    #             for partner in procurement_partners:
    #                 self.env["mail.activity"].sudo().create({
    #                     "res_model_id": self.env["ir.model"]._get_id("sale.order"),
    #                     "res_id": order.id,
    #                     "activity_type_id": activity_type.id,
    #                     "summary": "Stock Alert — Restock Required",
    #                     "note": body_html,
    #                     "user_id": partner.user_ids[:1].id if partner.user_ids else False,
    #                     "date_deadline": fields.Date.today(),
    #                 })

    #         _logger.info("⏱ Finished SO %s in %.2fs", order.name, time.time() - order_start)

    #     # Send all queued emails after loop
    #     self.env["mail.mail"].search([("state", "=", "outgoing")]).send()

    #     _logger.info("✅ Completed action_confirm for %d SOs in %.2fs", len(self), time.time() - start_time)
    #     return res


        # @api.depends('state', 'invoice_status', 'payment_ids.state')
    # def _compute_sdk_augmont_status(self):
    #     for order in self:
    #         status = False

    #         # Order confirmed
    #         if order.state == 'sale':
    #             status = 'confirmed'

    #         if order.delivery_status == 'started':
    #             status = 'In QC process'

    #         # Delivery completed
    #         if order.delivery_status == 'full':
    #             status = 'Delivered'
                
    #         if order.picking_ids.return_id:
    #             status = 'Return of Order'

    #         if order.payment_ids and order.payment_ids.state in ['draft','in_process']:
    #             status = 'Payment Pending'

    #         if order.payment_ids and order.payment_ids.state == 'paid':
    #             status = 'Payment Completed'
                
    #         # Fully invoiced
    #         if order.invoice_status == 'invoiced':
    #             status = 'Order Completed'
                
    #         if order.state == 'cancel':
    #             status = 'Cancelled'
                
    #         if order.state == 'draft':
    #             status = 'Diamond Booked'

    #         order.sdk_augmont_status = status


        # @api.onchange('availability_status')
    # def onchange_product_template_id(self):
    #     self.order_id.action_update_augmont_status_manually()
   
    # @api.depends('is_available', 'is_not_available', 'availability_status')
    # def _compute_available_status(self):
    #     for rec in self:
    #         # if rec.picking_id.state == 'done':
    #         if rec.is_available:
    #             rec.availability_status = 'available'
    #         if rec.is_not_available:
    #             rec.availability_status = 'not_available'
            # else:
            #     rec.qc_status = False

    # def action_available(self):
    #     # if self.is_available:
            

    #     self.availability_status = 'available'
    #     self.is_available = True
    #     self.is_not_available = False

            

    # def action_not_available(self):
    #     for move in self:
    #         self.product_uom_qty = 0
    #         move.is_not_available = True
    #         move.availability_status = 'not_available'
    #         move.is_available = False
            
            
    # def action_open_product_popup(self):
    #     self.ensure_one()

    #     context = dict(self._context or {})
    #     context.update({
    #         'default_sale_order_line_id': self.id if self.id else False,
    #         'default_sale_order_id': self.order_id.id if self.order_id else False,
    #     })

    #     print("DEBUG CONTEXT:", context)

    #     return {
    #         'name': "Create Product",
    #         'type': 'ir.actions.act_window',
    #         'res_model': 'product.template',
    #         'view_mode': 'form',
    #         'target': 'new',
    #         'context': context,
    #     }
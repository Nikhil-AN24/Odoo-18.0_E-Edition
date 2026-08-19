from odoo import http
from odoo.http import request, Response
from markupsafe import Markup
import logging
import json
from datetime import datetime

_logger = logging.getLogger(__name__)

ALLOWED_STATUSES = {
    'Diamond Booked', 'Confirmed', 'Not available', 'In QC process', 'Payment Pending', 'Cancelled', 'Payment Completed', 'Dispatched', 'Return of Order', 'Re - Dispatched', 'Delivered', 'Order Completed', 'QC Fail'
}

# ============================================================================
# Map order-level status → line-level availability_status selection key.
# Used to keep the Availability column in Procurement in sync when the
# website sends a per-order-number status update.
# ============================================================================
STATUS_TO_LINE_AVAILABILITY = {
    'Diamond Booked': 'diamond_booked',
    'Confirmed': 'confirmed',
    'Not available': 'not_available',
    'Cancelled': 'cancelled',
    'In QC process': 'in_qc_process',
    'QC Fail': 'qc_fail',
    'Payment Pending': 'payment_pending',
    'Payment Completed':'payment_completed',
    'Dispatched': 'dispatched',
    'Return of Order': 'return_of_order',
    'Re - Dispatched': 're_dispatched',
    'Delivered': 'delivered',
    'Order Completed': 'order_completed',
}

class AugmontBidirectionalAPI(http.Controller):

    # ============================================================================
    # ENDPOINT 1: Update Order Status (Website → Odoo)
    # ============================================================================
    @http.route('/api/v1/odoo/order/status/update', type='http', auth='public', methods=['POST'], csrf=False)
    def update_order_status(self, **kwargs):
        """
        Update order status for multiple orders from website to Odoo.

        Payload Example (for multiple orders with the SAME status):
        {
            "orders": [
                { "orderNumber": "571938" },
                { "orderNumber": "571939" },
                { "orderNumber": "571940" }
            ],
            "status": "Confirmed",
            "utr": "RGCHT123456",
            "courierPartnerName": "Blue Dart",
            "trackingNumber": "BD123456",
            "trackingUrl": "https://...",
            "comment": "Updated by customer",
            "updatedBy": {
                "name": "John Doe",
                "email": "john@example.com",
                "role": "Customer"
            }
        }

        Payload Example (for multiple orders with POTENTIALLY DIFFERENT statuses or details):
        {
            "orders": [
                { "orderNumber": "571938", "status": "Confirmed", "utr": "UTR1", "courierPartnerName": "BP",... },
                { "orderNumber": "571939", "status": "Dispatched", "trackingNumber": "TRACK2",... },
                { "orderNumber": "571940", "status": "Payment Pending",... }
            ],
            "updatedBy": {
                "name": "John Doe",
                "email": "john@example.com",
                "role": "Customer"
            }
        }
        """
        _logger.info("📥 [WEBSITE→ODOO] Bulk Status update request received")
        overall_result = {
            'status': 'success',
            'message': 'Processed multiple order status updates',
            'results': []
        }
        successful_updates = []
        failed_updates = []

        try:
            # ------------------------------------------------------------------
            # Auth
            # ------------------------------------------------------------------
            auth_header = request.httprequest.headers.get('Authorization', '')
            if not auth_header.startswith('Bearer '):
                _logger.warning("🔒 [AUTH FAIL] Missing or invalid Authorization header")
                return Response(
                    json.dumps({'status': 'error', 'message': 'Missing Authorization header', 'code': 401}),
                    content_type='application/json',
                    status=401
                )

            token = auth_header.split(' ')[1]
            Param = request.env['ir.config_parameter'].sudo()
            expected_token = Param.get_param('website.api.token', default='')

            if not expected_token:
                _logger.error("⚠️ [CONFIG] website.api.token not set in system parameters!")
                return Response(
                    json.dumps({'status': 'error', 'message': 'Server configuration error', 'code': 500}),
                    content_type='application/json',
                    status=500
                )

            if token!= expected_token:
                _logger.warning("🔒 [AUTH FAIL] Invalid token")
                return Response(
                    json.dumps({'status': 'error', 'message': 'Invalid API token', 'code': 401}),
                    content_type='application/json',
                    status=401
                )

            _logger.info("✅ [AUTH] Token validated")

            # ------------------------------------------------------------------
            # Parse
            # ------------------------------------------------------------------
            try:
                data = json.loads(request.httprequest.data)
            except json.JSONDecodeError as e:
                _logger.error(f"❌ [PARSE FAIL] Invalid JSON: {str(e)}")
                return Response(
                    json.dumps({'status': 'error', 'message': f'Invalid JSON: {str(e)}', 'code': 400}),
                    content_type='application/json',
                    status=400
                )

            orders_to_process = data.get('orders')
            if not orders_to_process or not isinstance(orders_to_process, list):
                _logger.warning("❌ [VALIDATION FAIL] 'orders' list is required in the payload.")
                return Response(
                    json.dumps({'status': 'error', 'message': "'orders' list is required in the payload", 'code': 400}),
                    content_type='application/json',
                    status=400
                )

            # Get common data (if provided at the root level)
            common_status = data.get('status')
            common_utr = data.get('utr')
            common_courier = data.get('courierPartnerName')
            common_tracking = data.get('trackingNumber')
            common_tracking_url = data.get('trackingUrl')
            common_comment = data.get('comment')
            common_updated_by = data.get('updatedBy', {}) 

            # Loop through each order in the 'orders' list
            for order_data in orders_to_process:
                order_number = order_data.get('orderNumber')
                # Use order-specific status if present, else fall back to common status
                new_status = order_data.get('status', common_status)
                utr = order_data.get('utr', common_utr)
                courier = order_data.get('courierPartnerName', common_courier)
                tracking = order_data.get('trackingNumber', common_tracking)
                tracking_url = order_data.get('trackingUrl', common_tracking_url)
                comment = order_data.get('comment', common_comment)
                updated_by = order_data.get('updatedBy', common_updated_by) 

                result = {'orderNumber': order_number, 'status': 'failed', 'message': ''}

                if not order_number:
                    result['message'] = 'orderNumber is required for each item in the orders list'
                    failed_updates.append(result)
                    _logger.warning(f"❌ [VALIDATION FAIL] {result['message']}")
                    continue

                if not new_status:
                    result['message'] = f'status is required for order {order_number}'
                    failed_updates.append(result)
                    _logger.warning(f"❌ [VALIDATION FAIL] {result['message']}")
                    continue

                if new_status not in ALLOWED_STATUSES:
                    result['message'] = f'Invalid status "{new_status}" for order {order_number}. Allowed: {sorted(ALLOWED_STATUSES)}'
                    failed_updates.append(result)
                    _logger.warning(f"❌ [VALIDATION FAIL] {result['message']}")
                    continue

                # Validate UTR for Payment Completed status
                if new_status == 'Payment Completed' and not utr:
                    result['message'] = f'UTR number is required for Payment Completed status for order {order_number}'
                    failed_updates.append(result)
                    _logger.warning(f"❌ [VALIDATION FAIL] {result['message']}")
                    continue

                # Validate courier info and tracking number for Dispatched status (REQUIRED)
                if new_status == 'Dispatched':
                    if not tracking:
                        result['message'] = f'trackingNumber is required for Dispatched status for order {order_number}'
                        failed_updates.append(result)
                        _logger.warning(f"❌ [VALIDATION FAIL] {result['message']}")
                        continue
                    if not courier:
                        result['message'] = f'courierPartnerName is required for Dispatched status for order {order_number}'
                        failed_updates.append(result)
                        _logger.warning(f"❌ [VALIDATION FAIL] {result['message']}")
                        continue

                # Optional validation for Re-Dispatched (warning only)
                if new_status == 'Re - Dispatched':
                    if not tracking:
                        _logger.warning(f'No tracking number provided for {new_status} status for order {order_number}')
                    if not courier:
                        _logger.warning(f'No courier partner name provided for {new_status} status for order {order_number}')

                # ------------------------------------------------------------------
                # Process individual order
                # ------------------------------------------------------------------
                try:
                    _logger.info(f"🔍 [SEARCH] Looking for order line for order number: {order_number}")
                    order_line = request.env['sale.order.line'].sudo().search([
                        ('order_number', '=', order_number)
                    ], limit=1)

                    if not order_line:
                        result['message'] = f'Sale Order Line with order number "{order_number}" not found'
                        failed_updates.append(result)
                        _logger.warning(f"❌ [NOT FOUND] {result['message']}")
                        continue

                    sale_order = order_line.order_id
                    old_order_status = sale_order.sdk_augmont_status
                    old_line_status = order_line.availability_status

                    _logger.info(
                        f"✅ [FOUND] Order: {sale_order.name} | "
                        f"Line availability: {old_line_status} | "
                        f"Order status: {old_order_status} → {new_status}"
                    )

                    availability_value = STATUS_TO_LINE_AVAILABILITY.get(new_status)
                    if availability_value:
                        order_line.sudo().with_context(from_website_api=True).write({
                            'availability_status': availability_value
                        })
                        _logger.info(
                            "🔄 [LINE UPDATE] order_number %s | availability: %s → %s",
                            order_number, old_line_status, availability_value
                        )

                    sale_order.order_line.invalidate_recordset(['availability_status'])
                    sale_order.invalidate_recordset(['sdk_augmont_status'])
                    sale_order.with_context(
                        from_website_api=True,
                        _in_compute_augmont_status=True,
                        skip_api_sync=True,
                    )._compute_order_status_from_lines()
                    new_computed_invoice_status = sale_order.sdk_augmont_status
                    if new_computed_invoice_status:
                        request.env.cr.execute(
                            "UPDATE sale_order SET sdk_augmont_status = %s WHERE id = %s",
                            (new_computed_invoice_status, sale_order.id)
                        )
                        sale_order.invalidate_recordset(['sdk_augmont_status'])
                        _logger.info(
                            "🔄 [API RECOMPUTE] Order %s: sdk_augmont_status persisted as '%s'",
                            sale_order.name, new_computed_invoice_status,
                        )

                    if (new_computed_invoice_status == 'Order Confirmed' and sale_order.state in ('draft', 'sent')):
                        try:
                            _logger.info(
                                "🔄 Auto-confirming Sale Order %s "
                                "(sdk_augmont_status='Order Confirmed' | "
                                "triggered by line %s → %s | state='%s')",
                                sale_order.name, order_number, availability_value, sale_order.state
                            )
                            sale_order.sudo().with_context(
                                from_website_api=True
                            ).action_confirm_wrapper()
                            _logger.info(
                                "✅ Sale Order %s confirmed — state: %s | "
                                "Stage bar: Draft Order → Confirmed Order",
                                sale_order.name, sale_order.state
                            )
                        except Exception as e:
                            _logger.error(
                                "❌ Failed to auto-confirm %s: %s", sale_order.name, e
                            )

                    if (availability_value == 'cancelled' and new_computed_invoice_status == 'Cancelled' and sale_order.state not in ['cancel', 'done']):
                        try:
                            _logger.info(
                                "🔄 Auto-cancelling Sale Order %s (all lines cancelled via website)",
                                sale_order.name
                            )
                            sale_order.sudo().with_context(from_website_api=True).action_cancel()
                            _logger.info(
                                "✅ Sale Order %s cancelled — state: %s",
                                sale_order.name, sale_order.state
                            )
                        except Exception as e:
                            _logger.error(
                                "❌ Failed to auto-cancel %s: %s", sale_order.name, e
                            )

                    # Update order-level fields if provided
                    update_vals = {}
                    if new_status == 'Payment Completed' and utr:
                        update_vals['utr_number'] = utr
                    if new_status in ['Dispatched', 'Re - Dispatched']:
                        if courier:
                            update_vals['courier_partner_name'] = courier
                        if tracking:
                            update_vals['tracking_number'] = tracking
                        if tracking_url:
                            update_vals['tracking_url'] = tracking_url

                    if update_vals:
                        sale_order.sudo().with_context(from_website_api=True).write(update_vals)

                    final_invoice_status = sale_order.sdk_augmont_status

                    # Chatter log
                    chatter_body = f""" <p><b>✅ Status Updated from Website (via Bulk API)</b></p> <ul>
                        <li><b>Order Number:</b> {order_number}</li>
                        <li><b>Line Status:</b> {old_line_status} → {order_line.availability_status}</li>
                        <li><b>Invoice Status:</b> {old_order_status} → {final_invoice_status}</li>
                    """
                    if utr: chatter_body += f"<li><b>UTR:</b> {utr}</li>"
                    if courier: chatter_body += f"<li><b>Courier:</b> {courier}</li>"
                    if tracking: chatter_body += f"<li><b>Tracking:</b> {tracking}</li>"
                    if comment: chatter_body += f"<li><b>Comment:</b> {comment}</li>"
                    if updated_by:
                        user_name = updated_by.get('name', 'Unknown')
                        user_email = updated_by.get('email', 'N/A')
                        user_role = updated_by.get('role', 'User')
                        chatter_body += (f"<li><b>Updated By:</b> {user_name} ({user_email}) - {user_role}</li>")
                    chatter_body += (f"<li><b>Timestamp:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</li></ul>")

                    sale_order.message_post(
                        body=Markup(chatter_body),
                        subject=f"Status Update via Bulk API for Order {order_number}",
                        message_type='notification'
                    )

                    _logger.info(
                        f"✅ [SUCCESS] Processed order {order_number} | Line: {old_line_status} → {order_line.availability_status} | "
                        f"Invoice: {old_order_status} → {final_invoice_status}"
                    )

                    successful_updates.append({
                        'orderNumber': order_number,
                        'status': 'success',
                        'message': 'Status updated successfully',
                        'data': {
                            'invoiceNumber': sale_order.sdk_augmont_number or '',
                            'orderNumber': order_number,
                            'oldLineStatus': old_line_status or '',
                            'newLineStatus': order_line.availability_status,
                            'oldInvoiceStatus': old_order_status,
                            'newInvoiceStatus': final_invoice_status,
                            'updatedAt': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        }
                    })

                except Exception as e:
                    _logger.exception(f"❌ [EXCEPTION] Failed to process order {order_number}: {str(e)}")
                    result['message'] = f'Server error processing order {order_number}: {str(e)}'
                    failed_updates.append(result)
                    continue

            overall_result['results'] = successful_updates + failed_updates
            if failed_updates:
                overall_result['status'] = 'partial_success'
                overall_result['message'] = f'Successfully updated {len(successful_updates)} orders, failed to update {len(failed_updates)} orders.'

            return Response(
                json.dumps(overall_result, default=str),
                content_type='application/json',
                status=200 if not failed_updates else 207 # 207 Multi-Status for partial success
            )

        except json.JSONDecodeError:
            _logger.error("❌ [ERROR] Invalid JSON in request body (outside order processing loop)")
            return Response(
                json.dumps({'status': 'error', 'message': 'Invalid JSON format in request body', 'code': 400}),
                content_type='application/json',
                status=400
            )
        except Exception as e:
            _logger.exception(f"❌ [FATAL ERROR] Bulk status update failed unexpectedly: {str(e)}")
            return Response(
                json.dumps({'status': 'error', 'message': f'Server error: {str(e)}', 'code': 500}),
                content_type='application/json',
                status=500
            )

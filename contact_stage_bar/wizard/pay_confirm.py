from odoo import models, fields, api, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)

class PaymentConfirmationWizard(models.TransientModel):
    _name = 'payment.confirmation.wizard'
    _description = 'Payment Confirmation Wizard'
    
    picking_id = fields.Many2one('stock.picking', string='Picking')
    sale_order_id = fields.Many2one('sale.order', string='Sale Order')
    utr_number = fields.Char('UTR Number', tracking=True)

    
    def action_confirm_yes(self):
        """User clicked Yes - Payment completed"""
        self.ensure_one()
        
        _logger.info(
            "💰 [PACK WIZARD - YES] Picking: %s | SO: %s | UTR: %s",
            self.picking_id.name if self.picking_id else 'N/A',
            self.sale_order_id.name if self.sale_order_id else 'N/A',
            self.utr_number or 'EMPTY'
        )
        
        # First: Update UTR Number in Sale Order
        if self.sale_order_id and self.utr_number:
            self.sale_order_id.write({
                'utr_number': self.utr_number
            })
            _logger.info(
                "✅ [UTR SAVED] Order: %s | UTR: %s",
                self.sale_order_id.name,
                self.utr_number
            )
            self.env.cr.commit()
        
        # Second: Update picking state to pack and SO status to 'Payment Completed'
        if self.picking_id and self.picking_id.state == 'assigned':
            # Mark picking as packed
            self.picking_id.write({
                'state': 'pack',
                'is_packed': True
            })
            _logger.info(
                "📦 [PICKING PACKED] %s | State: assigned → pack",
                self.picking_id.name
            )
            
            # Update sale order status to 'Payment Completed'
            if self.sale_order_id:
                old_status = self.sale_order_id.sdk_augmont_status
                
                #Update ONLY payment_pending lines to 'payment_completed'
                # DO NOT update qc_fail, cancelled, not_available, confirmed, or in_qc_process
                # This prevents duplicate order creation and maintains correct workflow
                payment_pending_lines = self.sale_order_id.order_line.filtered(
                    lambda l: l.availability_status == 'payment_pending'
                )
                if payment_pending_lines:
                    # Set context flag to prevent auto-confirm from triggering
                    payment_pending_lines.with_context(from_pack_wizard=True).write({
                        'availability_status': 'payment_completed'
                    })
                    _logger.info(
                        "💳 [PACK] Updated %d lines: payment_pending → payment_completed for order %s",
                        len(payment_pending_lines), self.sale_order_id.name
                    )
                
                # Update Order Status
                self.sale_order_id.with_context(bypass_status_validation=True).write({
                    'sdk_augmont_status': 'Payment Completed'
                })
                
                _logger.info(
                    "📊 [STATUS UPDATE] Order: %s | %s → Payment Completed",
                    self.sale_order_id.name,
                    old_status
                )
                
                # Call Augmont API with UTR included
                try:
                    self.sale_order_id._call_augmont_status_api('Payment Completed')
                    _logger.info(
                        "✅ [API SUCCESS] Order: %s | Status: Payment Completed sent to Augmont",
                        self.sale_order_id.name
                    )
                except Exception as e:
                    _logger.error(
                        "❌ [API FAIL] Order: %s | Payment Completed sync failed | Error: %s",
                        self.sale_order_id.name, e
                    )
            
            self.env.cr.commit()
            
        return {'type': 'ir.actions.act_window_close'}

    def action_confirm_no(self):
        """User clicked No - Payment not completed"""
        self.ensure_one()
        _logger.info(
            "❌ [PACK WIZARD - NO] Picking: %s | User cancelled",
            self.picking_id.name if self.picking_id else 'N/A'
        )
        return {'type': 'ir.actions.act_window_close'}

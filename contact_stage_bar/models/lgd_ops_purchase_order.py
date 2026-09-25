import logging
from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    lgd_po_voided = fields.Boolean(
        string='Returned — do not pay', copy=False,
        help="Set when every stone on this order went back to the vendor but "
             "the order could not be cancelled. Accounting must not pay it.")

    def _lgd_log(self, body):

        for order in self:
            try:
                order._message_log(body=body)
            except Exception:
                _logger.warning(
                    "Could not log to %s chatter: %s", order.name, body,
                    exc_info=True)

    def _lgd_confirm_ready_stones(self):
        """confirming a PO in Odoo confirms every line, so when only
        some stones are ready the ready ones are copied to their own RFQ and
        that one is confirmed.
        Copy, then zero. Lines are never moved between orders and never
        unlinked, so nothing is lost if a copy fails.
        """
        self.ensure_one()
        ready = self.order_line.filtered(
            lambda l: l.lgd_stage == 'qc_passed' and l.product_qty > 0)
        if not ready:
            raise UserError(_("No stones on this RFQ have passed QC yet."))
        rest = (self.order_line - ready).filtered(lambda l: l.product_qty > 0)

        if not rest:
            self.button_confirm()
            return self

        new_po = self.copy({'order_line': []})
        for line in ready:
            line.copy({'order_id': new_po.id})
            line.product_qty = 0
            line.lgd_stage = 'split'
        new_po.button_confirm()

        new_po._lgd_log(body=_("Split from %s.") % self.name)
        self._lgd_log(
            body=_("Ready stones split to %s and confirmed.") % new_po.name)
        return new_po

    # ── QC inspection by invoice ────────────────────────────────────────────
    def _lgd_qc_selected_lines(self):
        """The stones QC ticked in the Products grid, still with QC."""
        self.ensure_one()
        lines = self.order_line.filtered(
            lambda l: l.lgd_qc_selected and l.lgd_stage == 'in_qc')
        if not lines:
            raise UserError(_(
                "Tick the products you want to decide on first (only stones "
                "currently With QC can be passed or failed)."))
        return lines

    def action_lgd_qc_pass_selected(self):
        """Pass every ticked stone on this invoice in one go."""
        self.ensure_one()
        return self._lgd_qc_selected_lines().action_lgd_pass()

    def action_lgd_qc_fail_selected(self):
        """Fail the ticked stones — the wizard collects one reason for all."""
        self.ensure_one()
        return self._lgd_qc_selected_lines().action_lgd_open_fail_wizard()

    def action_lgd_confirm_ready_stones(self):
        """the button on the PO form and the Ready to Commit list."""
        self.env['purchase.order.line']._lgd_check_group(
            'contact_stage_bar.group_lgd_procurement')
        for order in self:
            order._lgd_confirm_ready_stones()
        return True


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    def action_lgd_confirm_ready_stones(self):
        """the Ready to Commit list is a list of lines, but the split and confirm happen once per order."""
        self._lgd_check_group('contact_stage_bar.group_lgd_procurement')
        for order in self.order_id:
            order._lgd_confirm_ready_stones()
        return True

    def _lgd_void_or_cancel_po(self):
        """called after a line is zeroed or returned.
        Cancel the order when nothing is left on it and no stock ever moved;
        otherwise flag it so Accounting does not pay. When stones are still
        live on the order, leave it alone — the zeroed line is enough.
        """
        self.ensure_one()
        order = self.order_id
        if not order:
            return
        spent_stages = ('rejected', 'failed', 'returned')
        still_live = order.order_line.filtered(
            lambda l: l.product_qty > 0 and l.lgd_stage not in spent_stages)
        if still_live:
            return

        # Button_cancel refuses only when a posted vendor bill exists, so
        # the "nothing received" test has to come first.
        if not any(m.state == 'done' for m in order.order_line.move_ids):
            try:
                order.button_cancel()
                return
            except UserError as err:
                _logger.info(
                    "Could not cancel %s (%s); neutralising instead.",
                    order.name, err)

        if not order.lgd_po_voided:
            order.lgd_po_voided = True
            order._lgd_log(body=_(
                "Stone(s) returned to vendor. Nothing is billable on this "
                "order — do not pay."))

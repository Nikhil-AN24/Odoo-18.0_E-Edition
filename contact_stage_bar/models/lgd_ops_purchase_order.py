import logging
from odoo import _, api, fields, models
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
                order.sudo()._message_log(body=body)
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
            # Every lgd_* field and sale_line_id is copy=False, so a bare
            # copy() lands on the new RFQ with no customer link and no stage:
            # the stone would vanish from Awaiting Acceptance and reappear in
            # Expected Stones as if it had never arrived. Carry the link and
            # the inward/QC audit trail across explicitly (U2).
            line.copy(dict(line._lgd_split_copy_defaults(),
                           order_id=new_po.id))
            line.product_qty = 0
            line.lgd_stage = 'split'
        new_po.button_confirm()

        new_po._lgd_log(body=_("Split from %s.") % self.name)
        self._lgd_log(
            body=_("Ready stones split to %s and confirmed.") % new_po.name)
        return new_po

    # ── QC inspection by invoice ────────────────────────────────────────────

    def _lgd_qc_invoice_domain(self):
        """Every stone with QC that belongs to this order's invoice."""
        self.ensure_one()
        if self.order_number:
            return [('lgd_stage', '=', 'in_qc'),
                    ('order_id.order_number', '=', self.order_number)]
        # No invoice number: the purchase order stands on its own rather
        # than silently joining every other unnumbered one.
        return [('lgd_stage', '=', 'in_qc'), ('order_id', '=', self.id)]

    def _lgd_qc_invoice_lines(self):
        self.ensure_one()
        return self.env['purchase.order.line'].search(
            self._lgd_qc_invoice_domain())

    lgd_qc_stone_count = fields.Integer(
        string="Stones", compute='_compute_lgd_qc_invoice_summary')
    lgd_qc_po_names = fields.Char(
        string="Purchase Orders", compute='_compute_lgd_qc_invoice_summary')
    lgd_qc_vendor_names = fields.Char(
        string="Vendors", compute='_compute_lgd_qc_invoice_summary')

    @api.depends('order_number', 'order_line.lgd_stage')
    def _compute_lgd_qc_invoice_summary(self):
        """What the one invoice row says: how many stones are waiting, and
        which purchase orders and vendors they are spread across."""
        for order in self:
            lines = order._lgd_qc_invoice_lines()
            orders = lines.order_id
            order.lgd_qc_stone_count = len(lines)
            order.lgd_qc_po_names = ", ".join(sorted(orders.mapped('name'))) or False
            order.lgd_qc_vendor_names = ", ".join(
                sorted({n for n in orders.mapped('partner_id.name') if n})) or False

    lgd_qc_invoice_primary = fields.Boolean(
        string="Invoice row",
        compute='_compute_lgd_qc_invoice_primary',
        search='_search_lgd_qc_invoice_primary',
        help="True on exactly one purchase order per invoice - the earliest "
             "raised. It is what makes To Inspect show one row per invoice "
             "instead of one per vendor.")

    @api.model
    def _lgd_qc_primary_ids(self):
        """The earliest purchase order of each invoice that has QC work.

        Computed in one pass rather than per record: deciding "am I the
        first?" one order at a time is a query per row, and this list is
        read for every row in To Inspect.
        """
        orders = self.sudo().search(
            [('order_line.lgd_stage', '=', 'in_qc')], order='id')
        seen, primary = set(), []
        for order in orders:
            key = order.order_number or 'po:%s' % order.id
            if key not in seen:
                seen.add(key)
                primary.append(order.id)
        return primary

    @api.depends('order_number', 'order_line.lgd_stage')
    def _compute_lgd_qc_invoice_primary(self):
        primary = set(self._lgd_qc_primary_ids())
        for order in self:
            order.lgd_qc_invoice_primary = order.id in primary

    def _search_lgd_qc_invoice_primary(self, operator, value):
        if operator not in ('=', '!='):
            raise UserError(_(
                "The invoice row flag can only be searched with = or !=."))
        ids = self._lgd_qc_primary_ids()
        wanted = (operator == '=') == bool(value)
        return [('id', 'in' if wanted else 'not in', ids)]

    def action_lgd_qc_open_invoice(self):
        """Open every stone with QC on this invoice, across its vendors."""
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id(
            'contact_stage_bar.action_lgd_qc_inspect_invoice')
        action['domain'] = self._lgd_qc_invoice_domain()
        action['display_name'] = _("Inspect %s") % (
            self.order_number or self.name)
        return action

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

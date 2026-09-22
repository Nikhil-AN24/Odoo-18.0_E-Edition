# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    # Back-link to the offline order this sale.order was created from.
    # Defined here (not in contact_stage_bar) because custom_sale_order loads
    # after contact_stage_bar, so the custom.sale.order comodel is available.
    custom_sale_order_id = fields.Many2one(
        'custom.sale.order', string='Offline Order',
        copy=False, readonly=True, index=True)

    @api.depends('custom_sale_order_id.customer_rfq_id.currency_id', 'order_source')
    def _compute_currency_id(self):
        """Offline orders inherit the currency from their Customer RFQ (whose
        prices they carry) instead of falling back to the company currency.
        The RFQ can be USD or INR (Procurement's choice), so the order — and its
        list Total — matches the figures the RFQ actually holds. Website orders
        keep the standard pricelist/company behaviour."""
        super()._compute_currency_id()
        for order in self:
            rfq = order.custom_sale_order_id.customer_rfq_id
            if order.order_source == 'offline' and rfq and rfq.currency_id:
                order.currency_id = rfq.currency_id

    @api.model
    def _backfill_offline_order_currency(self):
        """One-time (idempotent) alignment of EXISTING offline orders to their
        RFQ currency, so their list Total shows the right symbol. Skips any
        order that already has a posted invoice — changing an invoiced order's
        currency would clash with the posted move."""
        offline = self.sudo().search([
            ('order_source', '=', 'offline'),
            ('custom_sale_order_id.customer_rfq_id', '!=', False),
        ])
        safe = offline.filtered(
            lambda o: not o.invoice_ids.filtered(lambda m: m.state == 'posted'))
        fixed = safe.filtered(
            lambda o: o.custom_sale_order_id.customer_rfq_id.currency_id
            and o.currency_id != o.custom_sale_order_id.customer_rfq_id.currency_id)
        for order in fixed:
            order.currency_id = order.custom_sale_order_id.customer_rfq_id.currency_id
        return len(fixed)


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    # ── RFQ-derived pricing, surfaced on the (offline) Sale Order lines ──────
    # Offline orders are raised from a Customer RFQ, which holds the pricing the
    # salesperson worked with. These read straight from that RFQ via the
    # offline-order back-link, so the Sale Order lines can show the same three
    # figures the RFQ shows. Empty for website orders (no RFQ link). Computed
    # (not related) because the RFQ's Procurement Price/carat is a Float while
    # these are Monetary — related fields require the exact same type.
    # Stored so the editable Procurement Price/carat below has a reliable
    # currency to round against.
    rfq_currency_id = fields.Many2one(
        'res.currency', string='RFQ Currency', readonly=True, store=True,
        related='order_id.custom_sale_order_id.customer_rfq_id.currency_id')
    # Procurement Price/carat is SEEDED from the RFQ but EDITABLE by Procurement
    # (store=True + readonly=False): the compute fills it from the RFQ on create
    # (and backfills existing rows on upgrade), then Procurement can override it.
    # Because the compute depends only on the RFQ price — which doesn't change
    # after the offline order exists — a manual edit is not overwritten. The
    # view gates who may edit it (can_edit_vendor: Procurement / Procurement
    # Manager / Admin / SuperAdmin).
    procurement_price_per_carat = fields.Monetary(
        string='Procurement Price/carat', currency_field='rfq_currency_id',
        compute='_compute_procurement_price_per_carat',
        store=True, readonly=False)
    sales_price_per_carat = fields.Monetary(
        string='Sales Price/carat', readonly=True,
        currency_field='rfq_currency_id', compute='_compute_rfq_pricing')
    sales_price_per_stone = fields.Monetary(
        string='Sales Price/Stone', readonly=True,
        currency_field='rfq_currency_id', compute='_compute_rfq_pricing')
    # Whole-quote value from the RFQ, surfaced on the order-line info popup.
    order_total = fields.Monetary(
        string='Order Total', readonly=True, currency_field='rfq_currency_id',
        related='order_id.custom_sale_order_id.customer_rfq_id.order_total')
    # Vendor Total Price = Procurement Price/carat × Carat/Stone (the stone's
    # own carat weight). carat_weight is a Char, so it is parsed to a number.
    vendor_total_price = fields.Monetary(
        string='Vendor Total Price', readonly=True, currency_field='rfq_currency_id',
        compute='_compute_vendor_total_price')

    @api.depends('procurement_price_per_carat', 'carat_weight')
    def _compute_vendor_total_price(self):
        for line in self:
            try:
                carat = float(line.carat_weight) if line.carat_weight else 0.0
            except (TypeError, ValueError):
                carat = 0.0
            line.vendor_total_price = (line.procurement_price_per_carat or 0.0) * carat

    def _ensure_isolated_product(self):
        """Offline orders create N lines that all point to ONE product. Editing
        that product (certificate / IGI fetch) would change every line sharing
        it. So before such an edit, if this line's product is shared by other
        order lines, replace it with a private copy — the edit then affects only
        this stone. Runs sudo (cloning a product is a system action)."""
        for line in self.filtered(lambda l: l.product_template_id):
            tmpl = line.product_template_id
            shared = self.env['sale.order.line'].sudo().search_count([
                ('product_template_id', '=', tmpl.id), ('id', '!=', line.id)])
            if shared:
                new_tmpl = tmpl.sudo().copy({'name': tmpl.name})
                line.sudo().write({
                    'product_template_id': new_tmpl.id,
                    'product_id': new_tmpl.product_variant_id.id,
                })

    def write(self, vals):
        # Setting the Certificate Number writes through to the product; isolate
        # first so only this line's stone is affected, not its siblings.
        if vals.get('certificate'):
            self._ensure_isolated_product()
        return super().write(vals)

    def action_line_fetch_from_igi(self):
        """(i) popup — Fetch from IGI for this line's product. Delegates to the
        product's own IGI fetch (which reads its Certificate Number and self-
        guards on an empty number / non-IGI lab). On success we reload the popup
        so the freshly-fetched specs appear in the (related) General Information
        fields; a warning (not found / unavailable) is surfaced to the user.
        Restricted in the view to Procurement / Manager / Admin / SuperAdmin."""
        self.ensure_one()
        if not self.product_template_id:
            raise UserError(_("There is no product on this line to fetch."))
        # Ensure this line owns its product so the fetch only affects this stone.
        self._ensure_isolated_product()
        res = self.product_template_id.action_fetch_igi_data()
        params = res.get('params', {}) if isinstance(res, dict) else {}
        if params.get('type') == 'success':
            # Returning nothing makes the web client re-read this line, so the
            # related product-spec fields refresh with the fetched values.
            return True
        # not found / unavailable → show the warning notification as-is.
        return res

    def action_open_product_popup(self):
        """(i) button — order-aware info popup on the order line itself, so the
        Sales tab can show this line's Sales Price/carat and the order's Order
        Total (order/RFQ values a bare product cannot carry)."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'sale.order.line',
            'view_mode': 'form',
            'views': [(self.env.ref(
                'custom_sale_order.view_sale_order_line_info_popup').id, 'form')],
            'res_id': self.id,
            'target': 'new',
        }

    @api.depends('order_id.custom_sale_order_id.customer_rfq_id.price')
    def _compute_procurement_price_per_carat(self):
        for line in self:
            rfq = line.order_id.custom_sale_order_id.customer_rfq_id
            line.procurement_price_per_carat = rfq.price if rfq else 0.0

    @api.depends(
        'order_id.custom_sale_order_id.customer_rfq_id.sale_rate_per_carat',
        'order_id.custom_sale_order_id.customer_rfq_id.sale_price_per_stone')
    def _compute_rfq_pricing(self):
        for line in self:
            rfq = line.order_id.custom_sale_order_id.customer_rfq_id
            line.sales_price_per_carat = rfq.sale_rate_per_carat if rfq else 0.0
            line.sales_price_per_stone = rfq.sale_price_per_stone if rfq else 0.0

    @api.depends('order_id.custom_sale_order_id.customer_rfq_id.stone_type',
                 'order_id.custom_sale_order_id.customer_rfq_id.stone_certification_type')
    def _compute_stone_classification(self):
        """Offline orders are raised from a Customer RFQ, which records the
        stone type and certification Sales asked for, so take both from there
        instead of the website's lab-grown default."""
        super()._compute_stone_classification()
        for line in self.filtered(lambda l: not l.display_type):
            rfq = line.order_id.custom_sale_order_id.customer_rfq_id
            if rfq:
                line.stone_type = rfq.stone_type
                line.stone_certification_type = rfq.stone_certification_type

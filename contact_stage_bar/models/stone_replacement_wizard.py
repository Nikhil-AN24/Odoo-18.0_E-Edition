import re

from odoo import models, fields, api, _
from odoo.exceptions import UserError


class StoneReplacement(models.TransientModel):
    """Replace Stone wizard. Two entry modes:
      - IGI: enter the report number, the spec fills automatically.
      - Manual: type the spec by hand (GIA / other labs, or IGI fallback).
    All business logic lives on sale.order.line; this wizard only
    collects input and calls it, so the deferred engine can reuse the same code.
    """
    _name = 'stone.replacement'
    _description = 'Replace Stone'

    sale_line_id = fields.Many2one(
        'sale.order.line', string="Original Line", required=True, readonly=True)

    mode = fields.Selection(
        [('igi', 'IGI (report number)'),
         ('manual', 'Manual (GIA / other lab)')],
        string="Source", default='igi', required=True)

    igi_report_no = fields.Char(string="IGI Report Number")

    # ── Replacement stone spec (filled by IGI, or typed in Manual mode) ──────
    lab = fields.Char(string="Lab")
    shapes = fields.Char(string="Shape")
    weight_carat = fields.Char(string="Carat Weight")
    color = fields.Char(string="Colour")
    clarity = fields.Char(string="Clarity")
    cut = fields.Char(string="Cut")
    polish = fields.Char(string="Polish")
    symmetry = fields.Char(string="Symmetry")
    fluorescence_intensity = fields.Char(string="Fluorescence")
    measurements = fields.Char(string="Measurements")
    certificate = fields.Char(string="Certificate Number")
    stone_type = fields.Selection(
        [('natural', 'Natural'), ('lab_grown', 'Lab Grown')],
        string="Stone Type")

    # ── Vendor / price ─────────────────────────────────────
    vendor_id = fields.Many2one('res.partner', string="Vendor")
    gross_price_per_carat = fields.Float(
        string="Gross Price / Carat", digits=(16, 2),
        help="The vendor's listed price before any partnership discount.")
    carat_value = fields.Float(
        string="Carat", compute='_compute_wizard_carat', digits=(16, 2))
    gross_total = fields.Float(
        string="Gross Total", compute='_compute_gross_total', digits=(16, 2))
    vendor_confirmed = fields.Boolean(
        string="Vendor has confirmed this stone is available at this price")
    no_attribute_worse = fields.Boolean(
        string="No attribute is worse than the original")

    # ── Original stone, for side-by-side comparison ─────────────
    # No price shown: Procurement never sees the customer price.
    orig_product_name = fields.Char(
        related='sale_line_id.product_template_id.display_name',
        string="Original Stone", readonly=True)
    orig_shapes = fields.Char(related='sale_line_id.shapes', string="Orig Shape", readonly=True)
    orig_color = fields.Char(related='sale_line_id.color', string="Orig Colour", readonly=True)
    orig_clarity = fields.Char(related='sale_line_id.clarity', string="Orig Clarity", readonly=True)
    orig_cut = fields.Char(related='sale_line_id.cut', string="Orig Cut", readonly=True)
    orig_carat = fields.Char(related='sale_line_id.carat_weight', string="Orig Carat", readonly=True)
    orig_certificate = fields.Char(related='sale_line_id.certificate', string="Orig Certificate", readonly=True)
    orig_stone_type = fields.Selection(related='sale_line_id.stone_type', string="Orig Type", readonly=True)

    @api.depends('weight_carat')
    def _compute_wizard_carat(self):
        for wiz in self:
            value = 0.0
            if wiz.weight_carat:
                match = re.search(r'([\d.]+)', str(wiz.weight_carat))
                if match:
                    try:
                        value = float(match.group(1))
                    except (TypeError, ValueError):
                        value = 0.0
            wiz.carat_value = value

    @api.depends('gross_price_per_carat', 'carat_value')
    def _compute_gross_total(self):
        for wiz in self:
            wiz.gross_total = (wiz.gross_price_per_carat or 0.0) * (wiz.carat_value or 0.0)

    def action_fetch_igi(self):
        """IGI mode: pull the spec from the report number and fill the fields
        On not_found / unavailable, drop to manual"""
        self.ensure_one()
        from ..services import igi_service
        report = (self.igi_report_no or '').strip()
        if not report:
            raise UserError(_("Enter the IGI report number first."))
        result = igi_service.fetch_by_report_number(self.env, report)
        if not result or result.get('error'):
            self.mode = 'manual'
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _("IGI"),
                    'message': _("IGI could not return report %s — please enter "
                                 "the specs by hand.") % report,
                    'type': 'warning',
                    'sticky': False,
                },
            }
        self.write({
            'shapes': (result.get('shape') or '').title() or False,
            'weight_carat': str(result.get('carat_value')) if result.get('carat_value') else False,
            'color': result.get('color'),
            'clarity': result.get('clarity_norm'),
            'cut': result.get('cut'),
            'polish': result.get('polish'),
            'symmetry': result.get('symmetry'),
            'fluorescence_intensity': result.get('fluorescence'),
            'measurements': result.get('measurements'),
            'certificate': result.get('report_number') or report,
            'lab': 'IGI',
            'stone_type': 'lab_grown' if result.get('is_lab_grown') else 'natural',
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'stone.replacement',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
            'name': _("Replace Stone"),
        }

    def _spec_vals(self):
        self.ensure_one()
        return {
            'certificate': (self.certificate or '').strip() or False,
            'labs': self.lab or ('IGI' if self.mode == 'igi' else False),
            'shapes': self.shapes,
            'weight_carat': self.weight_carat,
            'color': self.color,
            'clarity': self.clarity,
            'cut': self.cut,
            'polish': self.polish,
            'symmetry': self.symmetry,
            'fluorescence_intensity': self.fluorescence_intensity,
            'measurements': self.measurements,
            'stone_type': self.stone_type,
        }

    def action_submit(self):
        """Validate, resolve the product, run the checks and price rule, then
        create the replacement."""
        self.ensure_one()
        line = self.sale_line_id
        if not self.vendor_confirmed:
            raise UserError(_(
                "You must tick 'Vendor has confirmed this stone is available at "
                "this price.' before submitting."))
        if not self.vendor_id:
            raise UserError(_("Select the vendor."))
        if not self.gross_price_per_carat:
            raise UserError(_("Enter the gross buying price per carat."))
        if not self.stone_type:
            raise UserError(_("Select the replacement stone type."))
        if not self.weight_carat:
            raise UserError(_("Enter the carat weight."))

        product = line._replacement_resolve_product(self._spec_vals())
        line._replacement_check_duplicate(product.certificate, exclude_line=line)
        line._replacement_check_hard_rules(product)

        # Make sure the chosen vendor is a seller on the product so the PO and
        # the line's vendor resolve.
        if self.vendor_id.id not in product.seller_ids.mapped('partner_id').ids:
            self.env['product.supplierinfo'].create({
                'product_tmpl_id': product.id,
                'partner_id': self.vendor_id.id,
            })

        gross_total = (self.gross_price_per_carat or 0.0) * (self.carat_value or 0.0)
        outcome = line._replacement_price_outcome(
            product, gross_total, self.no_attribute_worse)
        line._create_replacement(
            product, self.vendor_id, gross_total, outcome, self.no_attribute_worse)

        message = (
            _("Replacement created and sent to Sales for confirmation.")
            if outcome['needs_sales']
            else _("Replacement completed. The original stone is now Replaced."))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Replace Stone"),
                'message': message,
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }


class StoneReplacementReject(models.TransientModel):
    """Captures the mandatory reason when Sales rejects a replacement."""
    _name = 'stone.replacement.reject'
    _description = 'Reject Stone Replacement'

    line_id = fields.Many2one('sale.order.line', required=True, readonly=True)
    # Predefined reasons, reusing the same list as the RFQ/order Cancel flow,
    # plus a free-text comment; so Reject asks for a reason the same way.
    reason_ids = fields.Many2many(
        'customer.rfq.cancel.reason', string="Reason", required=True,
        help="Pick every reason that applies.")
    comment = fields.Text(
        string="Comment",
        help="Any extra detail for whoever reviews this later.")

    def action_confirm(self):
        self.ensure_one()
        reason = ', '.join(self.reason_ids.mapped('name'))
        if self.comment:
            reason = f"{reason} — {self.comment}" if reason else self.comment
        self.line_id._replacement_do_reject(reason)
        return {'type': 'ir.actions.client', 'tag': 'soft_reload'}

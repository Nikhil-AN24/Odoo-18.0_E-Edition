# -*- coding: utf-8 -*-
"""Set Availability and Vendor Company on several stones at once.

An offline order raises one line per stone, so an order for seven identical
stones meant opening seven rows and typing the same two values into each.
Odoo's own multi-edit cannot help here: it is a list-VIEW feature, switched
on in list_controller.js, and the Order Lines grid is an embedded x2many
whose rows carry no selection checkboxes at all.

So the selection happens in this wizard instead. It opens with every stone
the user is allowed to edit already listed; they remove the ones they do not
want and set the two values once.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.sale_order import LGD_MANUAL_AVAILABILITY

_logger = logging.getLogger(__name__)


class LgdLineBulkWizard(models.TransientModel):
    _name = 'lgd.line.bulk.wizard'
    _description = 'Set Availability / Vendor on several stones'

    order_id = fields.Many2one('sale.order', string='Order', readonly=True)
    line_ids = fields.Many2many('sale.order.line', string='Stones')

    # Only the statuses a person may set by hand while the order is being
    # built. Mirrors MANUAL_ALLOWED in limit_availability_dropdown.js, which
    # trims the same list on the grid's own dropdown - the two must agree, or
    # this wizard becomes a way round the rule the grid enforces.
    availability_status = fields.Selection(
        LGD_MANUAL_AVAILABILITY, string='Availability',
        help="Leave empty to change only the Vendor Company.")
    vendor_id = fields.Many2one(
        'res.partner', string='Vendor Company',
        domain="[('partner_kind', '=', 'supplier')]",
        help="Leave empty to change only the Availability.")

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        order = self.env['sale.order'].browse(
            self.env.context.get('active_id') or values.get('order_id'))
        if not order.exists():
            return values
        values['order_id'] = order.id
        # Pre-tick every stone the user may actually edit. A line they cannot
        # touch is left out rather than listed and then refused on Apply.
        # MELEE child parcels are excluded for the same reason the status
        # aggregation excludes them - they are not stones in their own right.
        values['line_ids'] = [(6, 0, order.order_line.filtered(
            lambda l: l.product_id and not l.display_type
            and not l.melee_parent_line_id
            and (l.can_edit_availability or l.can_edit_vendor)
        ).ids)]
        return values

    def action_apply(self):
        self.ensure_one()
        if not self.line_ids:
            raise UserError(_("Tick at least one stone to update."))
        if not self.availability_status and not self.vendor_id:
            raise UserError(_(
                "Nothing to apply. Choose an Availability, a Vendor Company, "
                "or both."))

        vals = {}
        if self.availability_status:
            self._lgd_refuse_locked('can_edit_availability', _("Availability"))
            vals['availability_status'] = self.availability_status
        if self.vendor_id:
            self._lgd_refuse_locked('can_edit_vendor', _("Vendor Company"))
            vals['vendor_id'] = self.vendor_id.id

        # Same elevation the Confirm button uses: a plain Procurement user has
        # availability-only field access, and these are sanctioned writes.
        lines = self.line_ids
        if self.order_id._lgd_proc_needs_elevation():
            lines = lines.sudo()
        lines.with_context(lgd_proc_action=True).write(vals)
        _logger.info(
            "[BULK] %s: %s on %d line(s) by %s",
            self.order_id.name, ', '.join(sorted(vals)), len(self.line_ids),
            self.env.user.login)
        return {'type': 'ir.actions.client', 'tag': 'soft_reload'}

    def _lgd_refuse_locked(self, gate, label):
        """Stop before writing anything if the selection includes a stone the
        user may not change. Partially applying would leave the order half
        updated with no record of which half."""
        locked = self.line_ids.filtered(lambda l: not l[gate])
        if not locked:
            return
        raise UserError(_(
            "%(what)s cannot be set on these stones: %(stones)s.\n\n"
            "They are either closed, or past the point where the status is "
            "still set by hand. Remove them from the list and apply to the "
            "rest.",
            what=label,
            stones=', '.join(locked.mapped(
                lambda l: l.lgd_stock_number or l.product_template_id.display_name
                or str(l.id)))))

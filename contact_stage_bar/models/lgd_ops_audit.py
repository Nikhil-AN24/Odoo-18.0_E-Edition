import logging
from markupsafe import Markup, escape
from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

# Odoo's own field tracking writes its own chatter entries, separate from the
# audit entries below, and those also expose money. Any tracking message on
# these model/field pairs is flagged too. A single message can carry several
# tracked values at once (Total, Untaxed Amount and Order Status arrive
# together), and a message cannot be split, so one sensitive value hides the whole entry.
_LGD_SENSITIVE_TRACKED = {
    'sale.order': {
        'amount_total',        # Total
        'amount_untaxed',      # Untaxed Amount
        'amount_tax',
        'sdk_augmont_status',  # Order Status
    },
}

# The roles that must not see these entries.
_LGD_SALES_ROLES = (
    'contact_stage_bar.group_lgd_sales',
    'contact_stage_bar.group_lgd_sales_manager',
    'contact_stage_bar.group_lgd_regional_sales_head',
)


class MailMessage(models.Model):
    _inherit = 'mail.message'

    lgd_is_price_audit = fields.Boolean(
        string='Price Audit Entry', default=False, index=True, copy=False,
        help="Marks a chatter entry that records a change to Availability, "
             "Procurement Price/carat or Vendor Company. The record rule "
             "lgd_mail_message_price_audit_rule hides these from Sales.")

    @api.model_create_multi
    def create(self, vals_list):
        """Flag Odoo's own tracking entries that reveal money.
        _message_track returns tracking data, not the messages it posted, so
        there is nothing to flag there. Catching it here covers every route
        that produces a tracked change — the form, RPC, and any automation."""
        messages = super().create(vals_list)
        messages._lgd_flag_sensitive_tracking()
        return messages

    @api.model
    def _lgd_backfill_price_audit_flags(self):
        """Flag entries that already exist, so the rule covers history too.
        Without this the rule would only ever hide changes made from now on,
        and every past price change would stay readable to Sales. Safe to
        re-run: it only looks at messages not already flagged.
        """
        Fields = self.env['ir.model.fields'].sudo()
        for model_name, field_names in _LGD_SENSITIVE_TRACKED.items():
            field_ids = Fields.search([
                ('model', '=', model_name),
                ('name', 'in', list(field_names)),
            ])
            if not field_ids:
                continue
            messages = self.sudo().search([
                ('model', '=', model_name),
                ('lgd_is_price_audit', '=', False),
                ('tracking_value_ids.field_id', 'in', field_ids.ids),
            ])
            if messages:
                messages.write({'lgd_is_price_audit': True})
                _logger.info(
                    "Flagged %d existing %s chatter entries as price audit.",
                    len(messages), model_name)

    def _lgd_flag_sensitive_tracking(self):
        to_flag = self.browse()
        for message in self:
            sensitive = _LGD_SENSITIVE_TRACKED.get(message.model)
            if not sensitive or message.lgd_is_price_audit:
                continue
            tracked = set(
                message.sudo().tracking_value_ids.field_id.mapped('name'))
            if tracked & sensitive:
                to_flag |= message
        if to_flag:
            to_flag.sudo().write({'lgd_is_price_audit': True})


class ResUsers(models.Model):
    _inherit = 'res.users'

    lgd_hide_price_audit = fields.Boolean(
        compute='_compute_lgd_hide_price_audit',
        string='Hide Price Audit Entries',
        help="True for the Sales roles, unless the user is also an "
             "administrator. Read by the mail.message record rule.")

    @api.depends('groups_id')
    def _compute_lgd_hide_price_audit(self):
        for user in self:
            # sudo: has_group refuses to answer for a user other than the
            # caller unless in superuser mode, and this field is read while a
            # record rule is being evaluated — it must never raise there.
            checker = user.sudo()
            privileged = (
                checker.has_group('base.group_system')
                or checker.has_group('contact_stage_bar.group_lgd_superadmin')
            )
            user.lgd_hide_price_audit = bool(
                not privileged
                and any(checker.has_group(role) for role in _LGD_SALES_ROLES)
            )


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    # Column label as the user sees it, keyed by field.
    _LGD_AUDITED_FIELDS = {
        'availability_status': 'Availability',
        # Lives in custom_sale_order, which contact_stage_bar does not depend
        # on, so every use of it is guarded by a _fields check.
        'procurement_price_per_carat': 'Procurement Price/carat',
        'vendor_id': 'Vendor Company',
    }

    def _lgd_audit_value(self, field_name, value):
        """Render an old/new value the way the column shows it."""
        field = self._fields.get(field_name)
        if not field:
            return ''
        if field.type == 'many2one':
            return value.display_name if value else _('(empty)')
        if field.type == 'selection':
            labels = dict(field._description_selection(self.env))
            return labels.get(value) or _('(empty)')
        if isinstance(value, float):
            return '%.2f' % value
        return _('(empty)') if value in (False, None, '') else str(value)

    def write(self, vals):
        tracked = [f for f in self._LGD_AUDITED_FIELDS
                   if f in vals and f in self._fields]
        before = ({line.id: {f: line[f] for f in tracked} for line in self}
                  if tracked else {})
        res = super().write(vals)
        if not tracked:
            return res
        for line in self:
            rows = []
            for field_name in tracked:
                old = before.get(line.id, {}).get(field_name)
                new = line[field_name]
                if old != new:
                    rows.append((
                        self._LGD_AUDITED_FIELDS[field_name],
                        line._lgd_audit_value(field_name, old),
                        line._lgd_audit_value(field_name, new),
                    ))
            if rows and line.order_id:
                line._lgd_post_price_audit(rows)
        return res

    def _lgd_post_price_audit(self, rows):
        """One chatter entry on the order, flagged so the rule can hide it."""
        self.ensure_one()
        product = escape(self.product_id.display_name or self.name or _('Line'))
        items = Markup('').join(
            Markup('<li>%s: %s → %s</li>') % (label, old, new)
            for label, old, new in rows
        )
        body = Markup(
            '<p><b>%s</b> — order line changed:</p><ul>%s</ul>'
        ) % (product, items)
        try:
            # _message_log, not message_post: it never raises on a missing
            # sender address, so an audit entry can never block the edit it is recording.
            message = self.order_id._message_log(body=body)
        except Exception:
            _logger.warning(
                "Could not write the audit entry for %s: %s",
                self.order_id.name, body, exc_info=True)
            return
        if message:
            # sudo: the rule hides these from Sales, including the Sales user
            # who just made the change, so they could not flag their own entry.
            message.sudo().write({'lgd_is_price_audit': True})

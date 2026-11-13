# -*- coding: utf-8 -*-
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

LGD_OFFLINE_SEND_SALESPERSON_PARAM = 'lgd_offline_send_salesperson'


def lgd_salesperson_info(data):
    """{'email', 'name'} from a website payload, or {} when none was sent.

    Accepts "salesperson": {"email", "name"}, "salesperson": "Name", or
    flat salespersonEmail / salespersonName keys."""
    if not isinstance(data, dict):
        return {}
    raw = data.get('salesperson')
    if isinstance(raw, str):
        raw = {'name': raw}
    raw = raw if isinstance(raw, dict) else {}
    email = (raw.get('email') or data.get('salespersonEmail') or '').strip()
    name = (raw.get('name') or data.get('salespersonName') or '').strip()
    return {k: v for k, v in (('email', email), ('name', name)) if v}


class LgdSalespersonMixin(models.AbstractModel):
    _name = 'lgd.salesperson.mixin'
    _description = 'LGD salesperson sync helpers'

    lgd_salesperson_error = fields.Char(
        string="Salesperson Sync Error", copy=False, readonly=True,
        help="Why the salesperson could not be taken from, or sent to, the "
             "LGD website. Empty once a salesperson is set.")

    @api.model
    def _lgd_resolve_salesperson(self, info):
        """(res.users, error). Exactly one of the two is set."""
        if not info:
            return self.env['res.users'], _(
                "The website sent no salesperson.")
        Users = self.env['res.users'].sudo().with_context(active_test=True)
        internal = [('share', '=', False)]
        email = info.get('email')
        if email:
            found = Users.search(internal + ['|', ('login', '=ilike', email),
                                             ('email', '=ilike', email)])
            if len(found) == 1:
                return found, None
            if len(found) > 1:
                return Users, _(
                    "Salesperson email %(email)s matches %(count)s Odoo "
                    "users (%(names)s).") % {
                        'email': email, 'count': len(found),
                        'names': ', '.join(found.mapped('name'))}
        name = info.get('name')
        if name:
            found = Users.search(internal + [('name', '=ilike', name)])
            if len(found) == 1:
                return found, None
            if len(found) > 1:
                return Users, _(
                    "Salesperson name '%(name)s' matches %(count)s Odoo "
                    "users; send the email to pick one.") % {
                        'name': name, 'count': len(found)}
        return Users, _(
            "No Odoo user matches the salesperson the website sent "
            "(%(who)s).") % {
                'who': ', '.join('%s: %s' % kv for kv in info.items())}

    def _lgd_record_salesperson_error(self, error):
        """Store `error` (or clear it) and post it once to the chatter."""
        for record in self.sudo():
            if (record.lgd_salesperson_error or False) == (error or False):
                continue
            record.with_context(lgd_from_website=True).write(
                {'lgd_salesperson_error': error or False})
            if error and hasattr(record, 'message_post'):
                record.message_post(body=_("Salesperson sync: %s") % error)
            if error:
                _logger.warning("[SALESPERSON] %s %s: %s", record._name,
                                record.display_name, error)

    def _lgd_apply_website_salesperson(self, info, fallback=None):
        """Set user_id from the salesperson the website sent.

        Returns the error recorded, or None. The rule:
          * sent and matched      -> that user, no error
          * nothing sent          -> keep the current salesperson (or take
                                     `fallback`, the buyer's, for an order);
                                     an error only if that leaves none
          * sent but not matched  -> same fallback, and the error is always
                                     recorded, so someone checks the name
        """
        self.ensure_one()
        user, error = self._lgd_resolve_salesperson(info)
        if not user:
            user = self.user_id or fallback or user
            if not info:
                error = None if user else _(
                    "The website sent no salesperson, and none is assigned "
                    "in Odoo.")
            elif user:
                error = _("%(error)s Kept %(user)s.") % {
                    'error': error, 'user': user.name}
        if user and self.user_id != user:
            self.sudo().with_context(lgd_from_website=True,
                                     skip_salesperson_sync=True).write(
                {'user_id': user.id})
        self._lgd_record_salesperson_error(error)
        return error

    @staticmethod
    def _lgd_salesperson_block(user):
        if not user:
            return None
        return {'name': user.name or '', 'email': user.email or user.login or ''}


def _lgd_clear_salesperson_error_on_manual_set(records, vals):
    """A salesperson picked in Odoo answers the sync error, so drop it.
    Website-driven writes (lgd_from_website) are skipped: that path
    records its own result right after."""
    if vals.get('user_id') and not records.env.context.get('lgd_from_website'):
        stale = records.filtered('lgd_salesperson_error')
        if stale:
            stale.sudo().with_context(lgd_from_website=True).write(
                {'lgd_salesperson_error': False})


class ResPartner(models.Model):
    _name = 'res.partner'
    _inherit = ['res.partner', 'lgd.salesperson.mixin']

    def write(self, vals):
        res = super().write(vals)
        _lgd_clear_salesperson_error_on_manual_set(self, vals)
        return res


class SaleOrder(models.Model):
    _name = 'sale.order'
    _inherit = ['sale.order', 'lgd.salesperson.mixin']

    def write(self, vals):
        res = super().write(vals)
        _lgd_clear_salesperson_error_on_manual_set(self, vals)
        return res

    def _lgd_offline_payload(self):
        payload = super()._lgd_offline_payload()
        send = self.env['ir.config_parameter'].sudo().get_param(
            LGD_OFFLINE_SEND_SALESPERSON_PARAM, '1')
        if send in ('0', 'False', 'false'):
            return payload
        user = self.user_id or self.partner_id.commercial_partner_id.user_id
        payload['salesperson'] = self._lgd_salesperson_block(user)
        self._lgd_record_salesperson_error(None if user else _(
            "Sent to the website without a salesperson: none is set on the "
            "order or its buyer."))
        return payload

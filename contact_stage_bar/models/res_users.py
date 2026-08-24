import logging

from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class ResUsers(models.Model):
    _inherit = "res.users"

    location = fields.Selection([('mumbai', 'India'), ('surat', 'USA')], string='Location')    

    @api.model
    def _lgd_restrict_export_to_admins(self):
        """Revoke base.group_allow_export from everyone who is not an admin.

        Called from security/res_groups.xml on every module update. Admins get
        the group by implication from base.group_system, so they are skipped
        here and keep it even after this runs.

        Needed because the group was granted to each user directly rather than
        through an implied group: changing the declarations alone would leave
        those direct memberships in place, and every user would still be able
        to export.
        """
        export_group = self.env.ref('base.group_allow_export',
                                    raise_if_not_found=False)
        admin_group = self.env.ref('base.group_system', raise_if_not_found=False)
        if not export_group or not admin_group:
            return
        revoke = export_group.users.filtered(
            lambda u: admin_group not in u.groups_id)
        if revoke:
            export_group.write({'users': [(3, u.id) for u in revoke]})
            _logger.info(
                "Export restricted to admins: revoked base.group_allow_export "
                "from %s user(s): %s",
                len(revoke), ", ".join(sorted(revoke.mapped('login'))))

    @api.model
    def _lgd_restrict_sale_order_bindings_to_admins(self):
        """Restrict the sale.order Actions dropdown to administrators. """
        admin_group = self.env.ref('base.group_system', raise_if_not_found=False)
        sale_order = self.env['ir.model']._get('sale.order')
        if not admin_group or not sale_order:
            return
        gated = []
        for model in ('ir.actions.server', 'ir.actions.act_window'):
            actions = self.env[model].sudo().search([
                ('binding_model_id', '=', sale_order.id),
                ('binding_type', '=', 'action'),
            ])
            for action in actions:
                if action.groups_id:
                    continue
                action.write({'groups_id': [(4, admin_group.id)]})
                gated.append(action.name)
        if gated:
            _logger.info(
                "sale.order Actions dropdown restricted to admins: %s",
                ", ".join(sorted(gated)))


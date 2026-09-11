import logging

from odoo import models, fields, api

_logger = logging.getLogger(__name__)

# Which Procurement > Orders stone lists an LGD Procurement user may open
# (PRD §5.7.5): user-form checkbox -> hidden res.groups gating that menu.
_LGD_PROCUREMENT_STONE_GROUPS = {
    'lgd_access_all_orders': 'contact_stage_bar.group_lgd_procurement_all_orders',
    'lgd_access_lab_grown_certified': 'contact_stage_bar.group_lgd_procurement_lab_grown_certified',
    'lgd_access_lab_grown_melee': 'contact_stage_bar.group_lgd_procurement_lab_grown_melee',
    'lgd_access_natural_certified': 'contact_stage_bar.group_lgd_procurement_natural_certified',
    'lgd_access_natural_melee': 'contact_stage_bar.group_lgd_procurement_natural_melee',
}


class ResGroups(models.Model):
    _inherit = "res.groups"

    @api.model
    def get_application_groups(self, domain):
        # Keep the stone-access groups out of the generated group checkboxes
        # (the Technical section in debug mode): they're managed from the
        # "LGD Procurement — Stone Access" section only, so one control each.
        stone_group_ids = [
            group.id for group in (
                self.env.ref(xmlid, raise_if_not_found=False)
                for xmlid in _LGD_PROCUREMENT_STONE_GROUPS.values())
            if group
        ]
        if stone_group_ids:
            domain = domain + [('id', 'not in', stone_group_ids)]
        return super().get_application_groups(domain)


class ResUsers(models.Model):
    _inherit = "res.users"

    location = fields.Selection([('mumbai', 'India'), ('surat', 'USA')], string='Location')

    # Drives the visibility of the stone-access checkboxes on the user form;
    # recomputed live while LGD roles are ticked, before saving.
    is_lgd_procurement = fields.Boolean(compute='_compute_is_lgd_procurement')
    lgd_access_all_orders = fields.Boolean(
        string="All Orders",
        compute='_compute_lgd_access_all_orders',
        inverse='_inverse_lgd_procurement_stone_access')
    lgd_access_lab_grown_certified = fields.Boolean(
        string="LabGrown Certified",
        compute='_compute_lgd_access_lab_grown_certified',
        inverse='_inverse_lgd_procurement_stone_access')
    lgd_access_lab_grown_melee = fields.Boolean(
        string="LabGrown Melle",
        compute='_compute_lgd_access_lab_grown_melee',
        inverse='_inverse_lgd_procurement_stone_access')
    lgd_access_natural_certified = fields.Boolean(
        string="Natural Certified",
        compute='_compute_lgd_access_natural_certified',
        inverse='_inverse_lgd_procurement_stone_access')
    lgd_access_natural_melee = fields.Boolean(
        string="Natural Melle",
        compute='_compute_lgd_access_natural_melee',
        inverse='_inverse_lgd_procurement_stone_access')

    @api.depends('groups_id')
    def _compute_is_lgd_procurement(self):
        procurement = self.env.ref('contact_stage_bar.group_lgd_procurement')
        for user in self:
            # Compare ids: on an unsaved form the groups are new-record
            # wrappers, which `in` on the recordset never matches.
            # trans_implied_ids so LGD Procurement Manager counts too.
            groups = user.groups_id | user.groups_id.trans_implied_ids
            user.is_lgd_procurement = procurement.id in groups.ids

    # One compute per checkbox, not one shared: while a field is being saved
    # the ORM protects every field sharing its compute method, so the boxes
    # left untouched would read as unticked in the inverse and lose their
    # group whenever a single box was saved on its own.
    @api.depends('groups_id')
    def _compute_lgd_access_all_orders(self):
        self._compute_lgd_access('lgd_access_all_orders')

    @api.depends('groups_id')
    def _compute_lgd_access_lab_grown_certified(self):
        self._compute_lgd_access('lgd_access_lab_grown_certified')

    @api.depends('groups_id')
    def _compute_lgd_access_lab_grown_melee(self):
        self._compute_lgd_access('lgd_access_lab_grown_melee')

    @api.depends('groups_id')
    def _compute_lgd_access_natural_certified(self):
        self._compute_lgd_access('lgd_access_natural_certified')

    @api.depends('groups_id')
    def _compute_lgd_access_natural_melee(self):
        self._compute_lgd_access('lgd_access_natural_melee')

    def _compute_lgd_access(self, fname):
        group_id = self.env.ref(_LGD_PROCUREMENT_STONE_GROUPS[fname]).id
        for user in self:
            user[fname] = group_id in user.groups_id.ids

    def _inverse_lgd_procurement_stone_access(self):
        for user in self:
            wanted = {xmlid: user[fname] for fname, xmlid in _LGD_PROCUREMENT_STONE_GROUPS.items()}
            commands = []
            for xmlid, ticked in wanted.items():
                group = self.env.ref(xmlid)
                if ticked and group not in user.groups_id:
                    commands.append((4, group.id))
                elif not ticked and group in user.groups_id:
                    commands.append((3, group.id))
            if commands:
                user.groups_id = commands

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


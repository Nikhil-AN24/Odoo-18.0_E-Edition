import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)

GROUP_LOGISTICS = 'contact_stage_bar.group_lgd_logistics'
GROUP_QUALITY = 'contact_stage_bar.group_lgd_quality'
GROUP_INVENTORY = 'contact_stage_bar.group_lgd_inventory'
GROUP_PROCUREMENT = 'contact_stage_bar.group_lgd_procurement'
GROUP_SYSTEM = 'base.group_system'

# Stage -> (who did it, when), used to name the other user in the "already
# processed" error (§8 preamble).
_LGD_STAGE_ACTOR = {
    'received': ('lgd_received_by', 'lgd_received_at'),
    'rejected': ('lgd_rejected_by', 'lgd_rejected_at'),
    'with_qc': ('lgd_handed_to_qc_by', 'lgd_handed_to_qc_at'),
    'in_qc': ('lgd_qc_received_by', 'lgd_qc_received_at'),
    'qc_passed': ('lgd_qc_decided_by', 'lgd_qc_decided_at'),
    'failed': ('lgd_qc_decided_by', 'lgd_qc_decided_at'),
    'in_inventory': ('lgd_accepted_by', 'lgd_accepted_at'),
    'returned': ('lgd_returned_by', 'lgd_returned_at'),
}


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    # ── §7.1 Link and display (all read-only, from the core sale_line_id) ────
    lgd_invoice_number = fields.Char(
        related='sale_line_id.order_id.sdk_augmont_number',
        store=True, index=True, string='Invoice No.')
    lgd_certificate = fields.Char(
        related='sale_line_id.certificate', string='Certificate')
    lgd_shape = fields.Char(related='sale_line_id.shapes', string='Shape')
    lgd_carat = fields.Char(related='sale_line_id.carat_weight', string='Carat')
    lgd_colour = fields.Char(related='sale_line_id.color', string='Colour')
    lgd_clarity = fields.Char(related='sale_line_id.clarity', string='Clarity')
    lgd_cert_type = fields.Selection(
        related='sale_line_id.stone_certification_type', string='Certification')
    lgd_line_type = fields.Selection(
        related='sale_line_id.line_type', string='Line Type')
    lgd_melee_qty = fields.Float(
        related='sale_line_id.melee_total_qty', string='Melee Qty')
    lgd_melee_unit = fields.Char(
        related='sale_line_id.melee_unit', string='Melee Unit')
    lgd_is_certified = fields.Boolean(
        compute='_compute_lgd_is_certified', string='Certified')

    lgd_stage = fields.Selection([
        ('expected', 'Expected'),
        ('received', 'Received'),
        ('with_qc', 'Handed to QC'),
        ('in_qc', 'With QC'),
        ('qc_passed', 'Passed QC'),
        ('in_inventory', 'In Inventory'),
        ('rejected', 'Rejected at inward'),
        ('failed', 'Failed QC'),
        ('returned', 'Returned to vendor'),
        ('split', 'Split to another RFQ'),
        ('historical', 'Historical (pre go-live)'),
    ], string='Stage', index=True, copy=False,
        help="Empty means Expected. Written only by the Logistics/QC buttons "
             "and the go-live action.")
    lgd_commit_deadline = fields.Date(
        compute='_compute_lgd_commit_deadline', store=True,
        string='Commit By')

    lgd_is_overdue = fields.Boolean(
        compute='_compute_lgd_is_overdue', string='Past Deadline')

    # ──  Logistics ──────────────────────────────────────────────────────
    lgd_collection_method = fields.Selection([
        ('vendor_delivers', 'Vendor delivers'),
        ('we_collect', 'We collect'),
    ], string='Collection', copy=False)
    lgd_collector_id = fields.Many2one(
        'res.users', string='Collected By', copy=False)

    lgd_chk_certificate = fields.Boolean(string='Certificate no.', copy=False)
    lgd_chk_shape = fields.Boolean(string='Shape', copy=False)
    lgd_chk_carat = fields.Boolean(string='Carat', copy=False)
    lgd_chk_quantity = fields.Boolean(string='Quantity', copy=False)
    lgd_chk_colour = fields.Boolean(string='Colour', copy=False)
    lgd_chk_clarity = fields.Boolean(string='Clarity', copy=False)
    lgd_received_certificate = fields.Char(
        string='Certificate Received', copy=False,
        help="Filled in at inward when the order carried no certificate number.")

    lgd_received_by = fields.Many2one('res.users', string='Received By', copy=False)
    lgd_received_at = fields.Datetime(string='Received On', copy=False)
    lgd_rejected_by = fields.Many2one('res.users', string='Rejected By', copy=False)
    lgd_rejected_at = fields.Datetime(string='Rejected On', copy=False)
    lgd_reject_reason_id = fields.Many2one(
        'lgd.inward.reject.reason', string='Reject Reason', copy=False)
    lgd_reject_note = fields.Text(string='Reject Note', copy=False)
    lgd_handed_to_qc_by = fields.Many2one(
        'res.users', string='Handed Over By', copy=False)
    lgd_handed_to_qc_at = fields.Datetime(string='Handed Over On', copy=False)

    # ── Quality Control ─────────────────────────────────────────────────────────────
    lgd_qc_received_by = fields.Many2one(
        'res.users', string='QC Received By', copy=False)
    lgd_qc_received_at = fields.Datetime(string='QC Received On', copy=False)

    lgd_note_order_specific = fields.Char(
        related='product_id.product_tmpl_id.order_specific',
        string='Order Specific Note')
    lgd_note_item = fields.Char(
        related='product_id.product_tmpl_id.item_notes', string='Item Note')
    lgd_note_qc_requirements = fields.Char(
        related='product_id.product_tmpl_id.default_qc_requirements',
        string='QC Requirement')
    lgd_note_order_item = fields.Char(
        related='sale_line_id.order_id.item_notes', string='Order Note')
    lgd_note_order_qc = fields.Char(
        related='sale_line_id.order_id.default_qc_requirements',
        string='Order QC Requirement')

    lgd_ok_order_specific = fields.Boolean(string='Order specific met', copy=False)
    lgd_ok_item = fields.Boolean(string='Item note met', copy=False)
    lgd_ok_qc_requirements = fields.Boolean(string='QC requirement met', copy=False)
    lgd_ok_order_item = fields.Boolean(string='Order note met', copy=False)
    lgd_ok_order_qc = fields.Boolean(string='Order QC requirement met', copy=False)

    lgd_qc_chk_inscription = fields.Boolean(string='Inscription', copy=False)
    lgd_qc_chk_weight = fields.Boolean(string='Weight', copy=False)
    lgd_qc_chk_measurements = fields.Boolean(string='Measurements', copy=False)
    lgd_qc_chk_no_damage = fields.Boolean(string='No damage', copy=False)
    lgd_qc_chk_colour_clarity = fields.Boolean(string='Colour & clarity', copy=False)
    lgd_qc_chk_screening = fields.Boolean(string='Lab-grown screening', copy=False)
    lgd_qc_chk_shape_qty = fields.Boolean(string='Shape & quantity', copy=False)

    # QC ticks these to choose which stones a Pass/Fail applies to. An
    # embedded one2many list has no record-selection checkboxes in Odoo, so
    # the "select several products" column has to be a real field.
    lgd_qc_selected = fields.Boolean(string='Select', copy=False)

    lgd_set_label = fields.Char(string='Set Label', copy=False)
    lgd_set_incomplete = fields.Boolean(string='Set Incomplete', copy=False)
    lgd_qc_result = fields.Selection(
        [('pass', 'Pass'), ('fail', 'Fail')], string='QC Result', copy=False)
    lgd_qc_decided_by = fields.Many2one(
        'res.users', string='QC Decided By', copy=False)
    lgd_qc_decided_at = fields.Datetime(string='QC Decided On', copy=False)
    lgd_fail_reason_id = fields.Many2one(
        'lgd.qc.fail.reason', string='Fail Reason', copy=False)
    lgd_fail_note = fields.Text(string='Fail Note', copy=False)

    # ── Inventory and returns ──────────────────────────────────────────
    lgd_accepted_by = fields.Many2one('res.users', string='Accepted By', copy=False)
    lgd_accepted_at = fields.Datetime(string='Accepted On', copy=False)
    lgd_return_method = fields.Selection([
        ('vendor_collected', 'Vendor collected'),
        ('we_delivered', 'We delivered'),
    ], string='Return Method', copy=False)
    lgd_returned_by = fields.Many2one('res.users', string='Returned By', copy=False)
    lgd_returned_at = fields.Datetime(string='Returned On', copy=False)
    lgd_return_note = fields.Text(string='Return Note', copy=False)
    lgd_return_proof_id = fields.Many2one(
        'ir.attachment', string='Return Proof', copy=False)
    lgd_return_move_id = fields.Many2one(
        'stock.move', string='Return Move', copy=False)

    # ── Computes ────────────────────────────────────────────────────────────
    @api.depends('lgd_cert_type', 'lgd_line_type')
    def _compute_lgd_is_certified(self):
        for line in self:
            if line.lgd_cert_type:
                line.lgd_is_certified = line.lgd_cert_type == 'certified'
            else:
                line.lgd_is_certified = line.lgd_line_type == 'lgd'

    @api.depends('order_id.date_order')
    def _compute_lgd_commit_deadline(self):
        """§5.2 — date_order + lgd.commit_window_days (default 7).

        Informational only: it blocks nothing and cancels nothing."""
        days = self._lgd_commit_window_days()
        for line in self:
            start = line.order_id.date_order
            line.lgd_commit_deadline = (
                fields.Date.to_date(start) + timedelta(days=days)
                if start else False
            )

    @api.depends('lgd_commit_deadline')
    def _compute_lgd_is_overdue(self):
        today = fields.Date.context_today(self)
        for line in self:
            line.lgd_is_overdue = bool(
                line.lgd_commit_deadline and line.lgd_commit_deadline < today)

    @api.model
    def _lgd_commit_window_days(self):
        param = self.env['ir.config_parameter'].sudo().get_param(
            'lgd.commit_window_days', '7')
        try:
            return int(param)
        except (TypeError, ValueError):
            _logger.warning(
                "lgd.commit_window_days is not a number (%r); using 7.", param)
            return 7

    @api.onchange('lgd_collection_method')
    def _onchange_lgd_collection_method(self):
        """Stamp whoever is working the row as the collector, the moment they
        touch it. Only fills a blank — an existing name is never overwritten,
        so handing a stone to a colleague is just picking them in the cell."""
        for line in self:
            if line.lgd_collection_method and not line.lgd_collector_id:
                line.lgd_collector_id = self.env.user

    # ── Responsible user helper ────────────────────────────────────────
    @api.model
    def _lgd_responsible_user(self, param_key, group_xmlid):
        """Login from the parameter, else the first active member of the group,
        else empty. Never raises — a missing responsible must not stop a
        stone being processed."""
        login = self.env['ir.config_parameter'].sudo().get_param(param_key)
        if login:
            user = self.env['res.users'].sudo().search(
                [('login', '=', login)], limit=1)
            if user:
                return user
        group = self.env.ref(group_xmlid, raise_if_not_found=False)
        if group:
            user = self.env['res.users'].sudo().search(
                [('groups_id', 'in', group.id), ('active', '=', True)], limit=1)
            if user:
                return user
        _logger.warning(
            "No responsible user for %s / %s; activity not assigned.",
            param_key, group_xmlid)
        return self.env['res.users']

    # ── Which checks apply ─────────────────────────────────────────────
    def _lgd_required_checks(self, scope='all'):

        self.ensure_one()
        checks = []

        if scope in ('all', 'logistics'):
            pass

        if scope in ('all', 'qc'):
            for tick, note in (
                ('lgd_ok_order_specific', 'lgd_note_order_specific'),
                ('lgd_ok_item', 'lgd_note_item'),
                ('lgd_ok_qc_requirements', 'lgd_note_qc_requirements'),
                ('lgd_ok_order_item', 'lgd_note_order_item'),
                ('lgd_ok_order_qc', 'lgd_note_order_qc'),
            ):
                if (self[note] or '').strip():
                    checks.append((tick, self._fields[tick].string))

        return checks

    def _lgd_missing_checks(self, scope):
        """Labels of the checks that are not satisfied yet."""
        self.ensure_one()
        missing = []
        for field, label in self._lgd_required_checks(scope):
            value = self[field]
            ok = bool(value.strip()) if isinstance(value, str) else bool(value)
            if not ok:
                missing.append(label)
        return missing

    # ── preamble helpers ─────────────────────────────────────────────────
    def _lgd_check_group(self, *groups):
        """Every action checks the user's group first."""
        user = self.env.user
        if user.has_group(GROUP_SYSTEM) or any(user.has_group(g) for g in groups):
            return
        raise AccessError(
            _("You are not allowed to perform this Operations action. "
              "Ask an administrator for the right role."))

    def _lgd_label(self):
        """How a stone is named in an error: certificate first, else product."""
        self.ensure_one()
        return (self.lgd_certificate or self.lgd_received_certificate
                or self.product_id.display_name or _('Stone'))

    def _lgd_busy_message(self):
        """'already Received by Asha on 23/09/2026' — names who and when, so
        two users cannot silently process the same stone twice."""
        self.ensure_one()
        stage_label = dict(self._fields['lgd_stage'].selection).get(
            self.lgd_stage, self.lgd_stage or _('Expected'))
        actor_field, at_field = _LGD_STAGE_ACTOR.get(
            self.lgd_stage, (None, None))
        who = self[actor_field] if actor_field else False
        when = self[at_field] if at_field else False
        if who and when:
            return _("already %(stage)s by %(who)s on %(when)s") % {
                'stage': stage_label,
                'who': who.name,
                'when': fields.Datetime.to_string(when),
            }
        return _("already %s") % stage_label

    def _lgd_guard(self, allowed_stages, extra=None):

        self.invalidate_recordset(['lgd_stage'])
        problems = []
        for line in self:
            label = line._lgd_label()
            if line.lgd_stage not in allowed_stages:
                problems.append("%s: %s." % (label, line._lgd_busy_message()))
                continue
            for message in (extra(line) if extra else []):
                problems.append("%s: %s" % (label, message))
        if problems:
            raise UserError("\n".join(problems))

    def _lgd_notify(self, record, summary, note, user):
        if not record:
            return
        domain = [
            ('res_model', '=', record._name),
            ('res_id', '=', record.id),
            ('summary', '=', summary),
        ]
        if user:
            domain.append(('user_id', '=', user.id))
        if self.env['mail.activity'].sudo().search_count(domain):
            return
        record.activity_schedule(
            'mail.mail_activity_data_todo', summary=summary, note=note,
            user_id=user.id if user else self.env.uid)

    def _lgd_stone_description(self):
        """Specs only — no price, no customer, no salesperson."""
        self.ensure_one()
        bits = [
            ('Invoice', self.lgd_invoice_number),
            ('Certificate', self.lgd_certificate or self.lgd_received_certificate),
            ('Shape', self.lgd_shape),
            ('Carat', self.lgd_carat),
            ('Colour', self.lgd_colour),
            ('Clarity', self.lgd_clarity),
        ]
        return ", ".join("%s %s" % (k, v) for k, v in bits if v)

    def _lgd_write_order_line_status(self, status, only_when):
        """order-line status writes always carry the bypass key."""
        for line in self:
            sale_line = line.sale_line_id
            if sale_line and sale_line.availability_status in only_when:
                sale_line.with_context(
                    skip_auto_procurement=True).write(
                        {'availability_status': status})

    def _lgd_collection_problems(self, line):

        if not line.lgd_collection_method:
            yield _("set Collection (how the stone arrived) first.")
        if not line.lgd_collector_id:
            yield _("set Collected By (who took it in) first.")

    # ── Receive ────────────────────────────────────────────────────────
    def action_lgd_receive(self):
        self._lgd_check_group(GROUP_LOGISTICS)
        return self._lgd_action_receive()

    def _lgd_action_receive(self):
        def problems(line):
            yield from self._lgd_collection_problems(line)
            if line.product_qty <= 0:
                yield _("this line is zero; there is nothing to receive.")
            if not line.sale_line_id:
                yield _("not linked to an order line. Ask Procurement to link it.")
            if line.order_id.state == 'cancel':
                yield _("its RFQ is cancelled.")
            missing = line._lgd_missing_checks('logistics')
            if missing:
                yield _("tick all required checks (missing: %s).") % ", ".join(missing)

        self._lgd_guard((False, 'expected'), problems)
        now = fields.Datetime.now()
        self.write({
            'lgd_stage': 'received',
            'lgd_received_by': self.env.uid,
            'lgd_received_at': now,
        })
        self.filtered(lambda l: not l.lgd_collector_id).write(
            {'lgd_collector_id': self.env.uid})
        for line in self:
            line.order_id._lgd_log(
                _("Stone received at inward: %s.") % line._lgd_stone_description())
        return True

    # ── Reject ─────────────────────────────────────────────────────────
    def action_lgd_open_reject_wizard(self):
        self._lgd_check_group(GROUP_LOGISTICS)
        # Check before asking for a reason, rather than discarding their typing when the wizard tries to save.
        self._lgd_guard((False, 'expected'), self._lgd_collection_problems)
        return self._lgd_open_wizard(
            'lgd.inward.reject.wizard', _('Reject Stone'),
            'contact_stage_bar.view_lgd_inward_reject_wizard_form')

    def _lgd_action_reject(self, reason, note=None):
        self._lgd_guard((False, 'expected'), self._lgd_collection_problems)
        now = fields.Datetime.now()
        self.write({
            'lgd_stage': 'rejected',
            'lgd_rejected_by': self.env.uid,
            'lgd_rejected_at': now,
            'lgd_reject_reason_id': reason.id if reason else False,
            'lgd_reject_note': note or False,
        })
        responsible = self._lgd_responsible_user(
            'lgd.procurement_responsible_login', GROUP_PROCUREMENT)
        for line in self:
            line.product_qty = 0
            line._lgd_void_or_cancel_po()
            line._lgd_write_order_line_status(
                'not_available', ('diamond_booked', 'confirmed'))
            line._lgd_notify(
                line.sale_line_id.order_id,
                _('Replace stone: rejected at inward'),
                _("%(stone)s was rejected at inward. Reason: %(reason)s.") % {
                    'stone': line._lgd_stone_description(),
                    'reason': reason.name if reason else _('not given'),
                },
                responsible)
        return True

    # ── Hand over to QC ────────────────────────────────────────────────
    def action_lgd_handover_qc(self):
        self._lgd_check_group(GROUP_LOGISTICS)
        return self._lgd_action_handover_qc()

    def _lgd_action_handover_qc(self):
        self._lgd_guard(('received',))
        now = fields.Datetime.now()
        self.write({
            'lgd_stage': 'with_qc',
            'lgd_handed_to_qc_by': self.env.uid,
            'lgd_handed_to_qc_at': now,
        })
        responsible = self._lgd_responsible_user(
            'lgd.qc_responsible_login', GROUP_QUALITY)
        for line in self:
            line._lgd_write_order_line_status(
                'in_qc_process', ('diamond_booked', 'confirmed'))
            line._lgd_notify(
                line.order_id, _('Stone handed to QC'),
                _("Ready to inspect: %s.") % line._lgd_stone_description(),
                responsible)
        return True

    # ── Received from Logistics (QC) ───────────────────────────────────
    def action_lgd_qc_receive(self):
        self._lgd_check_group(GROUP_QUALITY)
        return self._lgd_action_qc_receive()

    def _lgd_action_qc_receive(self):
        self._lgd_guard(('with_qc',))
        self.write({
            'lgd_stage': 'in_qc',
            'lgd_qc_received_by': self.env.uid,
            'lgd_qc_received_at': fields.Datetime.now(),
        })
        return True

    # ── Pass ───────────────────────────────────────────────────────────
    def action_lgd_pass(self):
        self._lgd_check_group(GROUP_QUALITY)
        return self._lgd_action_pass()

    def _lgd_action_pass(self):
        def problems(line):
            missing = line._lgd_missing_checks('qc')
            if missing:
                yield _("tick all required checks (missing: %s).") % ", ".join(missing)

        self._lgd_guard(('in_qc',), problems)
        now = fields.Datetime.now()
        self.write({
            'lgd_qc_result': 'pass',
            'lgd_qc_decided_by': self.env.uid,
            'lgd_qc_decided_at': now,
            'lgd_stage': 'qc_passed',
            'lgd_qc_selected': False,
        })
        responsible = self._lgd_responsible_user(
            'lgd.procurement_responsible_login', GROUP_PROCUREMENT)
        for line in self:
            line._lgd_write_order_line_status(
                'payment_pending', ('in_qc_process',))
            deadline = (fields.Date.to_string(line.lgd_commit_deadline)
                        if line.lgd_commit_deadline else _('not set'))
            line._lgd_notify(
                line.order_id, _('QC passed — confirm PO to commit'),
                _("%(stone)s passed QC. Commit by %(deadline)s.") % {
                    'stone': line._lgd_stone_description(),
                    'deadline': deadline,
                },
                responsible)
        return True

    # ── Fail ───────────────────────────────────────────────────────────
    def action_lgd_open_fail_wizard(self):
        self._lgd_check_group(GROUP_QUALITY)
        return self._lgd_open_wizard(
            'lgd.qc.fail.wizard', _('Fail Stone'),
            'contact_stage_bar.view_lgd_qc_fail_wizard_form')

    def _lgd_action_fail(self, reason, note=None):
        self._lgd_guard(('in_qc',))
        now = fields.Datetime.now()
        responsible = self._lgd_responsible_user(
            'lgd.procurement_responsible_login', GROUP_PROCUREMENT)

        for line in self:
            # Capture before the stage is overwritten: only a stone that
            # actually reached stock needs a vendor return.
            reached_stock = (
                line.lgd_stage == 'in_inventory'
                or any(m.state == 'done' and m.picking_code == 'incoming'
                       for m in line.move_ids)
            )
            line.write({
                'lgd_qc_result': 'fail',
                'lgd_fail_reason_id': reason.id if reason else False,
                'lgd_fail_note': note or False,
                'lgd_qc_decided_by': self.env.uid,
                'lgd_qc_decided_at': now,
                'lgd_stage': 'failed',
                'lgd_qc_selected': False,
            })

            if reached_stock:
                line.lgd_return_move_id = line._lgd_create_vendor_return()
            else:
                # No stock work at all.
                line.product_qty = 0

            line._lgd_void_or_cancel_po()

            # A failed member makes the whole set incomplete.
            if (line.lgd_set_label or '').strip():
                members = line._lgd_set_members()
                members.write({'lgd_set_incomplete': True})
            else:
                members = line.browse()

            line._lgd_write_order_line_status('qc_fail', ('in_qc_process',))

            others = (members - line).filtered(lambda m: m.id != line.id)
            note_body = _("%(stone)s failed QC. Reason: %(reason)s.") % {
                'stone': line._lgd_stone_description(),
                'reason': reason.name if reason else _('not given'),
            }
            if others:
                note_body += _(" Set members to match: %s.") % "; ".join(
                    o._lgd_stone_description() for o in others)
            line._lgd_notify(
                line.sale_line_id.order_id,
                _('Replace stone: failed QC'), note_body, responsible)
        return True

    def _lgd_set_members(self):
        """Every stone sharing this invoice number and set label."""
        self.ensure_one()
        label = (self.lgd_set_label or '').strip()
        if not label:
            return self
        return self.search([
            ('lgd_invoice_number', '=', self.lgd_invoice_number),
            ('lgd_set_label', '=', self.lgd_set_label),
        ])

    def action_lgd_open_product_info(self):
        """(i) on the QC Products grid — the product's General Information

        only. Uses the same info-only popup as the receipt screens, so no
        Sales or Purchase page, and therefore no cost or customer price,
        can reach a QC user (§11)."""
        self.ensure_one()
        tmpl = self.product_id.product_tmpl_id
        if not tmpl:
            raise UserError(_("There is no product on this line."))
        return {
            'type': 'ir.actions.act_window',
            'name': _('General Information'),
            'res_model': 'product.template',
            'res_id': tmpl.id,
            'view_mode': 'form',
            'views': [(self.env.ref(
                'contact_stage_bar.view_product_popup_info_only').id, 'form')],
            'target': 'new',
        }

    # ── Mark set complete ─────────────────────────────────────────────
    def action_lgd_set_complete(self):
        self._lgd_check_group(GROUP_QUALITY)
        return self._lgd_action_set_complete()

    def _lgd_action_set_complete(self):
        in_progress_stages = ('expected', 'received', 'with_qc', 'in_qc', False)
        for line in self:
            members = line._lgd_set_members()
            busy = members.filtered(
                lambda m: m.lgd_stage in in_progress_stages)
            if busy:
                raise UserError(
                    _("The set '%(label)s' still has stones in progress: "
                      "%(stones)s.") % {
                        'label': line.lgd_set_label,
                        'stones': ", ".join(b._lgd_label() for b in busy),
                    })
            members.write({'lgd_set_incomplete': False})
        return True

    # ── Accept — the only step that moves stock ───────────────────────
    def action_lgd_accept(self):
        self._lgd_check_group(GROUP_INVENTORY)
        return self._lgd_action_accept()

    def _lgd_action_accept(self):
        def problems(line):
            if line.order_id.state != 'purchase':
                yield _("not confirmed yet. Procurement must confirm the PO first.")

        self._lgd_guard(('qc_passed',), problems)
        now = fields.Datetime.now()
        for line in self:
            line._lgd_accept_chain()
            line.write({
                'lgd_stage': 'in_inventory',
                'lgd_accepted_by': self.env.uid,
                'lgd_accepted_at': now,
            })
        return True

    # ── Mark returned to vendor ───────────────────────────────────────
    def action_lgd_open_return_wizard(self):
        self._lgd_check_group(GROUP_LOGISTICS)
        return self._lgd_open_wizard(
            'lgd.vendor.return.wizard', _('Mark Returned to Vendor'),
            'contact_stage_bar.view_lgd_vendor_return_wizard_form')

    def _lgd_action_mark_returned(self, method, returned_by, note=None,
                                  attachment=None):
        def problems(line):
            if line.lgd_returned_at:
                yield _("already marked returned.")

        self._lgd_guard(('rejected', 'failed'), problems)
        now = fields.Datetime.now()
        for line in self:
            if line.lgd_return_move_id:
                # Validating the return drops qty_received back to 0. No bill,
                # credit note or refund is ever created here.
                line._lgd_validate_moves(line.lgd_return_move_id)
            line.write({
                'lgd_return_method': method,
                'lgd_returned_by': returned_by.id if returned_by else self.env.uid,
                'lgd_returned_at': now,
                'lgd_return_note': note or False,
                'lgd_return_proof_id': attachment.id if attachment else False,
                'lgd_stage': 'returned',
            })
            line._lgd_void_or_cancel_po()
        return True

    # ── validate only some moves on a transfer ───────────────
    def _lgd_validate_moves(self, moves):
        """Complete exactly `moves`; anything else on the same picking goes to a backorder."""
        for picking in moves.picking_id:
            todo = moves.filtered(lambda m: m.picking_id == picking)
            rest = picking.move_ids - todo
            for move in todo:
                move.quantity = move.product_uom_qty
            todo.picked = True
            rest.filtered(lambda m: m.state not in ('done', 'cancel')).picked = False
            picking.with_context(
                lgd_ops_flow=True, skip_backorder=True, skip_sms=True,
                skip_expired=True,
            ).button_validate()
        failed = moves.filtered(lambda m: m.state != 'done')
        if failed:
            raise UserError(
                _("Odoo could not complete: %s. Nothing was saved.")
                % ", ".join(failed.mapped(lambda m: m.product_id.display_name)))

    # ──  Inventory Accept validates the three-step chain ──────
    def _lgd_accept_chain(self):
        """Receipt -> QC transfer -> storage, in one click."""
        self.ensure_one()
        receipt_move = self.move_ids.filtered(
            lambda m: m.picking_code == 'incoming'
            and m.state not in ('done', 'cancel'))[:1]
        if not receipt_move:
            raise UserError(
                _("No receipt found for %s. Has the PO been confirmed?")
                % self._lgd_label())
        self._lgd_validate_moves(receipt_move)          # creates the QC transfer
        qc_move = receipt_move.move_dest_ids.filtered(
            lambda m: m.state not in ('done', 'cancel'))[:1]
        if qc_move:
            self._lgd_validate_moves(qc_move)           # creates the storage move
            store_move = qc_move.move_dest_ids.filtered(
                lambda m: m.state not in ('done', 'cancel'))[:1]
            if store_move:
                self._lgd_validate_moves(store_move)

    # ── vendor return from the QC location ───────────────────
    def _lgd_create_vendor_return(self):
        self.ensure_one()
        receipt_move = self.move_ids.filtered(
            lambda m: m.picking_code == 'incoming' and m.state == 'done')[:1]
        if not receipt_move:
            return self.env['stock.move']
        warehouse = receipt_move.picking_type_id.warehouse_id
        rtv_type = self.env['stock.picking.type']._lgd_get_vendor_return_type(
            warehouse)
        suppliers = self.env.ref('stock.stock_location_suppliers')
        picking = self.env['stock.picking'].create({
            'picking_type_id': rtv_type.id,
            'location_id': warehouse.wh_qc_stock_loc_id.id,
            'location_dest_id': suppliers.id,
            'partner_id': self.order_id.partner_id.id,
            'origin': self.order_id.name,
        })
        return_move = self.env['stock.move'].create({
            'name': self.name,
            'product_id': self.product_id.id,
            'product_uom_qty': self.product_qty,
            'product_uom': self.product_uom.id,
            'location_id': warehouse.wh_qc_stock_loc_id.id,
            'location_dest_id': suppliers.id,
            'picking_id': picking.id,
            'picking_type_id': rtv_type.id,
            'company_id': self.company_id.id,
            'purchase_line_id': self.id,
            'origin_returned_move_id': receipt_move.id,
            'to_refund': True,
        })
        picking.action_confirm()
        picking.action_assign()
        return return_move

    # ── Wizard plumbing ─────────────────────────────────────────────────────
    def _lgd_open_wizard(self, model, name, view_xmlid):
        if not self:
            raise UserError(_("Select at least one stone first."))
        return {
            'type': 'ir.actions.act_window',
            'name': name,
            'res_model': model,
            'view_mode': 'form',
            'views': [(self.env.ref(view_xmlid).id, 'form')],
            'target': 'new',
            'context': dict(self.env.context, active_ids=self.ids,
                            active_model=self._name),
        }


class StockPickingType(models.Model):
    _inherit = 'stock.picking.type'

    @api.model
    def _lgd_get_vendor_return_type(self, warehouse):
        """The LGDRTV operation type for `warehouse`, created on first use.

        Never hardcode a database ID (Rule 2): it is found by sequence code and
        warehouse, and built from the warehouse's own QC location."""
        picking_type = self.search([
            ('sequence_code', '=', 'LGDRTV'),
            ('warehouse_id', '=', warehouse.id),
        ], limit=1)
        if picking_type:
            return picking_type
        suppliers = self.env.ref('stock.stock_location_suppliers')
        return self.create({
            'name': _('Return to Vendor'),
            'sequence_code': 'LGDRTV',
            'code': 'outgoing',
            'warehouse_id': warehouse.id,
            'default_location_src_id': warehouse.wh_qc_stock_loc_id.id,
            'default_location_dest_id': suppliers.id,
            'company_id': warehouse.company_id.id,
        })

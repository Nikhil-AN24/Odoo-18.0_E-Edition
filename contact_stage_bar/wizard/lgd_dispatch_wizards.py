# -*- coding: utf-8 -*-
"""Split a draft parcel — move some of its stones into a sibling
parcel bound for a different destination.

R3 shapes every line of this file: Dispatch (``group_lgd_shipment``) holds
no ``res.partner`` access at all, so nothing here may put a ``res.partner``
field on the wizard's own form — Odoo renders a many2one by resolving its
display name, which would raise ``AccessError`` for that user. Instead the
destinations are ``lgd.dispatch.destination.option`` records, built
server-side by ``lgd.dispatch._lgd_sync_destination_options``, whose
``partner_id`` is group-restricted: only the label ever reaches the client.
"""
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class LgdDispatchDestinationOption(models.TransientModel):
    """One pickable destination for a split, as a real record.

    Why this exists instead of a dynamic ``Selection``: the options depend
    on which parcel is being split, i.e. on ``default_dispatch_id`` in the
    context. Odoo's web client strips the context down to lang / tz / uid /
    allowed_company_ids before calling ``get_views``, so a selection
    callback that reads the context is invoked WITHOUT it and returns an
    empty list - the "Send to" dropdown rendered blank every time. Server
    side the callback was correct, which is why this never showed up in
    tests. Records carry no such problem: the field is an ordinary
    Many2one, domain-filtered on the wizard's own ``dispatch_id``, and
    ``dispatch_id`` reaches the client through ``default_get``, which does
    receive the full context.

    R3 is preserved: ``partner_id`` is group-restricted exactly as
    ``lgd.dispatch.ship_address_id`` is, so a Dispatch user can read the
    ``name`` label and nothing else.
    """
    _name = 'lgd.dispatch.destination.option'
    _description = 'Destination a parcel may be split to'
    _rec_name = 'name'
    _order = 'name'

    name = fields.Char(required=True, readonly=True)
    dispatch_id = fields.Many2one(
        'lgd.dispatch', required=True, readonly=True, index=True,
        ondelete='cascade')
    partner_id = fields.Many2one(
        'res.partner', required=True, readonly=True,
        groups="base.group_system",
        help="Server use only. Never shown to Dispatch (R3).")


class LgdDispatchSplitWizard(models.TransientModel):
    _name = 'lgd.dispatch.split.wizard'
    _description = 'Split a draft parcel'

    dispatch_id = fields.Many2one(
        'lgd.dispatch', string='Parcel', required=True,
        default=lambda self: self.env.context.get('default_dispatch_id'))
    line_ids = fields.Many2many(
        'sale.order.line', string='Stones to move',
        domain="[('lgd_dispatch_id', '=', dispatch_id)]")
    dest_option_id = fields.Many2one(
        'lgd.dispatch.destination.option', string='Send to', required=True,
        domain="[('dispatch_id', '=', dispatch_id)]",
        help="This order's own delivery addresses, built server-side "
             "(§4.5) so Dispatch never needs to read res.partner (R3) — "
             "only the label leaves the server.")

    @api.model
    def default_get(self, fields_list):
        """Build this parcel's destination options.

        default_get is the right hook: unlike get_views it is called with
        the action's full context, so default_dispatch_id is available
        here. Idempotent - options are keyed on (dispatch, partner), so
        re-opening the wizard refreshes labels instead of piling up
        duplicate rows.
        """
        res = super().default_get(fields_list)
        dispatch_id = res.get('dispatch_id') \
            or self.env.context.get('default_dispatch_id')
        if dispatch_id:
            self.env['lgd.dispatch'].browse(
                dispatch_id)._lgd_sync_destination_options()
        return res

    # ── Four refusal checks, collected before raising (R6); a
    # refusal leaves nothing saved (R7) ──────────────────────────────────
    def action_split(self):
        self.ensure_one()
        parcel = self.dispatch_id
        parcel_su = parcel.sudo()
        problems = []

        if parcel.state != 'draft':
            problems.append(_(
                "Only a draft parcel can be split. A parcel that has left "
                "is a customer return."))

        moving = self.line_ids
        if not moving:
            problems.append(_("Tick the stones to move."))
        elif moving - parcel.line_ids:
            problems.append(_(
                "Every stone selected must belong to %s.") % parcel.name)
        elif len(parcel.line_ids) > 1 and moving == parcel.line_ids:
            problems.append(_(
                "You cannot move every stone. Change the destination on "
                "the parcel instead."))

        if not self.dest_option_id:
            problems.append(_("Choose where the moved stones are going."))
        # sudo(): ship_address_id carries groups="base.group_system" (R3) —
        # a Dispatch user may never resolve it directly.
        elif self.dest_option_id.sudo().partner_id.id == parcel_su.ship_address_id.id:
            problems.append(_("Those stones are already going there."))

        if problems:
            raise UserError("\n".join(problems))

        order = parcel.order_id
        Dispatch = self.env['lgd.dispatch'].sudo()
        address = self.dest_option_id.sudo().partner_id

        # ── 1. Find or create the sibling inside the same batch ─────────
        sibling = Dispatch.search([
            ('lgd_batch_ref', '=', parcel_su.lgd_batch_ref),
            ('order_id', '=', order.id),
            ('state', '=', 'draft'),
            ('ship_address_id', '=', address.id),
            ('id', '!=', parcel.id),
        ], limit=1)
        if not sibling:
            sibling = Dispatch.create({
                'order_id': order.id,
                'dispatch_type': parcel_su.dispatch_type,
                'lgd_batch_ref': parcel_su.lgd_batch_ref,
                # Copied, not linked — Dispatch has no contact access, and
                # the label must still show what was actually sent if the
                # customer's address changes later (same rule as _lgd_action_make_parcels).
                'ship_name': address.name,
                'ship_street': address.street,
                'ship_street2': address.street2,
                'ship_city': address.city,
                'ship_state': address.state_id.name,
                'ship_zip': address.zip,
                'ship_country': address.country_id.name,
                'ship_phone': address.phone or address.mobile,
                'ship_address_id': address.id,
                'ship_label': (address.lgd_address_label or address.city
                               or address.name),
                'invoice_number': order.sdk_augmont_number,
            })

        # ── 2. Move BOTH the parcel link and the per-stone override, so
        # order and parcel agree ─────────────────────────────────────────
        moving.sudo().write({
            'lgd_dispatch_id': sibling.id,
            'lgd_ship_address_id': address.id,
        })

        remaining = parcel_su.line_ids

        # ── 3. Re-evaluate the partial-send rule on BOTH parcels.
        # The source parcel's own ship_label/ship_* fields are left alone:
        # the stones that stay still describe the same destination they
        # always did — only the ones that LEFT changed destination. ──────
        if remaining and parcel_su.ship_address_id:
            parcel_su.approval_state = Dispatch._lgd_approval_state_for(
                order, parcel_su.ship_address_id, remaining)
        sibling.approval_state = Dispatch._lgd_approval_state_for(
            order, address, sibling.line_ids)

        # ── 4. Clear paperwork printed for the old stone counts ──────────
        (parcel_su | sibling).write({
            'label_printed': False,
            'packing_list_printed': False,
            'invoice_slip_printed': False,
        })

        # ── 5. One line to the ORDER chatter — no prices, no customer name
        order.sudo().message_post(body=_(
            "%(count)s stone(s) moved to %(label)s by Dispatch."
        ) % {'count': len(moving),
             'label': sibling.ship_label or sibling.ship_city})

        # ── 6. An emptied source parcel is cancelled, never deleted — its
        # number must not be reused. ─────────────────────────────────────
        if not remaining:
            parcel_su.write({'state': 'cancelled'})

        return {'type': 'ir.actions.act_window_close'}


class LgdMemoReturnWizard(models.TransientModel):
    """Record what came back from a memo.

    A client took 100 stones on approval. Some are kept and become a sale;
    the rest come back, either into our own vault or straight back to the
    vendor they were held from. This screen records that decision, once per
    round: ``action_lgd_open_memo_return`` fills it with one row per stone
    that has no outcome yet, so keeping 30 today and 20 next week is simply
    two runs of the same screen.
    """
    _name = 'lgd.memo.return.wizard'
    _description = 'Record a memo return'

    dispatch_id = fields.Many2one(
        'lgd.dispatch', string='Memo parcel', required=True, readonly=True,
        default=lambda self: self.env.context.get('default_dispatch_id'))
    received_on = fields.Date(
        string='Received on', required=True,
        default=fields.Date.context_today,
        help="The day the stones physically came back. It dates the inward "
             "transfer, which is often not the day this is typed in.")
    note = fields.Text(string='Note')
    line_ids = fields.One2many(
        'lgd.memo.return.line', 'wizard_id', string='Stones')

    # ── The nine steps, in order ──────────────────────────────────
    def action_record(self):
        self.ensure_one()
        parcel = self.dispatch_id
        parcel._lgd_check_group()
        parcel_su = parcel.sudo()
        problems = []

        # The same four refusals as the button, re-checked here: the wizard
        # is a separate request, and the parcel may have moved on since it
        # was opened (another user's round, a cancellation).
        if parcel.dispatch_type != 'memo':
            problems.append(_("Only a memo parcel has a return to record."))
        if parcel.state not in ('handed_over', 'delivered'):
            problems.append(_("This memo has not been handed over yet."))
        undecided = parcel._lgd_memo_undecided_lines()
        if not undecided:
            problems.append(_("Every stone on this memo is already decided."))
        rows = self.line_ids
        if not rows:
            problems.append(_("Choose Kept or Returned for every stone."))
        elif rows.filtered(lambda row: not row.outcome):
            # R6 — name every stone still blank, one per line, certificate
            # first, under the spec's own heading.
            problems.append(_("Choose Kept or Returned for every stone."))
            problems.extend(
                row.sale_line_id._lgd_label()
                for row in rows.filtered(lambda r: not r.outcome))
        stray = rows.mapped('sale_line_id') - undecided
        if stray:
            problems.append(_(
                "These stones are not waiting for a decision on %s:")
                % parcel.name)
            problems.extend(line._lgd_label() for line in stray)
        # A RETURNED stone taken in on vendor memo whose computed PO line
        # has gone (the RFQ was split or cancelled after handover) has
        # nowhere to route: ``_compute_return_to`` falls back to 'stock'
        # because there is no ``po_line`` to read ``lgd_stage`` off, but it
        # is excluded from ``_lgd_stock_lines()`` because
        # ``lgd_vendor_memo`` is still set — so it would get NO inward move
        # and NO vendor routing, while ``lgd_memo_return_to`` is stamped
        # 'stock' as if it had come back into our vault. A KEPT stone in
        # the same state is not refused: nothing routes a kept stone either
        # way, so the only risk is cosmetic. Refuse the returned case
        # rather than stamp a value that is not what happened (R6 — every
        # such stone named, raised once).
        orphaned = rows.filtered(
            lambda row: row.outcome == 'returned'
            and row.sale_line_id.sudo().lgd_vendor_memo
            and not row.sale_line_id.sudo().lgd_po_line_id
        ).mapped('sale_line_id')
        if orphaned:
            problems.append(_(
                "These stones were taken on vendor memo but their purchase "
                "order line no longer exists, so there is nowhere to "
                "return them. Ask Procurement to resolve the purchase "
                "order before recording this return:"))
            problems.extend(line._lgd_label() for line in orphaned)
        if problems:
            raise UserError("\n".join(problems))

        now = fields.Datetime.now()
        kept = self.env['sale.order.line']
        returned = self.env['sale.order.line']
        to_stock = self.env['sale.order.line']
        to_vendor = self.env['sale.order.line']

        # ── 1. Stamp every row. return_to comes from the computed value
        # (R5) — it is a fact of the stone's provenance, never a choice. ──
        for row in rows:
            line = row.sale_line_id
            line.sudo().write({
                'lgd_memo_outcome': row.outcome,
                'lgd_memo_return_to': row.return_to,
                'lgd_memo_decided_by': self.env.uid,
                'lgd_memo_decided_at': now,
            })
            if row.outcome == 'kept':
                kept |= line
            else:
                returned |= line
                if row.return_to == 'vendor':
                    to_vendor |= line
                else:
                    to_stock |= line

        # ── 2. Kept stones are a completed sale. ─────────────────────────
        if kept:
            kept.sudo().write({'availability_status': 'delivered'})
        # ── 3. Returned stones are finished on this order. 'memo_returned'
        # is one of LGD_DEAD_STATUSES, which is what frees the stone to be
        # sold to someone else and keeps it off every invoice. ───────────
        if returned:
            returned.sudo().write({'availability_status': 'memo_returned'})

        # ── 4. One inward transfer for the stones that are ours. None is
        # created when nothing routes to stock (Review focus 3). ─────────
        picking = parcel_su._lgd_return_memo_stones(
            to_stock & parcel_su._lgd_stock_lines(), self.received_on)

        # ── 5. Vendor-memo stones go back to the vendor through the screen
        # Logistics already has. They never entered our stock, so there is
        # no quant to move — only the purchase stage and its reason. ─────
        if to_vendor:
            reason = self.env.ref(
                'contact_stage_bar.lgd_fail_reason_cust_memo_return',
                raise_if_not_found=False)
            po_lines = to_vendor.mapped('lgd_po_line_id').sudo()
            if po_lines:
                po_lines.write({
                    'lgd_stage': 'cust_returned',
                    'lgd_fail_reason_id': reason.id if reason else False,
                })

        # ── 6. Invoice the kept stones, and only them (§6.3). Task 11's
        # shared builder returns an EMPTY recordset for an empty input, so
        # an all-returned memo raises no invoice at all rather than an
        # empty one (Review focus 3). ────────────────────────────────────
        order = parcel_su.order_id.sudo()
        invoice = self.env['account.move'].sudo().browse()
        if kept:
            invoice = order._lgd_prepare_invoice_for_lines(kept)
            if invoice:
                invoice._lgd_post_with_platform_number()

        # ── 7. The memo return slip. The report itself is Task 13's; the
        # flag it keys on is written here either way, so a reprint is
        # always available and nothing depends on the report existing. ───
        parcel_su.write({'memo_return_slip_printed': True})

        # ── 8. Close the parcel only when nothing is left undecided, and
        # drop the chasing activities with it (§5.8 stops at memo_closed
        # anyway; the activities already raised must still go). ──────────
        parcel_su.invalidate_recordset(['line_ids'])
        still_open = parcel_su._lgd_memo_undecided_lines()
        closing = not still_open
        if closing:
            close_vals = {
                'state': 'memo_closed',
                'memo_decided_by': self.env.uid,
                'memo_decided_at': now,
            }

            if parcel_su.line_ids.filtered(
                    lambda line: line.lgd_memo_outcome == 'kept'):
                close_vals['dispatch_type'] = 'sale'
            parcel_su.write(close_vals)

            parcel_su.activity_ids.filtered(
                lambda a: a.summary == self.env['lgd.dispatch']
                .LGD_MEMO_OVERDUE_SUMMARY
            ).unlink()

        # ── 9. One line to the parcel chatter. ───────────────────────────
        all_kept = parcel_su.line_ids.filtered(
            lambda line: line.lgd_memo_outcome == 'kept')
        all_back = parcel_su.line_ids.filtered(
            lambda line: line.lgd_memo_outcome == 'returned')
        if closing:
            body = _(
                "Memo closed — %(kept)s kept, %(returned)s returned, "
                "received %(date)s."
            ) % {'kept': len(all_kept), 'returned': len(all_back),
                 'date': self.received_on}
        else:
            body = _(
                "Memo return recorded — %(kept)s kept, %(returned)s "
                "returned, received %(date)s. %(open)s stone(s) are still "
                "undecided."
            ) % {'kept': len(all_kept), 'returned': len(all_back),
                 'date': self.received_on, 'open': len(still_open)}
        if invoice:
            body += Markup("<br/>") + _("Invoice %s raised for the kept "
                                        "stones.") % invoice.name
        if picking:
            body += Markup("<br/>") + _("Inward transfer %s.") % picking.name
        if self.note:
            body += Markup("<br/>") + _("Note: %s") % self.note
        parcel_su.message_post(body=body)

        # The slip report is; return it when it exists, so the
        # press prints in one motion then, and simply closes until then.
        report = self.env.ref(
            'contact_stage_bar.action_lgd_memo_return_slip',
            raise_if_not_found=False)
        if report:
            return report.report_action(parcel)
        return {'type': 'ir.actions.act_window_close'}


class LgdMemoReturnLine(models.TransientModel):
    """One stone on the memo return screen (§4.5)."""
    _name = 'lgd.memo.return.line'
    _description = 'A stone on a memo return'

    wizard_id = fields.Many2one(
        'lgd.memo.return.wizard', ondelete='cascade', index=True)
    sale_line_id = fields.Many2one(
        'sale.order.line', string='Stone', required=True, readonly=True)
    certificate = fields.Char(
        related='sale_line_id.certificate', readonly=True)
    carat = fields.Char(related='sale_line_id.carat_weight', readonly=True)

    outcome = fields.Selection(
        [('kept', 'Kept'), ('returned', 'Returned')], string='Outcome')
    return_to = fields.Selection(
        [('stock', 'Our stock'), ('vendor', 'Back to vendor')],
        string='Returns to', compute='_compute_return_to', readonly=True,
        help="Computed from the stone's own purchase record, never chosen "
             "(R5): a stone we bought comes back to our vault, a stone we "
             "only held on vendor memo goes back to the vendor.")

    @api.depends('sale_line_id')
    def _compute_return_to(self):
        """Verbatim. sudo() because purchase.order.line is another
        module's model and Dispatch holds no rights on it."""
        for row in self:
            po_line = row.sale_line_id.sudo().lgd_po_line_id
            row.return_to = 'vendor' if (
                po_line and (po_line.lgd_stage == 'on_memo'
                             or row.sale_line_id.sudo().lgd_vendor_memo)) else 'stock'
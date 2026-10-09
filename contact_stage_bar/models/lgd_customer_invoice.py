# -*- coding: utf-8 -*-
import logging
import re

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError
from .lgd_inventory_sale_line import LGD_DEAD_STATUSES

_logger = logging.getLogger(__name__)

GROUP_SHIPMENT = 'contact_stage_bar.group_lgd_shipment'

LGD_INVOICEABLE_ORDER_STATES = ('sale', 'done')

class AccountMove(models.Model):
    _inherit = 'account.move'

    lgd_platform_number = fields.Char(
        string="Platform number", copy=False, index=True,
        help="The selling platform's own invoice number for the sale order "
             "this invoice came from. It becomes the invoice number when no "
             "other invoice in the company already carries it.")

    def _lgd_sale_order(self):
        """The sale order this invoice was raised for.
        Resolved through ``sale_line_ids`` rather than a stored link, so it
        cannot go stale and cannot disagree with ``order.invoice_ids``.
        """
        self.ensure_one()
        return self.invoice_line_ids.sale_line_ids.order_id[:1]

    # ── The fallback half: keep it in the JOURNAL's own series ─────
    def _lgd_journal_series_prefix(self):

        self.ensure_one()
        starting = self._get_starting_sequence()
        regex = self._make_regex_non_capturing(
            self._sequence_fixed_regex.replace(r"?P<seq>", ""))
        return starting[:re.match(regex, starting).start(1)]

    def _get_last_sequence(self, relaxed=False, with_prefix=None):
    
        prefix = self.env.context.get('lgd_journal_series_prefix')
        scoped_ids = self.env.context.get('lgd_journal_series_move_ids') or ()
        if with_prefix is None and prefix and self.id in scoped_ids:
            with_prefix = prefix
        return super()._get_last_sequence(
            relaxed=relaxed, with_prefix=with_prefix)

    def _lgd_post_with_platform_number(self):
        problems = []
        for move in self:
            if move.state != 'draft':
                continue
            order = move._lgd_sale_order()
            if order and order.state not in LGD_INVOICEABLE_ORDER_STATES:
                problems.append(_(
                    "%s: the order is still a quotation. Confirm it before "
                    "its invoice is posted.")
                    % (order.sdk_augmont_number or order.name))
        if problems:
            raise UserError("\n".join(problems))

        for move in self:
            if move.state != 'draft':
                # Already posted (a second parcel on the same order) or
                # cancelled: a later parcel posts nothing.
                continue

            order = move._lgd_sale_order()
            platform = (move.lgd_platform_number
                        or order.sdk_augmont_number or '').strip()

            taken = bool(platform) and bool(self.sudo().search_count([
                ('id', '!=', move.id),
                ('move_type', '=', 'out_invoice'),
                ('company_id', '=', move.company_id.id),
                ('state', '!=', 'cancel'),
                ('name', '=', platform),
            ]))

            if platform and not taken:
                move.sudo().write({'name': platform,
                                   'lgd_platform_number': platform})
                move.sudo().action_post()
                continue

            # Fall back to the JOURNAL's own sequence: clear the name so
            # account.move's sequence mixin fills it in while posting, and
            # pin the series it must continue so the number lands in
            # INV/<year>/… and never inside the platform's AUG-… namespace
            # (which is what the previous build did, where it could later
            # collide with a genuine platform number).
            move.sudo().write({'name': False,
                               'lgd_platform_number': platform or False})
            move.sudo().with_context(
                lgd_journal_series_prefix=move._lgd_journal_series_prefix(),
                lgd_journal_series_move_ids=(move.id,),
            ).action_post()
            if platform and order:
                order.sudo().write({'lgd_invoice_fallback_number': True})
                order.sudo().message_post(body=_(
                    "Invoice number %(platform)s is already used by another "
                    "invoice, so this invoice was numbered %(name)s from the "
                    "accounting sequence."
                ) % {'platform': platform, 'name': move.name})
        return True


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    lgd_invoice_ready = fields.Boolean(
        string="Invoice ready", compute='_compute_lgd_invoice_ready',
        help="Every live stone on this order is in the vault, so the whole "
             "order can be invoiced. Readiness is per order, never per "
             "parcel: splitting an order across destinations does not change "
             "it.")
    lgd_invoice_fallback_number = fields.Boolean(
        string="Numbered from the sequence", copy=False, readonly=True,
        help="The platform number was already used by another invoice, so "
             "this order's invoice was numbered from the accounting "
             "sequence instead")

    @api.depends('state',
                 'order_line.lgd_in_vault',
                 'order_line.availability_status',
                 'order_line.product_id',
                 'order_line.display_type',
                 'order_line.lgd_dispatch_id',
                 'order_line.lgd_dispatch_id.dispatch_type',
                 'order_line.lgd_dispatch_id.state')
    def _compute_lgd_invoice_ready(self):
        for order in self:
            auto = order._lgd_auto_invoiceable_lines()
            order.lgd_invoice_ready = (
                order.state in LGD_INVOICEABLE_ORDER_STATES
                and bool(auto)
                and all(line.lgd_in_vault for line in auto))

    # ── Which lines belong on the invoice ───────────────────────────────
    def _lgd_live_invoiceable_lines(self):

        self.ensure_one()
        return self.order_line.filtered(
            lambda line: line.product_id
            and not line.display_type
            and line.availability_status not in LGD_DEAD_STATUSES)

    def _lgd_memo_lines(self):

        self.ensure_one()
        return self.order_line.filtered(
            lambda line: line.lgd_dispatch_id
            and line.lgd_dispatch_id.dispatch_type == 'memo'
            and line.lgd_dispatch_id.state != 'cancelled')

    def _lgd_auto_invoiceable_lines(self):

        self.ensure_one()
        return self._lgd_live_invoiceable_lines() - self._lgd_memo_lines()

    @api.model
    def _lgd_already_invoiced(self, line):
        """True when this stone already sits on a customer invoice that is
        not cancelled. The single guarantee that a stone is never invoiced
        twice.

        A credit note (``out_refund``) is deliberately **not** counted: like
        standard Odoo, a stone whose invoice was corrected by a credit note
        can be invoiced again. Only the invoice itself blocks.
        """
        return bool(line.invoice_lines.filtered(
            lambda aml: aml.move_id.move_type == 'out_invoice'
            and aml.move_id.state != 'cancel'))

    @api.model
    def _lgd_posted_invoiced(self, line):

        return bool(line.invoice_lines.filtered(
            lambda aml: aml.move_id.move_type == 'out_invoice'
            and aml.move_id.state not in ('draft', 'cancel')))

    def _lgd_open_draft_invoice(self):
        """This order's draft customer invoice, if it has one."""
        self.ensure_one()
        return self.invoice_ids.filtered(
            lambda move: move.move_type == 'out_invoice'
            and move.state == 'draft')[:1]

    # ── Build the draft ─────────────────
    def _lgd_prepare_invoice_for_lines(self, lines):

        self.ensure_one()
        Move = self.env['account.move'].sudo()

        if self.state not in LGD_INVOICEABLE_ORDER_STATES:
            raise UserError(_(
                "%s: the order is still a quotation. Confirm it before "
                "raising its invoice.")
                % (self.sdk_augmont_number or self.name))

        invoiceable = (lines & self._lgd_live_invoiceable_lines()).filtered(
            lambda line: not self._lgd_already_invoiced(line))
        if not invoiceable:
            return Move.browse()

        line_commands = []
        for line in invoiceable.sorted('sequence'):
            values = line._prepare_invoice_line()
            if not values.get('quantity'):
                continue
            line_commands.append(Command.create(values))
        if not line_commands:
            return Move.browse()

        draft = self._lgd_open_draft_invoice()
        if draft:
            draft.sudo().write({'invoice_line_ids': line_commands})
            return draft.sudo()

        values = self._prepare_invoice()
        values['invoice_line_ids'] = line_commands
        values['lgd_platform_number'] = self.sdk_augmont_number or False
        return Move.create(values)

    # ── Prepare automatically when the order becomes ready ───────
    def _lgd_try_create_invoice(self):

        prepared = self.env['account.move'].sudo().browse()
        for order in self:
            if not order.lgd_invoice_ready:
                continue
            prepared |= order._lgd_prepare_invoice_for_lines(
                order._lgd_auto_invoiceable_lines())
        return prepared

    # ── A memo is not a sale: take its stones off the draft ──────
    def _lgd_withdraw_memo_lines_from_draft(self):
        """Remove from the order's open draft invoice any stone that has
        since been committed to a memo parcel, and withdraw the draft
        altogether when nothing is left on it.

        Why this exists: ``dispatch_type`` lives on ``lgd.dispatch``, and the
        parcel is only made *after* the stones are vaulted — so has
        already raised a full-order draft by the time anyone says "this goes
        out on memo". Left alone, that draft is both postable (it covers
        stones that may yet come back) and poisonous to the memo return:
        every kept stone would already be on an invoice, so 
        ``_lgd_prepare_invoice_for_lines(kept)`` would filter them all out
        and invoice nothing.

        Withdrawing rather than cancelling: a draft that was never posted and
        now covers nothing has no accounting value, and a cancelled husk on
        the order would still read as "this order was invoiced". The order's
        chatter carries the audit trail instead.
        """
        for order in self:
            draft = order._lgd_open_draft_invoice()
            if not draft:
                continue
            memo = order._lgd_memo_lines()
            if not memo:
                continue
            doomed = draft.invoice_line_ids.filtered(
                lambda aml: aml.sale_line_ids & memo)
            if not doomed:
                continue
            count = len(doomed)
            if doomed == draft.invoice_line_ids:
                draft.sudo().unlink()
                body = _(
                    "%s stone(s) on this order are going out on memo, so the "
                    "draft invoice has been withdrawn. A memo is not a sale: "
                    "the stones the customer keeps are invoiced at the "
                    "return decision."
                ) % count
            else:
                draft.sudo().write({
                    'invoice_line_ids': [Command.delete(aml.id)
                                         for aml in doomed],
                })
                body = _(
                    "%s stone(s) are going out on memo and have been taken "
                    "off draft invoice %s. They are invoiced only if the "
                    "customer keeps them."
                ) % (count, draft.display_name)
            order.sudo().message_post(body=body)
        return True

    # ── Post from the order, when no parcel is left ──────────────
    def _lgd_check_invoice_group(self):
        """Who may post a customer invoice from the order.

        This button posts a legal document and does its writes under
        ``sudo()`` — deliberately, because Sales and Dispatch both hold only
        read-only ``account.move``. Without this check that ``sudo()``
        is privilege escalation: anyone who can write a ``sale.order`` could
        post an invoice. Gives the button to Dispatch, so Dispatch (and
        Settings) is who may press it. Mirrors
        ``lgd.dispatch._lgd_check_group``.
        """
        if self.env.su \
                or self.env.user.has_group(GROUP_SHIPMENT) \
                or self.env.user.has_group('base.group_system'):
            return
        raise AccessError(_(
            "You are not allowed to post a customer invoice. Ask an "
            "administrator for the Dispatch role."))

    def action_lgd_post_invoice(self):
        """Dispatch's **Post invoice** button on the order.

        For the case where the last parcel has already gone out under a
        partial approval, the order has since become ready, and there is no
        further handover to hang the posting on.
        """
        self._lgd_check_invoice_group()
        problems = []
        for order in self:
            if order.state not in LGD_INVOICEABLE_ORDER_STATES:
                problems.append(_(
                    "%s: the order is still a quotation. Confirm it before "
                    "posting its invoice.")
                    % (order.sdk_augmont_number or order.name))
                continue
            order._lgd_try_create_invoice()
            draft = order._lgd_open_draft_invoice()
            if not draft:
                if order.lgd_invoice_ready:
                    problems.append(_(
                        "%s: its invoice has already been posted.")
                        % (order.sdk_augmont_number or order.name))
                else:
                    problems.append(_(
                        "%s: not every stone is in the vault yet, so there "
                        "is nothing to invoice.")
                        % (order.sdk_augmont_number or order.name))
        if problems:
            # Every failing order collected, then raised once.
            raise UserError("\n".join(problems))

        for order in self:
            order._lgd_open_draft_invoice()._lgd_post_with_platform_number()
        return True


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    def _lgd_check_memo_not_invoiced_on_arrival(self, dispatch_id):

        if not dispatch_id:
            return
        dispatch = self.env['lgd.dispatch'].sudo().browse(dispatch_id)
        if dispatch.dispatch_type != 'memo':
            return
        SaleOrder = self.env['sale.order']
        found = [line._lgd_label() for line in self.sudo()
                 if SaleOrder._lgd_posted_invoiced(line)]
        if found:
            raise UserError(_(
                "These stones are already on a posted customer invoice, so "
                "they cannot be moved onto a memo parcel:\n%s\n\n"
                "A memo stone may come back, and nothing is ever invoiced "
                "for a returned stone. Credit the invoice first, or send "
                "these stones out as the sale they already are."
            ) % "\n".join(found))

    def write(self, vals):

        if 'lgd_dispatch_id' in vals:
            self._lgd_check_memo_not_invoiced_on_arrival(
                vals.get('lgd_dispatch_id'))
        res = super().write(vals)
        if 'lgd_dispatch_id' in vals:
            orders = self.mapped('order_id').sudo()
            orders._lgd_withdraw_memo_lines_from_draft()
            orders._lgd_try_create_invoice()
        return res


class LgdDispatch(models.Model):
    _inherit = 'lgd.dispatch'

    def _lgd_sync_invoice_scope(self):
        """Re-scope the orders' draft invoices around ``self``'s types.

        A parcel switched to **memo** pulls its stones off the draft;
        a parcel switched back to **sale** gives its stones another chance to
        be prepared, which is why the sale branch also runs. Both calls are
        idempotent.
        """
        orders = self.mapped('order_id').sudo()
        orders._lgd_withdraw_memo_lines_from_draft()
        orders._lgd_try_create_invoice()
        return True

    def _lgd_check_memo_not_invoiced(self):

        SaleOrder = self.env['sale.order']
        found = []
        for parcel in self:
            for line in parcel.sudo().line_ids:
                if SaleOrder._lgd_posted_invoiced(line):
                    found.append(line._lgd_label())
        if found:
            raise UserError(_(
                "These stones are already on a posted customer invoice, so "
                "this parcel cannot go out on memo:\n%s\n\n"
                "A memo stone may come back, and nothing is ever invoiced "
                "for a returned stone. Credit the invoice first, or hand "
                "this parcel over as the sale it already is."
            ) % "\n".join(found))
        return True

    @api.model_create_multi
    def create(self, vals_list):
        parcels = super().create(vals_list)
        memo = parcels.filtered(lambda p: p.dispatch_type == 'memo')
        if memo:
            memo._lgd_check_memo_not_invoiced()
            memo._lgd_sync_invoice_scope()
        return parcels

    def write(self, vals):
        if vals.get('dispatch_type') == 'memo':
            self.filtered(
                lambda p: p.dispatch_type != 'memo'
            )._lgd_check_memo_not_invoiced()
        res = super().write(vals)
        if 'dispatch_type' in vals or 'state' in vals:
            self._lgd_sync_invoice_scope()
        return res

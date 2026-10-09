# -*- coding: utf-8 -*-
import logging
import re

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .lgd_ops_mixin import lgd_notify, lgd_responsible_user

_logger = logging.getLogger(__name__)

# Shared with lgd_inventory_sale_line.py / lgd_ops_purchase_line.py /
# lgd_dispatch.py: the Sales team that should be nagged about an address
# the website created and nobody has checked yet.
GROUP_SALES = 'contact_stage_bar.group_lgd_sales'

# How many of the junk texts _lgd_convert_text_addresses skips get kept
# verbatim in its return value / log line for a human to eyeball. Capped
# so a run with thousands of skips cannot blow up the log or the result dict.
_LGD_MAX_SKIPPED_TEXTS_LOGGED = 50


class ResPartner(models.Model):
    _inherit = "res.partner"

    lgd_address_label = fields.Char(
        string="Address Label", index=True,
        help="Short name shown everywhere this address is picked from a "
             "list (e.g. a label printed on a dispatch slip). Defaults "
             "from the city and street when left blank.")
    lgd_is_primary = fields.Boolean(
        string="Primary Address",
        help="The default delivery address for this customer - the one "
             "pre-filled on a new order when no other address is chosen. "
             "Only one address per customer can be the primary one: "
             "ticking a different address automatically unticks the "
             "previous one.")
    lgd_from_website = fields.Boolean(
        string="From Platform", readonly=True,
        help="Ticked automatically when this address was created from a "
             "free-text address typed on the website/platform order, "
             "rather than entered here by a sales person. It flags an "
             "address nobody has checked yet - use the 'Needs checking' "
             "filter on the Customers list to find them all. It is set by "
             "the system and cannot be edited by hand.")
    lgd_source_text = fields.Text(
        string="Original Text", readonly=True,
        help="The raw free-text address this record was created from, "
             "kept for reference after it is turned into a real address.")
    lgd_can_manage_delivery_addresses = fields.Boolean(
        string="Can Manage Delivery Addresses", compute='_compute_lgd_can_manage_delivery_addresses',
        help="Technical: true for the current user when they belong to one "
             "of _LGD_ADDRESS_MANAGER_GROUPS. Used to gate both creating "
             "and deleting delivery addresses from the inline list the "
             "same way.")

    # Fields a delivery address may never carry a value for.
    _LGD_DELIVERY_BLANK_FIELDS = ('email', 'mobile', 'function', 'contact_person_name')

    # Who may add or remove a customer's delivery addresses. Deleting one is
    # destructive and silently redirects future shipments, so it is a
    # manager-level action: an ordinary Sales user may read the address book
    # but not edit its membership. base.group_system is Odoo's Administrator.
    _LGD_ADDRESS_MANAGER_GROUPS = (
        'base.group_system',
        'contact_stage_bar.group_lgd_superadmin',
        'contact_stage_bar.group_lgd_regional_sales_head',
        'contact_stage_bar.group_lgd_sales_manager',
        'contact_stage_bar.group_lgd_procurement_manager',
    )

    def _compute_lgd_can_manage_delivery_addresses(self):
        can_manage = any(
            self.env.user.has_group(g)
            for g in self._LGD_ADDRESS_MANAGER_GROUPS)
        for partner in self:
            partner.lgd_can_manage_delivery_addresses = can_manage

    def _lgd_default_label(self, city, street):
        label = " - ".join(p for p in (city, street) if p)
        return label.strip() or False

    def _lgd_scrub_delivery_vals(self, vals):
        """Force the person-identifying fields to False in ``vals`` in
        place. Only called once the caller has established the record(s)
        are (or are becoming) a delivery address."""
        for fname in self._LGD_DELIVERY_BLANK_FIELDS:
            if fname in self._fields:
                vals[fname] = False

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('type') == 'delivery':
                self._lgd_scrub_delivery_vals(vals)
                if not vals.get('lgd_address_label'):
                    label = self._lgd_default_label(
                        vals.get('city'), vals.get('street'))
                    if label:
                        vals['lgd_address_label'] = label
                if not vals.get('name'):
                    # A partner with a blank name is not harmless: Odoo falls
                    # back to the PARENT's name for display_name, so a
                    # nameless delivery child renders as plain "VB Arts" -
                    # indistinguishable from the account itself. One such
                    # record is why the duplicate-GSTIN warning looked like it
                    # was pointing at the account's own address book.
                    vals['name'] = vals.get('lgd_address_label') \
                        or vals.get('city') or _("Delivery Address")
        partners = super().create(vals_list)
        # Enforce "at most one primary per parent" for any record created
        # already-primary. Plain write(): see write() below for why this
        # cannot loop.
        new_primaries = partners.filtered(
            lambda p: p.type == 'delivery' and p.lgd_is_primary)
        for partner in new_primaries:
            partner._lgd_clear_sibling_primaries()
        return partners

    def write(self, vals):
        # Each record's guard is decided from the type it will HAVE after
        # this write (vals wins when it sets 'type'), not the type it has
        # right now - so a record being converted AWAY from delivery in
        # this same call is no longer scrubbed, while one being converted
        # INTO delivery still is.
        new_type = vals.get('type')
        guarded = self.filtered(
            lambda p: (new_type if new_type is not None else p.type) == 'delivery')
        untouched = self - guarded

        # Never mutate the caller's vals dict in place - Odoo code commonly
        # reuses one vals dict across several write() calls in a loop, and
        # mutating it would silently force person fields to False on every
        # subsequent write by that caller, including non-delivery ones.
        if guarded:
            scrubbed_vals = dict(vals)
            self._lgd_scrub_delivery_vals(scrubbed_vals)
        if guarded and untouched:
            # A single write() call is touching both delivery and
            # non-delivery records at once (e.g. a bulk write mixing
            # types): split the vals so the forced-blank fields can
            # never leak onto the non-delivery records.
            res = super(ResPartner, guarded).write(scrubbed_vals)
            res = super(ResPartner, untouched).write(vals) and res
        elif guarded:
            res = super(ResPartner, guarded).write(scrubbed_vals)
        else:
            res = super().write(vals)

        if guarded and not vals.get('lgd_address_label'):
            for partner in guarded.filtered(lambda p: not p.lgd_address_label):
                label = self._lgd_default_label(partner.city, partner.street)
                if label:
                    # Plain write(): vals here carries lgd_address_label,
                    # so this call's own guard above is skipped on
                    # re-entry - it cannot loop back into this branch.
                    partner.write({'lgd_address_label': label})

        if vals.get('lgd_is_primary'):
            # Guard against recursion: this branch only fires when vals
            # itself sets lgd_is_primary truthy. The sibling-clearing call
            # below writes lgd_is_primary=False on a DIFFERENT recordset,
            # so even though that write re-enters this method, vals there
            # is falsy and this branch does not fire again.
            new_primaries = self.filtered(
                lambda p: p.type == 'delivery' and p.lgd_is_primary)
            for partner in new_primaries:
                partner._lgd_clear_sibling_primaries()
        return res

    @api.onchange('child_ids')
    def _onchange_lgd_single_primary_address(self):
        """Untick the other delivery addresses the moment one is ticked.

        create()/write() below already guarantee at most one primary per
        parent, but they only run on save: in the editable Delivery Addresses
        list a user could tick three boxes and see three ticks until they
        saved, which reads as "a customer can have several primary
        addresses". This makes the single-choice rule visible immediately.

        Which one wins is not a guess: a row the user just ticked has
        ``lgd_is_primary`` True while its ``_origin`` (the unmodified database
        record) still has False. Rows that were already primary before this
        edit have it True in both. So the newly-ticked row is the one that
        differs, and every other primary is cleared. A brand-new unsaved row
        has an empty ``_origin``, so it also reads as newly ticked.
        """
        primaries = self.child_ids.filtered(
            lambda p: p.type == 'delivery' and p.lgd_is_primary)
        if len(primaries) < 2:
            return
        newly_ticked = primaries.filtered(lambda p: not p._origin.lgd_is_primary)
        # If several read as new (e.g. a multi-row paste) keep the last, which
        # is the one the user touched most recently. If none do, every primary
        # was already primary in the database - a state create()/write() cannot
        # produce, so leave it alone rather than silently picking a winner.
        if not newly_ticked:
            return
        keep = newly_ticked[-1]
        for address in primaries - keep:
            address.lgd_is_primary = False

    # Models that point a Many2one at a delivery address. Each entry is
    # (model, field, human label). Used to explain a blocked delete instead
    # of letting Postgres surface "sale_order_partner_shipping_id_fkey".
    _LGD_ADDRESS_REFERENCES = (
        ('sale.order', 'partner_shipping_id', "Sales Orders"),
        ('sale.order.line', 'lgd_ship_address_id', "order lines (Deliver to)"),
        ('lgd.dispatch', 'ship_address_id', "Dispatch parcels"),
    )

    def unlink(self):
        """Guard deleting a delivery address: who may do it, and whether
        the address is still referenced anywhere."""
        addresses = self.filtered(lambda p: p.type == 'delivery')
        if not addresses:
            return super().unlink()

        # security/ir.model.access.csv grants the four LGD manager groups
        # unlink on res.partner so the trash icon in the Delivery Addresses
        # list actually works for them. An ir.model.access row is
        # model-wide, so on its own that would also let them delete any
        # customer, vendor or contact in the database - far more than was
        # asked for. This keeps the grant to what it was meant for: unless
        # the user is a real contacts administrator, the only partners they
        # may delete are delivery addresses.
        real_contact_admin = self.env.su \
            or self.env.user.has_group('base.group_system') \
            or self.env.user.has_group('base.group_partner_manager')
        others = self - addresses
        if others and not real_contact_admin:
            raise AccessError(_(
                "You may only delete delivery addresses, not other "
                "contacts or customer accounts."))

        if not self.env.su and not any(
                self.env.user.has_group(g)
                for g in self._LGD_ADDRESS_MANAGER_GROUPS):
            raise AccessError(_(
                "You are not allowed to delete a delivery address.\n\n"
                "Deleting one changes where future orders ship to, so it "
                "is limited to Administrator, LGD SuperAdmin, LGD Regional "
                "Sales Head, LGD Sales Manager and LGD Procurement "
                "Manager."))

        # Collect every blocker first and report them in one message (R6),
        # rather than stopping at whichever foreign key Postgres hit first.
        blocked = []
        for address in addresses:
            users = []
            for model, fname, label in self._LGD_ADDRESS_REFERENCES:
                Model = self.env.get(model)
                if Model is None or fname not in Model._fields:
                    continue
                count = Model.sudo().search_count([(fname, '=', address.id)])
                if count:
                    users.append("%d %s" % (count, label))
            if users:
                blocked.append("  \u2022 %s - used by %s" % (
                    address.lgd_address_label or address.display_name,
                    ", ".join(users)))
        if blocked:
            raise UserError(_(
                "These delivery addresses cannot be deleted because "
                "existing records still point at them:\n\n%s\n\n"
                "Deleting one would break that history. Archive it "
                "instead - open the address in the Contacts app and use "
                "Actions > Archive. It then disappears from this list and "
                "from the pickers, while the orders that used it stay "
                "intact."
            ) % "\n".join(blocked))
        return super().unlink()

    def _lgd_clear_sibling_primaries(self):
        """Clear ``lgd_is_primary`` on every OTHER delivery child of this
        record's own parent. Never touches another customer's addresses."""
        self.ensure_one()
        if not self.parent_id:
            return
        siblings = self.parent_id.child_ids.filtered(
            lambda p: p.type == 'delivery'
            and p.id != self.id
            and p.lgd_is_primary)
        if siblings:
            siblings.write({'lgd_is_primary': False})

    def _lgd_primary_address(self):
        """The child address marked primary, else the record itself."""
        self.ensure_one()
        primary = self.child_ids.filtered(
            lambda p: p.type == 'delivery' and p.lgd_is_primary)[:1]
        return primary or self

    # ── free-text address conversion + order_source backfill ───

    _LGD_JUNK_MIN_LENGTH = 8

    @api.model
    def _lgd_normalise_address_text(self, text):
        """Normalise a free-text address into a comparison key: lower-cased,
        punctuation stripped, whitespace collapsed.

        This is a SHARED interface: Task 8 (matching an incoming website
        payload against an existing delivery child) calls this exact same
        helper, so its contract (lower-case, strip punctuation, collapse
        whitespace) must stay stable.
        """
        text = (text or '').lower()
        text = re.sub(r'[^\w\s]', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    @api.model
    def _lgd_is_junk_address_text(self, text):
        """Best-effort junk filter for a free-text shipping address.

        Skips text under 8 characters outright. Beyond length, the one
        reliable signal is a digit: a genuine Indian shipping address is
        always anchored by at least one - a house/flat number or a PIN
        code - so text with no digit at all (however long, and even
        though it has letters, e.g. 'ffug, ugug') is treated as junk. Pure
        numeric text (no letters) is NOT treated as junk by this rule -
        only the no-digit case is - matching the stored requirement that
        text with neither a letter nor a digit is junk, while catching
        the letters-only case that rule alone misses.
        """
        text = (text or '').strip()
        if len(text) < self._LGD_JUNK_MIN_LENGTH:
            return True
        if not any(ch.isdigit() for ch in text):
            return True
        return False

    @api.model
    def _lgd_parse_address_text(self, text):
        """Best-effort split of comma-separated free text into
        street/street2/city/zip, plus a label built from the first two
        comma parts. Never raises - anything left over lands in street2."""
        raw_parts = [p.strip() for p in (text or '').split(',') if p.strip()]
        parts = list(raw_parts)
        vals = {}

        zip_code = False
        if parts and re.fullmatch(r'\d{4,8}', parts[-1].replace(' ', '')):
            zip_code = parts.pop()
        if parts:
            vals['city'] = parts.pop()
        if parts:
            vals['street'] = parts.pop(0)
        if parts:
            vals['street2'] = ', '.join(parts)
        if zip_code:
            vals['zip'] = zip_code

        label = ' - '.join(raw_parts[:2]) or False
        return vals, label

    def _lgd_responsible_user(self, param_key, group_xmlid):
        return lgd_responsible_user(self.env, param_key, group_xmlid)

    def _lgd_notify(self, record, summary, note, user):
        return lgd_notify(self.env, record, summary, note, user)

    @api.model
    def _lgd_match_or_create_address(self, partner, text, order=None):
        """Match free-text ``text`` against an existing delivery child of
        ``partner``'s commercial entity, or create a new one flagged
        ``lgd_from_website`` for Sales to check.

        Matching reuses the exact same normaliser as the Task 6 migration
        (``_lgd_normalise_address_text``), compared against each
        candidate's STORED ``lgd_source_text`` - never its current
        street/label/city. This is deliberate: once Sales has corrected a
        generated address (``lgd_from_website`` False means "checked"),
        this method must link to it and leave it alone - never refresh
        ``street``, ``lgd_address_label`` or ``lgd_source_text`` on a
        match, whatever the flag's value. A match is a plain return; the
        only branch that ever writes is the create branch.

        ``order`` is optional and only used to raise exactly one activity
        for Sales when a NEW address is created - never on a match, which
        is the whole point of the guard above. Passing it is how the
        inbound website sync (controllers/website_api_route.py) gets the
        activity without this generic res.partner helper needing to know
        anything about sale orders beyond "something to post an activity
        on".
        """
        parent = partner.commercial_partner_id or partner
        text = (text or '').strip()

        if not text or self._lgd_is_junk_address_text(text):
            # Nothing usable to match or create from - degrade to the
            # parent itself rather than manufacture a junk delivery
            # address. No activity either: nothing was created.
            return parent

        key = self._lgd_normalise_address_text(text)
        existing = self.sudo().search([
            ('parent_id', '=', parent.id),
            ('type', '=', 'delivery'),
            ('lgd_source_text', '!=', False),
        ])
        for candidate in existing:
            if self._lgd_normalise_address_text(candidate.lgd_source_text) == key:
                return candidate

        vals, label = self._lgd_parse_address_text(text)
        vals.update({
            'parent_id': parent.id,
            'type': 'delivery',
            'lgd_source_text': text,
            'lgd_from_website': True,
        })
        if label:
            vals['lgd_address_label'] = label
        addr = self.sudo().create(vals)

        if order is not None:
            responsible = self._lgd_responsible_user(
                'lgd.sales_responsible_login', GROUP_SALES)
            self._lgd_notify(
                order.sudo(),
                _("New delivery address from the platform"),
                _("New delivery address from the platform — please check "
                  "it: %s.") % (addr.lgd_address_label or addr.display_name),
                responsible,
            )
        return addr

    @api.model
    def _lgd_convert_text_addresses(self):
        """Turn sale.order.shipping_address free text into real delivery
        child contacts, and point partner_shipping_id at them.

        Idempotent by design: a candidate match is found by comparing the
        NORMALISED free text against an existing delivery child's stored
        ``lgd_source_text`` - never against its current (possibly
        since-corrected) street/label/city - so re-running after Sales
        has fixed up a generated address neither reverts the correction
        nor creates a duplicate for the same text.

        Writes ONLY ``partner_shipping_id`` on the order, and only when
        it is not already pointing at the matched address - a clean
        re-run touches zero order rows.

        Returns {'created': int, 'linked': int, 'skipped': int,
        'skipped_texts': [str, ...]} - the keys from before Task 8 are
        unchanged, with 'skipped_texts' added alongside them. It carries
        the actual free text behind every skip (both the junk-text skips
        and the no-parent skips below), capped at
        ``_LGD_MAX_SKIPPED_TEXTS_LOGGED`` entries, so a human can eyeball
        what the junk rule rejected before this runs against the 
        production orders the rule was tuned against. The junk rule
        itself is untouched - this only makes its decisions visible.
        """
        SaleOrder = self.env['sale.order'].sudo()
        orders = SaleOrder.search([('shipping_address', '!=', False)])

        created = linked = skipped = 0
        skipped_texts = []

        existing = self.sudo().search([
            ('type', '=', 'delivery'),
            ('lgd_source_text', '!=', False),
        ])
        index = {}
        for rec in existing:
            key = (rec.parent_id.id,
                   self._lgd_normalise_address_text(rec.lgd_source_text))
            index.setdefault(key, rec.id)

        for order in orders:
            text = (order.shipping_address or '').strip()
            if self._lgd_is_junk_address_text(text):
                skipped += 1
                if len(skipped_texts) < _LGD_MAX_SKIPPED_TEXTS_LOGGED:
                    skipped_texts.append(text)
                continue

            parent = order.partner_id.commercial_partner_id or order.partner_id
            if not parent:
                skipped += 1
                if len(skipped_texts) < _LGD_MAX_SKIPPED_TEXTS_LOGGED:
                    skipped_texts.append(text)
                continue

            index_key = (parent.id, self._lgd_normalise_address_text(text))
            addr_id = index.get(index_key)
            if addr_id:
                linked += 1
                addr = self.sudo().browse(addr_id)
            else:
                vals, label = self._lgd_parse_address_text(text)
                vals.update({
                    'parent_id': parent.id,
                    'type': 'delivery',
                    'lgd_source_text': text,

                })
                if label:
                    vals['lgd_address_label'] = label
                addr = self.sudo().create(vals)
                index[index_key] = addr.id
                created += 1

            if order.partner_shipping_id.id != addr.id:
                order.write({'partner_shipping_id': addr.id})

        # Run AFTER the order loop so a block that happens to
        # match a free text already converted above links to that child
        # rather than making a near-duplicate of it.
        primary = self._lgd_convert_parent_shipping_blocks()

        _logger.info(
            "Address conversion: %d created, %d linked, %d skipped (of %d orders with free-text shipping_address).",
            created, linked, skipped, len(orders))
        if skipped_texts:
            _logger.info(
                "Address conversion: skipped texts (first %d of %d): %r",
                len(skipped_texts), skipped, skipped_texts)
        return {
            'created': created, 'linked': linked, 'skipped': skipped,
            'skipped_texts': skipped_texts,
            'primary_created': primary['created'],
            'primary_linked': primary['linked'],
        }

    @api.model
    def _lgd_convert_parent_shipping_blocks(self):
        """§ "Copy the parent's existing ``shipping_*`` block
        into a child address marked ``lgd_is_primary`` when the parent has
        one."

        Never implemented before (final-review defect C). Combined with
        primary-address default (also missing, defect B), no order
        would ever have defaulted to a primary address after the
        migration: the two defects compounded.

        The parent's flat block lives on ``res.partner`` as
        ``shipping_street`` / ``shipping_street2`` / ``shipping_city`` /
        ``shipping_zip`` / ``shipping_state_id`` / ``shipping_country_id``,
        with the stored text copy ``shipping_address`` computed from them
        (``models/res_partner.py``). Only top-level partners are
        considered: ``res.partner.create()`` mirrors billing onto
        ``shipping_*`` for every record it makes, delivery children
        included, so a child would otherwise be mistaken for a parent with a block of its own.

        Counted separately from the free-text conversion's own ``created``
        / ``linked`` so the pre-existing keys of the result dict keep
        meaning exactly what they meant before.

        **Idempotent, by two rules:**

        * A parent that ALREADY has a primary delivery child is skipped
          outright — so a re-run neither creates a second address nor
          flips the flag a second time (which would also re-run the
          sibling-clearing write).
        * Otherwise the block's text is matched, normalised, against the
          existing children's ``lgd_source_text`` — the same evidence the
          free-text conversion uses — and an existing child is marked
          rather than duplicated.

        Returns ``{'created': int, 'linked': int}``.
        """
        parents = self.sudo().search([
            ('parent_id', '=', False),
            '|', ('shipping_street', '!=', False),
                 ('shipping_city', '!=', False),
        ])
        created = linked = 0
        for parent in parents:
            children = parent.child_ids.filtered(
                lambda p: p.type == 'delivery')
            if children.filtered('lgd_is_primary'):
                continue
            text = (parent.shipping_address or '').strip()
            key = self._lgd_normalise_address_text(text)
            match = children.filtered(
                lambda p: p.lgd_source_text
                and self._lgd_normalise_address_text(
                    p.lgd_source_text) == key)[:1] if key else children[:0]
            if match:
                match.write({'lgd_is_primary': True})
                linked += 1
                continue
            self.sudo().create({
                'parent_id': parent.id,
                'type': 'delivery',
                'lgd_is_primary': True,
                'lgd_source_text': text or False,
                'street': parent.shipping_street or False,
                'street2': parent.shipping_street2 or False,
                'city': parent.shipping_city or False,
                'zip': parent.shipping_zip or False,
                'state_id': parent.shipping_state_id.id or False,
                'country_id': parent.shipping_country_id.id or False,
            })
            created += 1
        _logger.info(
            "Primary address conversion: %d created, %d linked from an "
            "existing child (of %d parents carrying a shipping block).",
            created, linked, len(parents))
        return {'created': created, 'linked': linked}

    def action_lgd_new_delivery_address(self):
        """Open a blank delivery-address form for this customer.

        The view gates this button to contact_stage_bar.group_lgd_sales
        and base.group_system; the inline list itself is read-only
        (create="0") so this is the only way to add a new address.
        """
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("New Delivery Address"),
            'res_model': 'res.partner',
            'view_mode': 'form',
            'view_id': self.env.ref(
                'contact_stage_bar.view_lgd_delivery_address_form').id,
            'target': 'new',
            'context': {
                'default_parent_id': self.id,
                'default_type': 'delivery',
                'default_country_id': self.country_id.id,
                'default_stage_id': False,
            },
        }

class SaleOrder(models.Model):
    _inherit = "sale.order"

    # ── The order defaults to the customer's primary address ────
    @api.depends('partner_id')
    def _compute_partner_shipping_id(self):
        """ "On the order, **Delivery Address** defaults to the
        customer's ``lgd_is_primary`` child, or the customer itself when
        none is marked."

        Core's own compute calls ``address_get(['delivery'])``, which picks
        an ARBITRARY delivery child — with three addresses on file the
        order could default to any of them. ``_lgd_primary_address()``
        existed for this since but had no caller; this is it.

        Why override the compute rather than ``address_get``: ``address_get``
        is read by pickings, invoices and every other caller of a partner's
        delivery address, and is a rule about the *order*. Overriding
        it here keeps the change where the spec puts it.

        A deliberate choice is never stomped, for the same reason core's
        isn't: ``partner_shipping_id`` is stored and ``readonly=False``, and
        this compute depends on ``partner_id`` alone — so it re-runs only
        when the customer changes, exactly as before. Picking a different
        address by hand sticks."""
        super()._compute_partner_shipping_id()
        for order in self:
            if order.partner_id:
                order.partner_shipping_id = \
                    order.partner_id._lgd_primary_address()

    @api.model
    def _lgd_backfill_order_source(self):
        """ fill blank order_source.
        'website' when sdk_augmont_number is set, or any augmont.api.retry
        sync history exists for the order; 'offline' otherwise. Only
        touches rows where order_source is currently empty, so a re-run
        never overwrites a value a human (or a later real order) set.

        Returns {'website': int, 'offline': int}.
        """
        orders = self.sudo().search([('order_source', '=', False)])
        if not orders:
            _logger.info("order_source backfill: 0 website, 0 offline (no orders with order_source blank).")
            return {'website': 0, 'offline': 0}

        synced_order_ids = set(self.env['augmont.api.retry'].sudo().search([
            ('order_id', 'in', orders.ids),
        ]).mapped('order_id.id'))

        website = orders.filtered(
            lambda o: o.sdk_augmont_number or o.id in synced_order_ids)
        offline = orders - website

        if website:
            website.write({'order_source': 'website'})
        if offline:
            offline.write({'order_source': 'offline'})

        _logger.info(
            "order_source backfill: %d website, %d offline (of %d orders with order_source blank).",
            len(website), len(offline), len(orders))
        return {'website': len(website), 'offline': len(offline)}
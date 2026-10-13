# -*- coding: utf-8 -*-
import logging
import re
from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

LGD_DOC_NUMBER_MAX_LEN = 16
LGD_DOC_NUMBER_RE = re.compile(r'^[A-Z0-9/-]+$')

LGD_CHANNEL_BY_ORDER_SOURCE = {
    'website': 'online',
    'offline': 'offline',
}


class LgdDocSeries(models.Model):
    _name = 'lgd.doc.series'
    _description = 'LGD Document Numbering Series'
    _order = 'doc_type, channel'

    doc_type = fields.Selection(
        [('memo_out',           'Memo Out'),
         ('memo_in',            'Memo In'),
         ('order_confirmation', 'Order Confirmation'),
         ('tax_invoice',        'Tax Invoice')],
        string="Document Type", required=True, index=True)
    channel = fields.Selection(
        [('online', 'Online'), ('offline', 'Offline')],
        string="Channel", required=True, index=True,
        help="Decided from the order's source and fixed at Order "
             "Confirmation / Memo Out. Every document linked to that order "
             "follows the same channel.")
    active = fields.Boolean(default=True)

    # ── The sheet's input columns ────────────────────────────────
    prefix = fields.Char(
        string="Prefix", required=True,
        help="Everything before the financial year, e.g. ON-MO. Letters, "
             "digits, '-' and '/' only.")
    fy_code = fields.Char(
        string="FY", required=True, default=lambda self: self._lgd_fy_code(),
        help="Financial year code: 2627 means 1-Apr-2026 to 31-Mar-2027. "
             "Change it on 1 April - the 'Roll to new financial year' "
             "button does it for every series at once and resets the "
             "running numbers.")
    start_no = fields.Integer(
        string="Start No", required=True, default=1,
        help="The first running number of a financial year. Only read when "
             "that year's counter is created, so editing it afterwards does "
             "not disturb numbers already issued.")
    digits = fields.Integer(
        string="Digits", required=True, default=5,
        help="Width of the running number, zero-padded. 5 gives 00001. "
             "Like Start No, it is read when a financial year's counter is "
             "created; widening it takes effect from the next financial "
             "year, so numbers already issued keep the width they were "
             "issued at.")

    # ── The sheet's derived columns ──────────────────────────────
    first_number = fields.Char(
        string="First Number", compute='_compute_lgd_preview')
    last_number = fields.Char(
        string="Last Number (capacity)", compute='_compute_lgd_preview')
    length = fields.Integer(
        string="Length", compute='_compute_lgd_preview')
    gst_check = fields.Char(
        string="GST Check", compute='_compute_lgd_preview',
        help="OK when the number fits GST Rule 46: at most 16 characters, "
             "and only A-Z, 0-9, '-' and '/'.")

    # ── Live state ───────────────────────────────────────────────
    next_number = fields.Char(
        string="Next Number", compute='_compute_lgd_next_number',
        help="What this series will issue next, for the financial year in "
             "the FY column. Reads the counter; it does not consume it.")

    _sql_constraints = [
        ('lgd_doc_series_unique',
         'unique(doc_type, channel)',
         "There is already a series for that document type and channel."),
    ]

    # ── Financial year ───────────────────────────────────────────
    @api.model
    def _lgd_fy_code(self, date=None):
        """'2627' for any date in 1-Apr-2026 .. 31-Mar-2027."""
        date = date or fields.Date.context_today(self)
        start = date.year if date.month >= 4 else date.year - 1
        return '%02d%02d' % (start % 100, (start + 1) % 100)

    # ── Format ───────────────────────────────────────────────────
    def _lgd_format(self, running, fy_code=None):
        """Render one number. The single place the format is assembled."""
        self.ensure_one()
        return '%s/%s/%0*d' % (self.prefix or '', fy_code or self.fy_code or '',
                               max(self.digits or 1, 1), running)

    @api.depends('prefix', 'fy_code', 'start_no', 'digits')
    def _compute_lgd_preview(self):
        for series in self:
            digits = max(series.digits or 0, 1)
            first = series._lgd_format(series.start_no or 0)
            last = series._lgd_format(10 ** digits - 1)
            series.first_number = first
            series.last_number = last
            series.length = len(last)
            if len(last) > LGD_DOC_NUMBER_MAX_LEN:
                series.gst_check = _("Too long: %(len)s of %(max)s characters") % {
                    'len': len(last), 'max': LGD_DOC_NUMBER_MAX_LEN}
            elif not LGD_DOC_NUMBER_RE.match(last):
                series.gst_check = _("Invalid characters: use A-Z, 0-9, - and / only")
            else:
                series.gst_check = _("OK")

    @api.depends('prefix', 'fy_code', 'start_no', 'digits')
    def _compute_lgd_next_number(self):
        for series in self:
            seq = series._lgd_sequence(create=False)
            running = seq.number_next_actual if seq else (series.start_no or 1)
            series.next_number = series._lgd_format(running)

    # ── Validation ───────────────────────────────────────────────
    @api.constrains('prefix', 'fy_code', 'start_no', 'digits')
    def _check_lgd_format(self):
        """Refuse a configuration that cannot produce a legal number.

        Checked against the LAST number the series can reach, not the
        first: a prefix that fits at 00001 and overflows at 99999 would
        otherwise pass here and fail mid-year, with no way back because
        numbers already issued cannot be re-cut.
        """
        for series in self:
            if series.digits < 1 or series.digits > 10:
                raise ValidationError(_("Digits must be between 1 and 10."))
            if series.start_no < 0:
                raise ValidationError(_("Start No cannot be negative."))
            if not re.match(r'^\d{4}$', series.fy_code or ''):
                raise ValidationError(_(
                    "FY must be four digits, e.g. 2627 for "
                    "1-Apr-2026 to 31-Mar-2027."))
            candidate = series._lgd_format(10 ** max(series.digits, 1) - 1)
            if len(candidate) > LGD_DOC_NUMBER_MAX_LEN:
                raise ValidationError(_(
                    "%(doc)s / %(chan)s would reach %(num)s, which is "
                    "%(len)s characters. GST Rule 46 allows at most "
                    "%(max)s. Shorten the prefix or reduce the digits."
                ) % {'doc': dict(self._fields['doc_type'].selection)[series.doc_type],
                     'chan': series.channel, 'num': candidate,
                     'len': len(candidate), 'max': LGD_DOC_NUMBER_MAX_LEN})
            if not LGD_DOC_NUMBER_RE.match(candidate):
                raise ValidationError(_(
                    "%(num)s contains characters GST Rule 46 does not "
                    "allow. Use A-Z, 0-9, '-' and '/' only."
                ) % {'num': candidate})

    # ── The counter behind one (series, FY) ──────────────────────
    def _lgd_sequence(self, fy_code=None, create=True):
        """The ir.sequence for this series in one financial year."""
        self.ensure_one()
        fy_code = fy_code or self.fy_code
        full_prefix = '%s/%s/' % (self.prefix, fy_code)
        code = 'lgd.doc.%s.%s' % (self.doc_type, self.channel)
        Sequence = self.env['ir.sequence'].sudo()

        seqs = Sequence.search([('prefix', '=', full_prefix),
                                ('code', '=like', 'lgd.doc.%')])
        if len(seqs) > 1:

            _logger.error(
                "LGD numbering: %d counters share prefix %s (ids %s). "
                "Using the highest; run _lgd_repair_numbering().",
                len(seqs), full_prefix, seqs.ids)
            seqs = seqs.sorted(lambda sq: sq.number_next, reverse=True)
        seq = seqs[:1]
        if not seq and create:
            seq = Sequence.create({
                'name': 'LGD %s %s' % (self.prefix, fy_code),
                'code': code,
                'prefix': full_prefix,
                'padding': max(self.digits, 1),
                'number_next': max(self.start_no, 1),
                'number_increment': 1,
                'implementation': 'no_gap',
                'company_id': False,
            })
        return seq

    # ── Rolling the financial year ───────────────────────────────
    def action_lgd_roll_financial_year(self):
        """Move every series to the current financial year.

        Deliberately a button and not an automatic rollover. The running
        number restarting is a thing the business must be able to point
        at and date; silently switching at midnight on 1 April would
        leave nobody able to say when it happened. Issuing continues in
        whatever FY the column says until someone presses this.
        """
        target = self._lgd_fy_code()
        series = self or self.search([])
        stale = series.filtered(lambda s: s.fy_code != target)
        if not stale:
            raise UserError(_("Every series is already on %s.") % target)
        stale.write({'fy_code': target})
        _logger.info("LGD numbering: rolled %d series to FY %s",
                     len(stale), target)
        return True

    # ── Resolving a series ───────────────────────────────────────
    @api.model
    def _lgd_channel_for(self, order):
        """'online' / 'offline' for an order.

        Always resolved from the ORDER - never from the document being
        numbered, and never from who is logged in. An order with no
        order_source yet reads as offline, which is what
        _lgd_backfill_order_source decides for a blank one too, so a
        number issued before that backfill runs cannot contradict it.
        """
        source = order.order_source if order else False
        return LGD_CHANNEL_BY_ORDER_SOURCE.get(source, 'offline')

    @api.model
    def _lgd_series_for(self, doc_type, order):
        channel = self._lgd_channel_for(order)
        series = self.sudo().search(
            [('doc_type', '=', doc_type), ('channel', '=', channel)], limit=1)
        if not series:
            raise UserError(_(
                "No numbering series is configured for %(doc)s / %(chan)s. "
                "Add it under Dispatch > Configuration > Document Numbering."
            ) % {'doc': doc_type, 'chan': channel})
        return series

    @api.model
    def _lgd_next_number(self, doc_type, order, date=None):
        """Hand out the next number for this document type and order.

        Callers MUST write the result somewhere durable in the same
        transaction. A number drawn and then rolled back is a gap, and
        Rule 46 does not allow gaps - which is also why nothing should
        draw one speculatively just to show a user what is coming.
        """
        series = self._lgd_series_for(doc_type, order)
        number = series._lgd_sequence().next_by_id()
        if len(number) > LGD_DOC_NUMBER_MAX_LEN:
            # The running number has outgrown its digits - the 100,000th
            # document of the year on a 5-digit series. Refuse rather
            # than issue something GST will reject.
            raise UserError(_(
                "Document number %(number)s is %(len)s characters; GST "
                "allows at most %(max)s. The %(doc)s / %(chan)s series has "
                "run past its capacity and needs a wider format."
            ) % {'number': number, 'len': len(number),
                 'max': LGD_DOC_NUMBER_MAX_LEN,
                 'doc': doc_type, 'chan': series.channel})
        _logger.info("LGD numbering: %s/%s -> %s",
                     doc_type, series.channel, number)
        return number

    # ── One-time repair ──────────────────────────────────────────
    #: Where each document type keeps its issued number. account.move
    #: is deliberately absent: a posted invoice's name is an accounting
    #: record and is never rewritten here - duplicates there are
    #: reported and left for a human.
    _LGD_NUMBER_FIELDS = {
        'order_confirmation': ('sale.order', 'lgd_oc_number', lambda r: r),
        'memo_out': ('lgd.dispatch', 'lgd_memo_out_number',
                     lambda r: r.order_id),
        'memo_in': ('lgd.dispatch', 'lgd_memo_in_number',
                    lambda r: r.order_id),
    }

    @api.model
    def _lgd_repair_numbering(self):
        """Merge duplicate counters, renumber duplicate documents.

        Idempotent, and a no-op on a healthy database, so it is safe to
        leave wired into every upgrade.

        Three passes, in this order - each depends on the one before:

        1. Collapse ir.sequence rows that share a prefix. Keeps the
           oldest row and advances it to the furthest of the group, so
           nothing the group already issued can be handed out twice.
        2. Renumber documents that ended up sharing a number. The
           EARLIEST record keeps the number it was issued; later ones
           are reissued. Rule 46 forbids reuse, not renumbering before
           go-live, and a duplicate cannot be left standing.
        3. Push every counter past the highest number actually issued
           under its prefix, so a counter that was reset or restored
           from an older backup cannot walk back over live numbers.
        """
        Sequence = self.env['ir.sequence'].sudo()

        # ── 1. duplicate counters ────────────────────────────────
        by_prefix = defaultdict(lambda: Sequence.browse())
        for seq in Sequence.search([('code', '=like', 'lgd.doc.%')]):
            by_prefix[seq.prefix] |= seq
        merged = 0
        for prefix, seqs in by_prefix.items():
            if len(seqs) < 2:
                continue
            keeper = seqs.sorted('id')[0]
            keeper.number_next = max(seqs.mapped('number_next'))
            _logger.warning(
                "LGD numbering repair: prefix %s had %d counters %s; "
                "kept %s at %d.", prefix, len(seqs), seqs.ids,
                keeper.id, keeper.number_next)
            (seqs - keeper).unlink()
            merged += len(seqs) - 1

        # ── 2. duplicate document numbers ────────────────────────
        reissued = 0
        for doc_type, (model, field, order_of) in self._LGD_NUMBER_FIELDS.items():
            if model not in self.env:
                continue
            records = self.env[model].sudo().search(
                [(field, '!=', False)], order='id')
            seen = {}
            for record in records:
                number = record[field]
                if number not in seen:
                    seen[number] = record
                    continue
                # A later record carrying a number an earlier one
                # already has. The earlier one keeps it.
                fresh = self._lgd_next_number(doc_type, order_of(record))
                _logger.warning(
                    "LGD numbering repair: %s %s had %s, already held by "
                    "%s; reissued as %s.", model, record.id, number,
                    seen[number].id, fresh)
                record[field] = fresh
                seen[fresh] = record
                reissued += 1

        # ── 3. counters behind what is already issued ────────────
        advanced = 0
        for doc_type, (model, field, _order_of) in self._LGD_NUMBER_FIELDS.items():
            if model not in self.env:
                continue
            highest = defaultdict(int)
            for number in self.env[model].sudo().search(
                    [(field, '!=', False)]).mapped(field):
                head, _sep, tail = number.rpartition('/')
                if head and tail.isdigit():
                    highest['%s/' % head] = max(highest['%s/' % head],
                                                int(tail))
            for prefix, top in highest.items():
                seq = Sequence.search([('prefix', '=', prefix),
                                       ('code', '=like', 'lgd.doc.%')], limit=1)
                if seq and seq.number_next <= top:
                    _logger.warning(
                        "LGD numbering repair: counter %s was at %d but %d "
                        "is already issued; advanced to %d.",
                        prefix, seq.number_next, top, top + 1)
                    seq.number_next = top + 1
                    advanced += 1

        _logger.info(
            "LGD numbering repair: %d counter(s) merged, %d document(s) "
            "reissued, %d counter(s) advanced.", merged, reissued, advanced)
        return {'merged': merged, 'reissued': reissued, 'advanced': advanced}

# -*- coding: utf-8 -*-
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class AccountFiscalPosition(models.Model):
    _inherit = 'account.fiscal.position'

    # The Indian localisation ships Inter State mappings for 1%, 2%, 5%, 12%,
    # 18% and 28% - but not for 1.5%, the diamond rate. Without the row,
    # map_tax() returns the intra-state "1.5% GST" group unchanged, so an
    # out-of-Maharashtra sale books CGST + SGST. That is what printed CGST and
    # SGST on an inter-state Tax Invoice while the Order Slip, which reads
    # gst_treatment rather than the booked taxes, said IGST.
    _LGD_INTER_STATE_POSITION = 'Inter State'
    _LGD_GST_RATE_MAPPINGS = (('1.5% GST', '1.5% IGST'),)

    @staticmethod
    def _lgd_effective_rate(tax):
        """A tax's own percentage, or the sum of its children for a group.

        "1.5% GST" is a group whose own amount reads 0.0 - the rate lives on
        the 0.75 CGST and 0.75 SGST beneath it.
        """
        if tax.amount_type == 'group':
            return sum(child.amount for child in tax.children_tax_ids)
        return tax.amount

    @api.model
    def _lgd_ensure_gst_tax_mappings(self):
        """Create the missing 1.5% Inter State rows, once, per company.

        Idempotent: a position that already maps the source tax is left alone,
        and a company whose two taxes do not carry the same effective rate is
        skipped rather than given a mapping that would change what it charges.
        """
        Tax = self.env['account.tax'].sudo()
        Mapping = self.env['account.fiscal.position.tax'].sudo()
        created = 0

        for position in self.sudo().search(
                [('name', '=', self._LGD_INTER_STATE_POSITION)]):
            company = position.company_id
            for src_name, dest_name in self._LGD_GST_RATE_MAPPINGS:
                for tax_use in ('sale', 'purchase'):
                    domain = [('company_id', '=', company.id),
                              ('type_tax_use', '=', tax_use)]
                    src = Tax.search(domain + [('name', '=', src_name)], limit=1)
                    dest = Tax.search(domain + [('name', '=', dest_name)], limit=1)
                    if not src or not dest:
                        continue
                    if Mapping.search_count([('position_id', '=', position.id),
                                             ('tax_src_id', '=', src.id)]):
                        continue

                    src_rate = self._lgd_effective_rate(src)
                    dest_rate = self._lgd_effective_rate(dest)
                    if abs(src_rate - dest_rate) > 0.001:
                        _logger.warning(
                            "Fiscal position %s (company %s): not mapping %s "
                            "-> %s, they carry %.3f%% and %.3f%%. Mapping them "
                            "would change what the company charges.",
                            position.name, company.display_name, src_name,
                            dest_name, src_rate, dest_rate)
                        continue

                    Mapping.create({'position_id': position.id,
                                    'tax_src_id': src.id,
                                    'tax_dest_id': dest.id})
                    created += 1
                    _logger.info(
                        "Fiscal position %s (company %s): mapped %s -> %s.",
                        position.name, company.display_name, src_name, dest_name)

        if created:
            _logger.info("Created %s Inter State GST mapping(s).", created)
        return created

# -*- coding: utf-8 -*-
import logging
from odoo import models
_logger = logging.getLogger(__name__)

class IrActionsReport(models.Model):
    _inherit = 'ir.actions.report'

    _LGD_DISPATCH_PREPARE = {
        'contact_stage_bar.report_lgd_memo_slip':
            '_lgd_prepare_memo_slip',
        'contact_stage_bar.report_lgd_memo_return_slip':
            '_lgd_prepare_memo_return_slip',
    }

    def _lgd_prepare_dispatch_report(self, report_ref, res_ids):
        """Run the challan's own guard before it is rendered."""
        if not res_ids:
            return
        report = self._get_report(report_ref)
        method = self._LGD_DISPATCH_PREPARE.get(report.report_name)
        if not method:
            return
        parcels = self.env['lgd.dispatch'].browse(res_ids).exists()
        if parcels:
            getattr(parcels, method)()

    def _render_qweb_pdf(self, report_ref, res_ids=None, data=None):
        if isinstance(res_ids, int):
            res_ids = [res_ids]
        self._lgd_prepare_dispatch_report(report_ref, res_ids)
        return super()._render_qweb_pdf(report_ref, res_ids=res_ids, data=data)

    def _render_qweb_html(self, report_ref, docids, data=None):

        if isinstance(docids, int):
            docids = [docids]
        self._lgd_prepare_dispatch_report(report_ref, docids)
        return super()._render_qweb_html(report_ref, docids, data=data)

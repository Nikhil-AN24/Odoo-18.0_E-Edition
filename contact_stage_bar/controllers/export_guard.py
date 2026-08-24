# -*- coding: utf-8 -*-
"""Enforce base.group_allow_export on the export download endpoints.

Odoo hides the Export entry in the UI for users without
base.group_allow_export (list_controller.js checks the group before adding the
item, and export_all.js does the same for the cog). The endpoints behind it do
not: /web/export/csv and /web/export/xlsx are declared auth='user' with no
group check, and neither is ExportFormat.base(). Only /json/<model> checks the
group.

That makes the group a UI preference rather than a restriction: a revoked user
who has the URL, a bookmark, or a copied fetch from devtools can still pull the
full recordset. These overrides close that gap so revoking the group actually
prevents the download.
"""

import logging

from odoo import _
from odoo.exceptions import AccessError
from odoo.http import request
from odoo.addons.web.controllers.export import CSVExport, ExcelExport

_logger = logging.getLogger(__name__)


def _assert_export_allowed(endpoint):
    user = request.env.user
    if user.has_group('base.group_allow_export'):
        return
    _logger.warning(
        "Blocked export attempt on %s by %s (uid %s): no base.group_allow_export",
        endpoint, user.login, user.id)
    raise AccessError(_(
        "Exporting records is restricted to administrators. "
        "Contact your system administrator if you need this data."))


class LgdCSVExport(CSVExport):

    def web_export_csv(self, data):
        _assert_export_allowed('/web/export/csv')
        return super().web_export_csv(data)


class LgdExcelExport(ExcelExport):

    def web_export_xlsx(self, data):
        _assert_export_allowed('/web/export/xlsx')
        return super().web_export_xlsx(data)

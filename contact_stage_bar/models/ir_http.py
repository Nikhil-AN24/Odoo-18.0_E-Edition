# -*- coding: utf-8 -*-
from odoo import models


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    def session_info(self):
        """Withhold "Insert in spreadsheet" from non-administrators.

        The entry is not an action binding, so groups_id cannot reach it. The
        client shows it whenever session.can_insert_in_spreadsheet is true, and
        three enterprise modules OR that flag together from
        documents.group_documents_user, spreadsheet_dashboard.group_dashboard_manager
        and sales_team.group_sale_manager. Revoking those would break the roles
        that legitimately need them, so the flag is cleared here instead.

        Inserting a list into a spreadsheet copies the whole recordset into a
        file the user can then download, which is the same exposure the export
        restriction closes; the two belong together.
        """
        res = super().session_info()
        if not self.env.user.has_group('base.group_system'):
            res['can_insert_in_spreadsheet'] = False
        return res

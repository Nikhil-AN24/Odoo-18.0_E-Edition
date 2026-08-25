import io
import logging
import xlsxwriter

from odoo import http
from odoo.http import request
_logger = logging.getLogger(__name__)

class OrderLinesExportController(http.Controller):
    # LGD Procurement works the vendor side of the order, so it keeps the vendor
    # SKU / pricing / company columns and never sees the sale price.
    PROCUREMENT_COLUMNS = [
        ("LGD SKU", "lgd_stock_number"),
        ("Vendor SKU", "vendor_sku"),
        ("Vendor Final Price", "final_price"),
        ("Certificate Number", "certificate"),
        ("Vendor Company", "vendor_id"),
        ("Shapes", "shapes"),
        ("Carat", "carat_weight"),
        ("Color", "color"),
        ("Clarity", "clarity"),
        ("Cut", "cut"),
        ("Symmetry", "symmetry"),
        ("Polish", "polish"),
        ("Growth", None),          # not tracked in Odoo yet
        ("Lab", "labs"),
        ("Measurement", "measurements"),
        ("Table%", None),          # not tracked in Odoo yet
        ("Depth%", None),          # not tracked in Odoo yet
        ("Ratio", None),           # not tracked in Odoo yet
        ("Vendor Per Carat Price", "vendor_per_carat_price"),
    ]

    COLUMNS = [
        ("LGD SKU", "lgd_stock_number"),
        ("Certificate Number", "certificate"),
        ("Shapes", "shapes"),
        ("Carat", "carat_weight"),
        ("Color", "color"),
        ("Clarity", "clarity"),
        ("Cut", "cut"),
        ("Symmetry", "symmetry"),
        ("Polish", "polish"),
        ("Growth", None),          # not tracked in Odoo yet
        ("Lab", "labs"),
        ("Measurement", "measurements"),
        ("Table%", None),          # not tracked in Odoo yet
        ("Depth%", None),          # not tracked in Odoo yet
        ("Ratio", None),           # not tracked in Odoo yet
        ("Sale Price", "final_price_margin"),
        ("Sale Price per carat", "sale_price_per_carat"),
    ]

    def _get_columns(self):
        """Vendor-side columns for LGD Procurement, sale-side for everyone else."""
        if request.env.user.has_group('contact_stage_bar.group_lgd_procurement'):
            return self.PROCUREMENT_COLUMNS
        return self.COLUMNS

    @http.route('/sale_order/<int:order_id>/export_order_lines_csv', type='http', auth='user', csrf=False)
    def export_order_lines_csv(self, order_id, **kwargs):
        order = request.env['sale.order'].browse(order_id).exists()
        if not order:
            return request.not_found()

        try:
            order.check_access('read')
        except Exception:
            return request.not_found()

        columns = self._get_columns()

        buffer = io.BytesIO()
        workbook = xlsxwriter.Workbook(buffer, {'in_memory': True})
        sheet = workbook.add_worksheet('Order Lines')
        header_format = workbook.add_format({'bold': True})

        for col, (label, _field) in enumerate(columns):
            sheet.write_string(0, col, label, header_format)
            sheet.set_column(col, col, max(len(label) + 2, 12))
        sheet.freeze_panes(1, 0)

        row = 1
        for line in order.order_line:
            # Skip section/note lines which have no product.
            if line.display_type:
                continue
            for col, (_label, field_name) in enumerate(columns):
                if not field_name:
                    continue
                value = line[field_name]
                if field_name == 'vendor_id':
                    value = value.name or ''
                if value is False or value is None or value == '':
                    continue
                if isinstance(value, (int, float)):
                    sheet.write_number(row, col, value)
                else:
                    sheet.write_string(row, col, str(value))
            row += 1

        workbook.close()
        xlsx_data = buffer.getvalue()
        buffer.close()

        filename = "%s_order_lines.xlsx" % (order.name or 'order').replace('/', '_')
        headers = [
            ('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
            ('Content-Disposition', 'attachment; filename="%s"' % filename),
        ]
        return request.make_response(xlsx_data, headers=headers)

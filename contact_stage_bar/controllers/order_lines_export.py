import csv
import codecs
import io
import logging

from odoo import http
from odoo.http import request
_logger = logging.getLogger(__name__)

class OrderLinesExportController(http.Controller):
    COLUMNS = [
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

    @http.route('/sale_order/<int:order_id>/export_order_lines_csv', type='http', auth='user', csrf=False)
    def export_order_lines_csv(self, order_id, **kwargs):
        order = request.env['sale.order'].browse(order_id).exists()
        if not order:
            return request.not_found()

        try:
            order.check_access('read')
        except Exception:
            return request.not_found()

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([label for label, _field in self.COLUMNS])

        for line in order.order_line:
            # Skip section/note lines which have no product.
            if line.display_type:
                continue
            row = []
            for _label, field_name in self.COLUMNS:
                if not field_name:
                    row.append('')
                    continue
                value = line[field_name]
                if field_name == 'vendor_id':
                    value = value.name or ''
                row.append(value if value not in (False, None) else '')
            writer.writerow(row)

        csv_data = buffer.getvalue()
        buffer.close()

        filename = "%s_order_lines.csv" % (order.name or 'order').replace('/', '_')
        headers = [
            ('Content-Type', 'text/csv; charset=utf-8'),
            ('Content-Disposition', 'attachment; filename="%s"' % filename),
        ]
        # Prefix with a UTF-8 BOM. Excel & LibreOffice Calc both use the BOM to reliably auto-detect "comma-separated UTF-8" 
        csv_bytes = codecs.BOM_UTF8 + csv_data.encode('utf-8')
        return request.make_response(csv_bytes, headers=headers)
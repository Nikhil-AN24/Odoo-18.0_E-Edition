/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { ListRenderer } from "@web/views/list/list_renderer";
import { user } from "@web/core/user";

/**
 * Expected Stones — stamp the acting user into "Collected By" as soon as they
 * click a stone's row.
 *
 * This has to live in the browser. Odoo raises no server-side event for simply
 * entering a row: an onchange only runs once a value actually changes, so the
 * click itself is invisible to Python. Patching the list renderer's cell-click
 * handler is the only place that knows a row was touched.
 *
 * Deliberately narrow:
 *  - only purchase.order.line lists,
 *  - only when the view actually carries lgd_collector_id (so just the
 *    Logistics screens, never a standard Purchase list),
 *  - only when the cell is still empty, so a name already recorded is never
 *    overwritten.
 *
 * The field stays an ordinary editable many2one, so the stone remains
 * transferable: pick anyone in the cell to hand the collection over.
 */
patch(ListRenderer.prototype, {
    async onCellClicked(record, column, ev) {
        await super.onCellClicked(record, column, ev);

        if (record.resModel !== "purchase.order.line") {
            return;
        }
        if (!("lgd_collector_id" in record.data) || record.data.lgd_collector_id) {
            return;
        }
        // A many2one takes a [id, display_name] pair; the model completes the
        // name itself if it is missing.
        await record.update({ lgd_collector_id: [user.userId, user.name] });
    },
});

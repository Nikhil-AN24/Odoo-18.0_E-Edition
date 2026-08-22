/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { CogMenu } from "@web/search/cog_menu/cog_menu";

/**
 * Hide the control-panel cog on actions whose context sets hide_cog_menu.
 *
 * The cog cannot be switched off from the action alone. web.CogMenu renders
 * whenever `hasItems` is true, and that counts entries contributed by the
 * cogMenu registry -- Import records, Export All, Insert list in spreadsheet --
 * each of which decides for itself from the *view arch* (create / import /
 * export_xlsx attributes), not from the action context. Suppressing them that
 * way would mean pinning a dedicated list view to every action and would still
 * leave third-party registry items behind, so the flag is honoured here.
 *
 * Deliberately opt-in per action rather than per app: the cog is the only route
 * to "Import records", which is how suppliers and accounts get loaded from CSV.
 * Switching it off app-wide would take those workflows away with it.
 */
patch(CogMenu.prototype, {
    get hasItems() {
        if (this.env.searchModel?.context?.hide_cog_menu) {
            return false;
        }
        return super.hasItems;
    },
});

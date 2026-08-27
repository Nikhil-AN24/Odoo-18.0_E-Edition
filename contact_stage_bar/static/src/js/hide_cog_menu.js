/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { CogMenu } from "@web/search/cog_menu/cog_menu";
import { user } from "@web/core/user";

patch(CogMenu.prototype, {
    get hasItems() {
        if (this.env.searchModel?.context?.hide_cog_menu) {
            if (!user.isAdmin) {
                return false;
            }
        }
        return super.hasItems;
    },
});

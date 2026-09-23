import { patch }          from "@web/core/utils/patch";
import { SelectionField } from "@web/views/fields/selection/selection_field";

const TARGET_FIELD = "availability_status";
const MANUAL_ALLOWED = ["diamond_booked", "confirmed", "not_available", "cancelled"];

patch(SelectionField.prototype, {

    get options() {
        const allOptions = super.options;
        if (this.props.name !== TARGET_FIELD) {
            return allOptions;
        }
        // Keep the record's CURRENT value in the list too, so a line already at
        // an auto-set status (e.g. Dispatched) still renders correctly instead
        // of showing blank when the cell is opened.
        const current = this.props.record?.data?.[TARGET_FIELD];
        return allOptions.filter(
            ([value]) => MANUAL_ALLOWED.includes(value) || value === current
        );
    },

});

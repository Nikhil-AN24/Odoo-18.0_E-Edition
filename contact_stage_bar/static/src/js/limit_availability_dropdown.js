import { patch }          from "@web/core/utils/patch";
import { SelectionField } from "@web/views/fields/selection/selection_field";

const TARGET_FIELD = "availability_status";

// What a user may pick by hand while the quotation is still being built.
const MANUAL_ALLOWED = ["diamond_booked", "confirmed", "not_available", "cancelled"];

// A stone QC rejected has exactly one manual destination: Cancelled. Anything
// else (back to Confirmed, on to Payment Pending) would re-enter a flow the
// stone has already failed out of, so those options are not offered at all.
// The matching row-level gate lives in sale.order.line.can_edit_availability.
const QC_FAIL_ALLOWED = ["qc_fail", "cancelled"];

patch(SelectionField.prototype, {

    get options() {
        const allOptions = super.options;
        if (this.props.name !== TARGET_FIELD) {
            return allOptions;
        }
        const current = this.props.record?.data?.[TARGET_FIELD];

        if (current === "qc_fail") {
            return allOptions.filter(([value]) => QC_FAIL_ALLOWED.includes(value));
        }

        // Keep the record's CURRENT value in the list too, so a line already at
        // an auto-set status (e.g. Dispatched) still renders correctly instead
        // of showing blank when the cell is opened.
        return allOptions.filter(
            ([value]) => MANUAL_ALLOWED.includes(value) || value === current
        );
    },

});

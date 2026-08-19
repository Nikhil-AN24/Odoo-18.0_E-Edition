
import { patch }          from "@web/core/utils/patch";
import { SelectionField } from "@web/views/fields/selection/selection_field";

const TARGET_FIELD = "availability_status";

const ALLOWED_NEXT = {
    "diamond_booked": new Set(["confirmed", "not_available"]),
    "confirmed":      new Set(["confirmed", "cancelled"]),
    "not_available":  new Set(["cancelled"]),
};

const INFLIGHT = new Set([
    "in_qc_process", "qc_fail", "payment_pending", "payment_completed",
    "dispatched", "return_of_order", "re_dispatched", "delivered", "order_completed",
]);


patch(SelectionField.prototype, {

    get options() {
        const allOptions = super.options;
        if (this.props.name !== TARGET_FIELD) return allOptions;

        const effectiveValue = this.props.record?.data?.[TARGET_FIELD];

        // Defined transition set — always include effectiveValue so no blank.
        if (effectiveValue && ALLOWED_NEXT[effectiveValue]) {
            const allowed = ALLOWED_NEXT[effectiveValue];
            return allOptions.filter(
                ([value]) => value === effectiveValue || allowed.has(value)
            );
        }

        // In-flight status: show only the current value (read-only guard).
        if (effectiveValue && INFLIGHT.has(effectiveValue)) {
            return allOptions.filter(([value]) => value === effectiveValue);
        }

        // Fallback: show the 4 UI-editable options.
        const MANUAL_ALLOWED = new Set([
            "diamond_booked", "confirmed", "not_available",
        ]);
        return allOptions.filter(([value]) => MANUAL_ALLOWED.has(value));
    },


    onChange(ev) {
        // Let Odoo's standard handler run — this properly marks the field dirty
        super.onChange(ev);
    },

});

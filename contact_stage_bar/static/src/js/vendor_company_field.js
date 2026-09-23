/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Many2OneField, many2OneField } from "@web/views/fields/many2one/many2one_field";
import { Component } from "@odoo/owl";

// "Vendor Company" field widget: a single column that behaves as the normal
// editable vendor picker on active lines, but shows plain read-only "N/A" text
// when the line's Availability is Cancelled or Not available (no vendor to
// source from). The stored vendor is left intact, so restoring the status
// brings it back.
const NA_STATUSES = ["cancelled", "not_available"];

export class VendorCompanyField extends Component {
    static template = "contact_stage_bar.VendorCompanyField";
    static components = { Many2OneField };
    static props = { ...Many2OneField.props };

    get isNA() {
        const status = this.props.record.data.availability_status;
        return NA_STATUSES.includes(status);
    }
}

export const vendorCompanyField = {
    ...many2OneField,
    component: VendorCompanyField,
};

registry.category("fields").add("vendor_company", vendorCompanyField);

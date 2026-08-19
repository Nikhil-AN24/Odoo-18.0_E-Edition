
import { PhoneField } from "@web/views/fields/phone/phone_field";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { rpc } from "@web/core/network/rpc";

patch(PhoneField.prototype, {
    setup() {
        super.setup();
        this.agent = useService("voip_agent_oca");  // For OCA VOIP
    },

    async onPhoneClick(ev) {
    ev.preventDefault();
    ev.stopPropagation();

    let number = this.props.record.data[this.props.name];
    if (!number) {
        console.warn("Phone number is empty.");
        return;
    }

    // Get country from partner (fallback check with +91 prefix)
    const country = this.props.partner?.country?.toLowerCase();
    const isIndia = country === "india" || country === "in" || number.startsWith("+91");
      
    if (isIndia) {
        // 👉 MyOperator for India
        window.open(
            "https://in.app.myoperator.com/webcall",
            "myoperatorPopup",
            "width=500,height=500,resizable=yes,scrollbars=yes"
        );

        if (this.agent && this.agent.agent) {
            this.agent.call({ number });
        } else {
            const result = await rpc("/myoperator/call", {
                customer_number: number,
                partner: this.props.partner
            });
            console.log("MyOperator result:", result);
        }
    } else {
        number = number.replace(/[\s-]/g, ""); 
        number = number.replace(/^\+/, "");
        // 👉 Pulse for International (USA & others)
        const response = await rpc("/pulse/call", {
            customer_number: number,
            partner: this.props.partner
        });

        // if (response?.status === "success" && response.iframe_url) {
        //     window.open(
        //         response.iframe_url,
        //         "pulsePopup",
        //         "width=500,height=500,resizable=yes,scrollbars=yes"
        //     );
        //     console.log("Pulse call triggered and iframe opened:", response.iframe_url);
        // } else {
        //     console.error("Pulse call failed:", response);
        // }
    }
}

});


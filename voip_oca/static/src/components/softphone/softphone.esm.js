/*
    Copyright 2025 Dixmit
    License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
*/

import { Component, onWillStart, useRef, useState } from "@odoo/owl";
import { Call } from "@voip_oca/components/call/call.esm";
import { Numpad } from "@voip_oca/components/numpad/numpad.esm";
import { Partner } from "@voip_oca/components/partner/partner.esm";
import { registry } from "@web/core/registry";
import { useDebounced } from "@web/core/utils/timing";
import { useService } from "@web/core/utils/hooks";
import { rpc } from "@web/core/network/rpc";  
import { Dialog } from "@web/core/dialog/dialog";
import { markup } from "@odoo/owl";
// import { AlertDialog } from "@web/core/confirmation_dialog/alert_dialog";
import { ConfirmationDialog, AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";






export class VoipOCASoftphone extends Component {
    setup() {
        this.voip = useState(useService("voip_oca"));
        this.agent = useService("voip_agent_oca");
        this.searchInput = useRef("searchInput");
        this.dialogService = useService("dialog");
        this.dialog = useService("dialog");
        this.onSearchInput = useDebounced(() => {
            this._searchInput(this.searchInput.el.value);
        }, 300);
        onWillStart(() => this._searchInput());
    }

    get showInput() {
        return Boolean(
            registry.category("voip_elements").get(this.voip.selectedTab).input
        );
    }

    get tabElements() {
        return registry
            .category("voip_elements")
            .getEntries()
            .sort((a, b) => a[1].order - b[1].order);
    }

    get childComponent() {
        const element = registry.category("voip_elements").get(this.voip.selectedTab);
        if (element) {
            return element.component;
        }
        return false;
    }

    get childComponentProps() {
        const props = {};
        const element = registry.category("voip_elements").get(this.voip.selectedTab);
        if (element.input) {
            props.records = this.voip[element.input];
        }
        return props;
    }

    async _searchInput(value) {
        const element = registry.category("voip_elements").get(this.voip.selectedTab);
        if (element && element.search) {
            await element.search(this.voip, value);
        }
    }

    /** Action */
    onClickBar() {
        this.voip.handleFold();
    }

    
    // const callWindow = window.open("https://in.app.myoperator.com/webcall", "_blank");
    
//     async onCall() {
    // const phoneNumber = this.voip.numpad.value;
//     console.log(phoneNumber, "phone");
//     console.log("onCall clicked!");
//     window.open(
//     "https://in.app.myoperator.com/webcall",
//     "myoperatorPopup",
//     "width=500,height=500,resizable=yes,scrollbars=yes"
// );
//     await rpc("/myoperator/call", {
//         customer_number: phoneNumber,
//     });

    // async onCall() {
    // console.log("testing1234");

    // // const country = this.props.partner?.country?.toLowerCase(); // assuming partner.country has the country name
    // // const isIndia = country === "india" || country === "in";
    
    // await rpc("/pulse/call", {
    //         customer_number: this.phoneNumber,
    //         partner: this.props.partner
    //     });

    //     console.log("Pulse call triggered for non-India");
    // }

    async onCall() {
    let phoneNumber = this.voip.numpad.value;
    console.log(phoneNumber, "phone");
    console.log("onCall clicked!");

    // Get country from partner (fallback to phone check)
    const country = this.props.partner?.country?.toLowerCase();
    const isIndia = country === "india" || country === "in" || phoneNumber.startsWith("+91");

    if (isIndia) {
        // 👉 MyOperator for India
        window.open(
            "https://in.app.myoperator.com/webcall",
            "myoperatorPopup",
            "width=500,height=500,resizable=yes,scrollbars=yes"
        );

        await rpc("/myoperator/call", {
            customer_number: phoneNumber,
            partner: this.props.partner
        });

        console.log("MyOperator call triggered for India:", phoneNumber);
    } else {
        // 👉 Pulse for International (USA & others)
        phoneNumber = number.replace(/[\s-]/g, "");  // remove spaces & hyphens
        phoneNumber = number.replace(/^\+/, "");    // remove +1 prefix (for USA)
        const response = await rpc("/pulse/call", {
            customer_number: phoneNumber,
            partner: this.props.partner
        });
    }
}










    // corrected 
//     async onCall() {
//     const phoneNumber = this.voip.numpad.value;
//     console.log(phoneNumber, "phone");
//     console.log("onCall clicked!");
//     // const phoneNumber = this.voip.numpad.value;
//     console.log("testing1234");

//     const response = await rpc("/pulse/call", {
//         customer_number: this.phoneNumber,
//         partner: this.props.partner
//     });

//     if (response?.status === "success" && response.iframe_url) {
//         window.open(
//             response.iframe_url,
//             "pulsePopup",
//             "width=500,height=500,resizable=yes,scrollbars=yes"
//         );
//         console.log("Pulse call triggered and iframe opened:", response.iframe_url);
//     } else {
//         console.error("Pulse call failed:", response);
//     }
// }

    // if (isIndia) {
    //     // Call MyOperator
    //     await rpc("/myoperator/call", {
    //         customer_number: this.phoneNumber,
    //         partner: this.props.partner
    //     });

    //     window.open(
    //         "https://in.app.myoperator.com/webcall",
    //         "myoperatorPopup",
    //         "width=500,height=500,resizable=yes,scrollbars=yes"
    //     );

    //     console.log("MyOperator call triggered for India");
    // } else {
    //     // Call Pulse
    //     await rpc("/pulse/call", {
    //         customer_number: this.phoneNumber,
    //         partner: this.props.partner
    //     });

    //     console.log("Pulse call triggered for non-India");
    // }
    // }


//     this.dialogService.add(ConfirmationDialog, {
//     title: "Test Call",
//     body: markup(`
//         <iframe
//             src="https://calldesk.pulsework360.com/Dialer/3bde4d4f7d5e9c3fe3bc5eac420a8996/clicktocallpage.php?userID=4241001&secret=Anirath%401001&authID=3bde4d4f7d5e9c3fe3bc5eac420a8996&phone=${encodeURIComponent(phoneNumber)}"
//             style="width:100%; height:450px; border:0; overflow:auto;"
//             allow="microphone; camera; autoplay; clipboard-write; encrypted-media; display-capture; picture-in-picture; web-share">
//         </iframe>
//     `),
//     confirmLabel: "Close",
//     confirm: () => {},
// });

// });
//     else {
//         // Call Pulse
//         rpc("/pulse/call", {
//             customer_number: this.phoneNumber,
//             partner: this.props.partner
//         });
// }

    // });

      

    onSelectTab(tag) {
        this.voip.selectedTab = tag;
        this._searchInput();
    }

    onClosePhone(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        this.voip.handleVoip();
    }

    onOpenNumpad() {
        this.voip.numpadTab = !this.voip.numpadTab;
    }
}

VoipOCASoftphone.components = { Call, Numpad, Partner };
VoipOCASoftphone.props = {};
VoipOCASoftphone.template = "voip_oca.VoipOCASoftphone";

registry.category("main_components").add("voip_oca.VoipOCASoftphone", {
    Component: VoipOCASoftphone,
});
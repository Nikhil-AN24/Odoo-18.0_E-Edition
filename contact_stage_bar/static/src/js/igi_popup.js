// /** @odoo-module **/

// import { registry } from '@web/core/registry';

// registry.category("actions").add("open_igi_popup_window", (env, action) => {
//     const url = action.params.url;
//     window.open(url, '_blank', 'width=800,height=600,scrollbars=yes,resizable=yes');
//     return Promise.resolve();
// });

/** @odoo-module **/

export async function fetchIGIData(certNum) {
    try {
        const response = await fetch(`https://api.igi.org/ReportDetail.php?Printno=${certNum}`, {
            method: 'GET',
            headers: {
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                'YOUR-SECRET-KEY': '123455'  
            }
        });

        const contentType = response.headers.get('Content-Type');

        if (contentType && contentType.includes("application/json")) {
            const data = await response.json();
            console.log('✅ IGI API Response:', data);
            return data;
        } else {
            console.warn('⚠️ Response is not JSON. Possibly blocked by Cloudflare');
            return null;
        }
    } catch (error) {
        console.error('❌ Error fetching IGI data:', error);
        return null;
    }
}


(function () {
    "use strict";

    const SALES_GROUP_XMLIDS = [
        "contact_stage_bar.group_lgd_sales",
        "contact_stage_bar.group_lgd_regional_sales_head",
        "contact_stage_bar.group_lgd_sales_manager",
    ];
    const PROCUREMENT_GROUP_XMLIDS = [
        "contact_stage_bar.group_lgd_procurement",
        "contact_stage_bar.group_lgd_procurement_manager",
    ];
    const ADMIN_GROUP_XMLID = "base.group_system";

    let salesReadonlyEnabled = null; 

    /** Resolve which groups the current user has via user_has_group RPC. */
    async function computeSalesReadonly() {
        try {
            const call = (groupId) =>
                fetch("/web/dataset/call_kw", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        jsonrpc: "2.0",
                        params: {
                            model: "res.users",
                            method: "has_group",
                            args: [groupId],
                            kwargs: {},
                        },
                    }),
                }).then((r) => r.json()).then((j) => Boolean(j.result));

            const [isAdmin, isProc, isProcMgr, ...salesFlags] = await Promise.all([
                call(ADMIN_GROUP_XMLID),
                call(PROCUREMENT_GROUP_XMLIDS[0]),
                call(PROCUREMENT_GROUP_XMLIDS[1]),
                ...SALES_GROUP_XMLIDS.map(call),
            ]);
            const isSales = salesFlags.some(Boolean);
            return isSales && !isAdmin && !isProc && !isProcMgr;
        } catch (e) {
            return false;
        }
    }

    /** Return the DOM node for the "General Information" tab-pane inside a
     * product form, or null if not found. */
    function findGeneralInfoPane(root) {
        // Renders notebook tab labels as anchor children of a nav-item.
        const forms = (root && root.querySelectorAll)
            ? root.querySelectorAll(".o_form_view")
            : document.querySelectorAll(".o_form_view");
        for (const form of forms) {
            const nav = form.querySelector(".o_notebook_headers, .nav-tabs");
            const content = form.querySelector(".o_notebook_content");
            if (!nav || !content) continue;
            const anchors = nav.querySelectorAll("a.nav-link, .nav-link");
            let idx = -1;
            anchors.forEach((a, i) => {
                if ((a.textContent || "").trim() === "General Information") {
                    idx = i;
                }
            });
            if (idx === -1) continue;
            const panes = content.querySelectorAll(":scope > .tab-pane");
            if (panes[idx]) return panes[idx];
        }
        return null;
    }

    /** Disable every input/select/textarea inside the tab pane and neutralise
     * pointer events on Many2one field wrappers so the picker can't open. */
    function makePaneReadonly(pane) {
        if (!pane || pane.dataset.csbSalesReadonly === "1") return;
        pane.dataset.csbSalesReadonly = "1";

        pane.querySelectorAll("input, textarea, select").forEach((el) => {
            el.readOnly = true;
            el.setAttribute("readonly", "readonly");
            if (el.tagName === "SELECT") el.disabled = true;
        });

        // Many2one, tag, boolean, and radio widgets ignore the input's readOnly
        // flag; block click/keydown at the wrapper level instead.
        pane.querySelectorAll(
            ".o_field_widget, .o_field_many2one, .o_field_many2many, .o_field_radio, .o_field_boolean, .o_field_selection_badge, .o_field_boolean_toggle"
        ).forEach((el) => {
            el.style.pointerEvents = "none";
            el.style.opacity = "0.75";
        });
    }

    function sweep(root) {
        const pane = findGeneralInfoPane(root);
        if (pane) makePaneReadonly(pane);
    }

    async function start() {
        if (!document.body) return;
        if (salesReadonlyEnabled === null) {
            salesReadonlyEnabled = await computeSalesReadonly();
        }
        if (!salesReadonlyEnabled) return;

        sweep(document);
        const observer = new MutationObserver((mutations) => {
            for (const m of mutations) {
                for (const node of m.addedNodes) {
                    if (node.nodeType === 1) sweep(node);
                }
                if (m.type === "attributes") sweep(m.target);
            }
        });
        observer.observe(document.body, {
            childList: true,
            subtree: true,
            attributes: true,
            attributeFilter: ["class"], // tab-pane toggles .active via class
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start);
    } else {
        start();
    }
})();

/**
 * Hide the standard stock "Replenish" button on product forms for all users.
 * The stock module declares that button with an action-reference `name=`
 * that resolves to a numeric action ID in the DOM. Touching it via XML
 * inheritance from another module trips Odoo's xmlid re-resolution and fails
 * to load the view. A DOM sweep side-steps the whole issue. */

(function () {
    "use strict";

    const REPLENISH_LABEL = /^\s*Replenish\s*$/;

    function hideReplenish(root) {
        const scope = root && root.querySelectorAll ? root : document;
        scope.querySelectorAll(".o_form_view button").forEach((btn) => {
            if (REPLENISH_LABEL.test(btn.textContent || "")) {
                btn.style.display = "none";
            }
        });
    }

    function start() {
        if (!document.body) {
            // Should not happen after DOMContentLoaded, but be defensive.
            return;
        }
        hideReplenish();
        const observer = new MutationObserver((mutations) => {
            for (const m of mutations) {
                for (const node of m.addedNodes) {
                    if (node.nodeType === 1) {
                        hideReplenish(node);
                    }
                }
            }
        });
        observer.observe(document.body, { childList: true, subtree: true });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start);
    } else {
        start();
    }
})();

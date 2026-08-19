(function () {

    function applyUICustomizations() {
        // Hide '+ New' button in Sale Orders list view only
        var saleNewButtons = document.querySelectorAll(
            ".o_sale_order .o_list_button_add span"
        );

        // Loop through any found buttons and hide them
        saleNewButtons.forEach(function(span) {
            if (span && span.textContent.trim() === "New") {
                // Hide the button completely
                span.closest('button').style.display = 'none';
            }
        });

        var poFormView = document.querySelector(".o_purchase_order .o_form_view");
        if (poFormView) {
            var poFormNewButtons = document.querySelectorAll(
                ".o_purchase_order .o_cp_buttons .o_form_button_create"
            );
            poFormNewButtons.forEach(function(btn) {
                if (btn) {
                    btn.style.display = 'none';
                }
            });
        }
        // HIDE THE "Upload" BUTTON in the Sales module.
        var allButtons = document.querySelectorAll(".o_control_panel button");
        allButtons.forEach(function(btn) {
            if (btn.textContent.trim() === "Upload") {
                btn.style.display = "none";
            }
        });

        // Sync the Breadcrumb/View Title exactly with the App Name
        var menuBrand = document.querySelector(".o_menu_brand");
        var appName = menuBrand ? menuBrand.textContent.trim() : "";        

        if (appName) {
            // Find the active breadcrumb item title
            var breadcrumbTitles = document.querySelectorAll(
                ".o_control_panel .breadcrumb-item.active, " +
                ".o_control_panel .breadcrumb-item.active span, " +
                ".o_last_breadcrumb_item span"
            );

            // Forcefully overwrite the native action name with the App Name
            breadcrumbTitles.forEach(function(breadcrumb) {
                if (breadcrumb && breadcrumb.textContent.trim() !== appName) {
                    breadcrumb.textContent = appName;
                }
            });

            // "Procurement" un-clickable 
            if (appName === "Procurement" && menuBrand) {
                menuBrand.style.pointerEvents = "none";

            }
        }
    }
    
    function setup() {
        var observer = new MutationObserver(applyUICustomizations);

        observer.observe(document.body, {
            childList: true,   /* detect added/removed elements */
            subtree: true,     /* watch all descendants recursively */
            characterData: true /* trigger if text changes inside a node */
        });

        applyUICustomizations();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", setup);
    } else {
        setup();
    }

}());
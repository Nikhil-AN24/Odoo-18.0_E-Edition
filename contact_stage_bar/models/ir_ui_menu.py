from odoo import models, api

class IrUiMenuHideSaleExtras(models.Model):
    _inherit = 'ir.ui.menu'

    @api.model
    def _reparent_customer_rfq(self):
        """Hide the standard Sales module root ('Orders' on the Apps grid)."""
        standard_sale_root = self.env.ref('sale.sale_menu_root', raise_if_not_found=False)
        if standard_sale_root:
            standard_sale_root.write({'active': False})

    @api.model
    def _rename_purchase_orders_menu(self):
        purchase_root = self.env.ref('purchase.menu_purchase_root', raise_if_not_found=False)
        if not purchase_root:
            return
        orders_menu = self.env['ir.ui.menu'].search([
            ('parent_id', '=', purchase_root.id),
            ('name', '=', 'Orders'),
        ], limit=1)
        if orders_menu:
            orders_menu.write({'name': 'Suppliers'})

    @api.model
    def _rename_rfq_menu(self):
        purchase_root = self.env.ref('purchase.menu_purchase_root', raise_if_not_found=False)
        if not purchase_root:
            return
        orders_menu = self.env['ir.ui.menu'].search([
            ('parent_id', '=', purchase_root.id),
            ('name', 'in', ['Orders', 'Suppliers']),
        ], limit=1)
        if not orders_menu:
            return
        rfq_menu = self.env['ir.ui.menu'].search([
            ('parent_id', '=', orders_menu.id),
            ('name', '=', 'Requests for Quotation'),
        ], limit=1)
        if rfq_menu:
            rfq_menu.write({'name': 'Request for PO'})

    @api.model
    def _hide_sales_extra_menus(self):
        # Strictly use the code-defined menu
        sale_root = self.env.ref('contact_stage_bar.menu_sales_3', raise_if_not_found=False)
            
        accounts_menu = self.env.ref('contacts.menu_contacts', raise_if_not_found=False)

        if accounts_menu and sale_root:
            accounts_menu.write({
                'parent_id': sale_root.id,
                'active': True,
                'name': 'Leads',
            })

        # Hide specific sub-menus (Standard Sales menus)
        menus_to_hide = [
            'sale.menu_sale_quotations','sale.menu_sale_order','sale.menu_sale_invoicing','sale.product_menu_catalog','sale.menu_sale_report','sale.menu_sale_config',
        ]

        for xml_id in menus_to_hide:
            menu = self.env.ref(xml_id, raise_if_not_found=False)
            if menu:
                menu.write({'active': False})

    @api.model
    def _visible_menu_ids(self, debug=False):
        """
        Override to restrict menu visibility dynamically for specific LGD custom groups.
        This acts as a negative filter to strip away ALL default apps like Discuss, Calendar, Settings, etc.
        """
        # Get the standard visible menus.
        visible_ids = super()._visible_menu_ids(debug=debug)

        # Break recursion for Superusers AND normal Administrators, let them see everything natively.
        if self.env.su or self.env.user._is_superuser() or self.env.user.has_group('base.group_system'):
            return visible_ids

        user = self.env.user
        lgd_groups = {
            'procurement': user.has_group('contact_stage_bar.group_lgd_procurement'),
            'sales': user.has_group('contact_stage_bar.group_lgd_sales'),
            'logistics': user.has_group('contact_stage_bar.group_lgd_logistics'),
            'shipment': user.has_group('contact_stage_bar.group_lgd_shipment'),
            'accounting': user.has_group('contact_stage_bar.group_lgd_accounting'),
            'hr': user.has_group('contact_stage_bar.group_lgd_hr'),
            'marketing': user.has_group('contact_stage_bar.group_lgd_marketing'),
        }

        # If user has NONE of these specific custom groups, let standard Odoo access run normally
        if not any(lgd_groups.values()):
            return visible_ids

        allowed_root_refs = set()
        allowed_root_names = set()

        # Build allowed apps list based strictly on user's assigned group(s)
        if lgd_groups['procurement']:
            allowed_root_refs.update(['purchase.menu_purchase_root', 'custom_sale_order.menu_custom_sale_root'])
            
        if lgd_groups['sales']:
            allowed_root_refs.update(['contact_stage_bar.menu_sales_3', 'custom_sale_order.menu_custom_sale_root'])
            allowed_root_names.add('Sales')
            
        if lgd_groups['logistics']:
            allowed_root_refs.add('custom_sale_order.menu_custom_sale_root')
            allowed_root_names.update(['Logistics', 'Offline order'])
            
        if lgd_groups['shipment']:
            # Allows Inventory, Dispatch, Logistics, and LGD Inventory
            allowed_root_refs.update(['stock.menu_stock_root'])
            allowed_root_names.update(['Dispatch', 'Logistics', 'LGD Inventory', 'Inventory'])
            
        if lgd_groups['accounting']:
            # Only allow Invoicing & Offline Orders
            allowed_root_refs.update([
                'account.menu_finance', 
                'custom_sale_order.menu_custom_sale_root'
            ])
            allowed_root_names.add('Offline order')
            
        if lgd_groups['hr']:
            allowed_root_refs.update([
                'hr.menu_hr_root',
                'hr_attendance.menu_hr_attendance_root',
                'hr_holidays.menu_hr_holidays_root'
            ])
            
        if lgd_groups['marketing']:
            allowed_root_refs.update(['mass_mailing.mass_mailing_menu_root'])

        # Reports app (employee_reports) — soft reference, no manifest dependency.
        if user.has_group('employee_reports.group_reports_user'):
            allowed_root_refs.add('employee_reports.menu_reports_root')

        allowed_root_ids = set()

        # Resolve the XML IDs to actual database IDs
        for xml_id in allowed_root_refs:
            menu = self.env.ref(xml_id, raise_if_not_found=False)
            if menu:
                allowed_root_ids.add(menu.id)
        
        # Resolve custom UI-created menus dynamically by casting JSONB to text in SQL
        if allowed_root_names:
            for root_name in allowed_root_names:
                self.env.cr.execute(
                    "SELECT id FROM ir_ui_menu WHERE parent_id IS NULL AND name::text LIKE %s", 
                    (f'%"{root_name}"%',)
                )
                for row in self.env.cr.fetchall():
                    allowed_root_ids.add(row[0])

        if not allowed_root_ids:
            return set()

        # Bypass Odoo's ORM search loop to prevent recursion error using raw SQL
        # This safely grabs every single sub-menu underneath ONLY the allowed apps.
        self.env.cr.execute("""
            WITH RECURSIVE menu_tree AS (
                SELECT id FROM ir_ui_menu WHERE id = ANY(%s)
                UNION
                SELECT m.id FROM ir_ui_menu m
                INNER JOIN menu_tree mt ON m.parent_id = mt.id
            )
            SELECT id FROM menu_tree
        """, [list(allowed_root_ids)])

        allowed_menu_ids = set(row[0] for row in self.env.cr.fetchall())

        if lgd_groups['procurement']:
            self.env.cr.execute(
                "SELECT id FROM ir_ui_menu WHERE name::text LIKE %s OR name::text LIKE %s",
                ('%"Requests for Quotation"%', '%"Purchase Agreements"%')
            )
            hidden_for_procurement = set(row[0] for row in self.env.cr.fetchall())
            allowed_menu_ids -= hidden_for_procurement

        # Return the intersection: matching Odoo's allowed security with our allowed lists.
        # This instantly strips away Discuss, Apps, Settings, Notes, and ALL other base apps.
        return visible_ids.intersection(allowed_menu_ids)
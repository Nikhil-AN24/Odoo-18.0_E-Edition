from odoo import api, fields, models, _
from markupsafe import Markup

from odoo.exceptions import UserError

class CustomSaleOrder(models.Model):
    _name = 'custom.sale.order'
    _description = 'Custom Sale Order'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_order desc, id desc'


    name = fields.Char(string='Order Reference', required=True, copy=False, readonly=True, default='New')
    state = fields.Selection([
        ('draft', 'Draft Order'),
        ('to_approve', 'Price Checked'),
        ('price_done', 'Price Done'),
        ('sent', 'Quotation Sent'),
        ('confirmed', 'Sale Order Created'),
        ('cancel', 'Cancelled'),
    ], string='Status', readonly=True, copy=False, index=True, tracking=3, default='draft')

    sale_state = fields.Selection([
        ('draft', 'Quotation'),
        ('sent', 'Quotation Sent'),
        ('sale', 'Sales Order'),
        ('cancel', 'Cancelled'),
    ], string='Order Status', readonly=True, copy=False, index=True, tracking=True, default='draft')


    sdk_augmont_status = fields.Selection([
        ('Order Received', 'Order Received'),
        ('Diamond Booked', 'Diamond Booked'),
        ('Confirmed', 'Confirmed'),
        ('Order Confirmed', 'Order Confirmed'),
        ('Not available', 'Not available'),
        ('Cancelled', 'Cancelled'),
        ('Confirmation Pending', 'Confirmation Pending'),
        ('Availability Check', 'Availability Check'),
        ('In QC process', 'In QC process'),
        ('QC Fail', 'QC Fail'),
        ('Payment Pending', 'Payment Pending'),
        ('Payment Completed', 'Payment Completed'),
        ('Dispatched', 'Dispatched'),
        ('Return of Order', 'Return of Order'),
        ('Re - Dispatched', 'Re - Dispatched'),
        ('Delivered', 'Delivered'),
        ('Order Completed', 'Order Completed'),
    ], string='Order Status', tracking=True, default='Order Received',
       compute='_compute_augmont_status_from_lines', store=True, readonly=True)
    # Legacy free-text reason, kept for records cancelled before the wizard
    # below existed. New cancellations fill the structured fields instead.
    cancel_reason = fields.Text(string="Cancellation Reason")

    # ── Cancellation ──────────────────────────────────────────────────────────
    # Same shape as customer.rfq (contact_stage_bar), sharing its master list
    # of reasons so both documents are cancelled with the same vocabulary.
    cancel_reason_ids = fields.Many2many(
        'customer.rfq.cancel.reason',
        'custom_sale_order_cancel_reason_rel', 'order_id', 'reason_id',
        string='Cancellation Reason', readonly=True, copy=False, tracking=True,
    )
    cancel_comment = fields.Text(string='Cancellation Comment', readonly=True, copy=False)
    cancelled_by_id = fields.Many2one('res.users', string='Cancelled By', readonly=True, copy=False)
    cancelled_on = fields.Datetime(string='Cancelled On', readonly=True, copy=False)

    date_order = fields.Datetime(string='Quotation Date', required=True, index=True,
                                 states={'draft': [('readonly', False)], 'sent': [('readonly', False)]},
                                 copy=False, default=fields.Datetime.now)

    partner_id = fields.Many2one('res.partner', string='Customer', required=True,
                                 states={'draft': [('readonly', False)], 'sent': [('readonly', False)]},
                                 change_default=True, tracking=1, domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]")

    shipping_address = fields.Text(string="Shipping Address")
    billing_address = fields.Text(string="Billing Address")


    pricelist_id = fields.Many2one('product.pricelist', string='Pricelist',
                                   states={'draft': [('readonly', False)], 'sent': [('readonly', False)]},
                                   domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]")

    currency_id = fields.Many2one('res.currency', related='pricelist_id.currency_id', string='Currency', readonly=True)

    order_line = fields.One2many('custom.sale.order.line', 'order_id', string='Order Lines',
                                 states={'cancel': [('readonly', True)], 'confirmed': [('readonly', True)]}, copy=True, auto_join=True)

    amount_untaxed = fields.Monetary(string='Untaxed Amount', store=True, compute='_compute_amounts', tracking=5)
    amount_tax = fields.Monetary(string='Taxes', store=True, compute='_compute_amounts')
    amount_total = fields.Monetary(string='Total', store=True, compute='_compute_amounts', tracking=4)

    note = fields.Html('Terms and conditions')

    company_id = fields.Many2one('res.company', string='Company', required=True, readonly=True, default=lambda self: self.env.company)
    user_id = fields.Many2one('res.users', string='Salesperson', index=True, tracking=2, default=lambda self: self.env.user)

    payment_term_id = fields.Many2one('account.payment.term', string='Payment Terms',
                                      domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]")

    # Link to created sale order
    sale_order_id = fields.Many2one('sale.order', string='Created Sale Order', readonly=True, copy=False)

    # Back-link to the Customer RFQ this offline order was created from.
    customer_rfq_id = fields.Many2one('customer.rfq', string='Customer RFQ',
                                      readonly=True, copy=False, index=True)


    @api.depends('order_line.price_total')
    def _compute_amounts(self):
        for order in self:
            amount_untaxed = amount_tax = 0.0
            for line in order.order_line:
                amount_untaxed += line.price_subtotal
                amount_tax += line.price_tax
            order.update({
                'amount_untaxed': amount_untaxed,
                'amount_tax': amount_tax,
                'amount_total': amount_untaxed + amount_tax,
            })

    @api.onchange('partner_id')
    def onchange_partner_id(self):
        if self.partner_id:
            self.pricelist_id = self.partner_id.property_product_pricelist.id
            self.payment_term_id = self.partner_id.property_payment_term_id.id

    @api.depends('order_line.availability_status', 'sale_state')
    def _compute_augmont_status_from_lines(self):
        """Mirror sale.order C1→C11 cascade — read-only on custom.sale.order."""
        C1_IGNORE = frozenset([
            'diamond_booked', 'confirmed', 'not_available', 'cancelled',
            'in_qc_process', 'qc_fail', 'payment_pending', 'payment_completed',
            'dispatched', 're_dispatched',
        ])
        C3_IGNORE = frozenset([
            'diamond_booked', 'confirmed', 'qc_fail', 'not_available', 'cancelled',
        ])
        C7_IGNORE = frozenset([
            'cancelled', 'not_available', 'qc_fail', 'diamond_booked', 'confirmed',
            'payment_completed',
        ])
        C9_IGNORE = frozenset([
            'not_available', 'cancelled', 'in_qc_process', 'qc_fail',
            'payment_completed', 'dispatched', 'return_of_order',
            're_dispatched', 'delivered', 'order_completed', 'diamond_booked',
        ])
        LOGISTICS = frozenset(['dispatched', 'return_of_order', 're_dispatched'])

        for order in self:
            lines = order.order_line.filtered(lambda l: l.availability_status)
            if not lines:
                order.sdk_augmont_status = 'Order Received'
                continue

            all_statuses = set(lines.mapped('availability_status')) - {False}
            if not all_statuses:
                order.sdk_augmont_status = 'Order Received'
                continue

            def active_set(ignore):
                return set(
                    lines.filtered(lambda l: l.availability_status not in ignore)
                         .mapped('availability_status')
                ) - {False}

            c1_active = active_set(C1_IGNORE)
            c3_active = active_set(C3_IGNORE)
            c7_active = active_set(C7_IGNORE)
            c9_active = active_set(C9_IGNORE)

            if order.sale_state == 'cancel':
                order.sdk_augmont_status = 'Cancelled'
            elif c1_active and c1_active <= {'delivered', 'order_completed'}:
                order.sdk_augmont_status = 'Order Completed'
            elif all_statuses & LOGISTICS:
                order.sdk_augmont_status = 'Dispatched'
            elif c3_active and c3_active == {'payment_completed'}:
                order.sdk_augmont_status = 'Payment Completed'
            elif 'in_qc_process' in all_statuses:
                order.sdk_augmont_status = 'In QC process'
            elif 'not_available' in all_statuses or 'qc_fail' in all_statuses:
                order.sdk_augmont_status = 'Availability Check'
            elif 'payment_pending' in all_statuses and 'confirmed' in all_statuses:
                order.sdk_augmont_status = 'Order Confirmed'
            elif c7_active and c7_active == {'payment_pending'}:
                order.sdk_augmont_status = 'Payment Pending'
            elif 'confirmed' in all_statuses and 'diamond_booked' in all_statuses:
                order.sdk_augmont_status = 'Confirmation Pending'
            elif c9_active and c9_active == {'confirmed'}:
                order.sdk_augmont_status = 'Order Confirmed'
            elif 'diamond_booked' in all_statuses:
                order.sdk_augmont_status = 'Order Received'
            elif all_statuses == {'cancelled'}:
                order.sdk_augmont_status = 'Cancelled'
            else:
                order.sdk_augmont_status = 'Order Received'

    def action_quotation_send(self):
        for rec in self:
            rec.write({'sale_state': 'sent', 'state': 'sent'})

    def action_confirm_offline_order(self):
        for rec in self:
            # Guard: never create a second sale.order from the same offline order.
            if rec.sale_order_id:
                raise UserError(_(
                    "This offline order is already confirmed (Sale Order %s)."
                ) % rec.sale_order_id.name)
            if not rec.order_line:
                raise UserError(_(
                    "Add at least one line before confirming the offline order."
                ))

            if not rec.customer_rfq_id and not any(
                    line.certificate_number or line.lgd_stock_number
                    for line in rec.order_line):
                raise UserError(_(
                    "Please add stone details (Certificate Number or LGD SKU) "
                    'before confirming the offline order — use the "Add Product" '
                    "button above the order lines."
                ))
            sale_order = rec._create_sale_order()
            rec.write({
                'sale_state': 'sale',
                'state': 'confirmed',
                'sale_order_id': sale_order.id,
            })
        return True

    def action_cancel_offline_order(self):
        for rec in self:
            rec.write({'sale_state': 'cancel', 'state': 'cancel'})

    def action_set_to_quotation(self):
        for rec in self:
            rec.write({'sale_state': 'draft', 'state': 'draft'})

    def action_again_price_check(self):
        self.state = 'to_approve'

        subject = _("Sale Order %s created — follow up on price check and Certificate Number") % self.name
        body_html = Markup("""
                                <p>Dear Procurement Team,</p>
                                <p>The Offline order <strong>%s</strong> has been created.</p>
                                <p>Please follow up on price check and Certificate Number</p>
                            """ % (self.name))

        email_from = self.env.user.partner_id.email

        # fetch the group by xml_id
        group = self.env.ref("purchase.group_purchase_manager", raise_if_not_found=False)

        # collect all users' emails in that group
        recipients = []
        partners = []
        if group:
            recipients = group.users.mapped("partner_id.email")
            recipients = [email for email in recipients if email]  # remove empty emails
            partners = group.users.mapped("partner_id")

        # send mail
        mail_values = {
            'subject': subject,
            'body_html': body_html,
            'email_from': email_from,
            'email_to': ",".join(recipients),
        }
        self.env['mail.mail'].create(mail_values).send()

        if partners:
            self.message_post(
                subject=subject,
                body=body_html,
                message_type="notification",
                subtype_xmlid="mail.mt_comment",
                partner_ids=partners.ids,
            )
    
    def action_done(self):
        self.state = 'price_done'

        subject = _("Sale Order %s created — follow up on price check and Certificate Number") % self.name
        body_html = Markup("""
                                <p>Dear Sales Team,</p>
                                <p>The Price Check <strong>%s</strong> has been done.</p>
                                <p>Please process the offline order</p>
                            """ % (self.name))

        email_from = self.env.user.partner_id.email

        # fetch the group by xml_id
        group = self.env.ref("__export__.res_groups_49_fa649952", raise_if_not_found=False)

        # collect all users' emails in that group
        recipients = []
        partners = []
        if group:
            recipients = group.users.mapped("partner_id.email")
            recipients = [email for email in recipients if email]  # remove empty emails
            partners = group.users.mapped("partner_id")

        # send mail
        mail_values = {
            'subject': subject,
            'body_html': body_html,
            'email_from': email_from,
            'email_to': ",".join(recipients),
        }
        self.env['mail.mail'].create(mail_values).send()

        if partners:
            self.message_post(
                subject=subject,
                body=body_html,
                message_type="notification",
                subtype_xmlid="mail.mt_comment",
                partner_ids=partners.ids,
            )

    def action_approve(self):
        self.state = 'to_approve'

        subject = _("Sale Order %s created — follow up on price check and Certificate Number") % self.name
        body_html = Markup("""
                        <p>Dear Procurement Team,</p>
                        <p>The Offline order <strong>%s</strong> has been created.</p>
                        <p>Please follow up on price check and Certificate Number</p>
                    """ % (self.name))

        email_from = self.env.user.partner_id.email

        # fetch the group by xml_id
        group = self.env.ref("purchase.group_purchase_manager", raise_if_not_found=False)

        # collect all users' emails in that group
        recipients = []
        partners = []
        if group:
            recipients = group.users.mapped("partner_id.email")
            recipients = [email for email in recipients if email]  # remove empty emails
            partners = group.users.mapped("partner_id")

        # send mail
        mail_values = {
            'subject': subject,
            'body_html': body_html,
            'email_from': email_from,
            'email_to': ",".join(recipients),
        }
        self.env['mail.mail'].create(mail_values).send()

        if partners:
            self.message_post(
                subject=subject,
                body=body_html,
                message_type="notification",
                subtype_xmlid="mail.mt_comment",
                partner_ids=partners.ids,
            )

    def action_confirm(self):
        for order in self:
            # if order.state != 'draft':
            #     raise UserError(_('Only draft orders can be confirmed.'))

            # Create the actual sale order
            sale_order = self._create_sale_order()
            order.write({
                'state': 'confirmed',
                'sale_order_id': sale_order.id,
            })
        return True

    def _create_sale_order(self):
        self.ensure_one()

        # Prepare sale order lines
        order_lines = []
        for line in self.order_line:
            order_lines.append((0, 0, {
                'product_id': line.product_template_id.product_variant_id.id,
                'product_template_id': line.product_template_id.id,
                'product_uom_qty': line.product_uom_qty,
                'product_uom': line.product_uom.id,
                'price_unit': line.price_unit,
                'discount': line.discount,
                'tax_id': [(6, 0, line.tax_id.ids)],
                'name': line.name or line.product_template_id.display_name or '/',
                'availability_status': line.availability_status,
            }))


        # Create sale order
        sale_order_vals = {
            'partner_id': self.partner_id.id,
            # 'partner_invoice_id': self.partner_invoice_id.id,
            # 'partner_shipping_id': self.partner_shipping_id.id,
            'shipping_address': self.shipping_address,
            'billing_address': self.billing_address,
            'pricelist_id': self.pricelist_id.id,
            'payment_term_id': self.payment_term_id.id if self.payment_term_id else False,
            'user_id': self.user_id.id,
            'company_id': self.company_id.id,
            'note': self.note,
            'order_line': order_lines,
            'origin': self.name,
            # Offline serial reused as the Invoice Number so the Sales list can
            # show + differentiate offline orders (e.g. AUG-OFF-007).
            'sdk_augmont_number': self.name,
            'order_source': 'offline',
            'custom_sale_order_id': self.id,
        }

        sale_order = self.env['sale.order'].create(sale_order_vals)

        return sale_order

    def action_draft(self):
        self.write({'state': 'draft'})

    def action_open_cancel_wizard(self):
        """Open the reason prompt. Cancelling always goes through it, so an
        offline order can never end up cancelled with no explanation recorded.
        Mirrors customer.rfq.action_open_cancel_wizard."""
        self.ensure_one()
        if self.sale_state == 'cancel':
            raise UserError(_("This Offline Order is already cancelled."))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Cancel Offline Order'),
            'res_model': 'custom.sale.order.cancel.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_order_id': self.id},
        }

    def _apply_cancellation(self, reasons, comment):
        """Record the cancellation. Called by the wizard, not from the UI.
        Mirrors customer.rfq._apply_cancellation."""
        self.ensure_one()
        if self.sale_state == 'cancel':
            raise UserError(_("This Offline Order is already cancelled."))
        if not reasons:
            raise UserError(_("Select at least one cancellation reason."))
        self.write({
            'cancel_reason_ids': [(6, 0, reasons.ids)],
            'cancel_comment': comment or False,
            'cancelled_by_id': self.env.uid,
            'cancelled_on': fields.Datetime.now(),
        })
        # The state change goes through the existing primitive so inheriting
        # modules keep firing (amplitude_integration tracks
        # 'Offline Order Cancelled' on it).
        self.action_cancel_offline_order()
        body = _("Cancelled - %s") % ', '.join(reasons.mapped('name'))
        if comment:
            body += Markup("<br/>") + comment
        self.message_post(body=body)
        return True

    def action_open_cancel(self):
        # Legacy entry point — routes through the reason wizard now.
        return self.action_open_cancel_wizard()

    def action_cancel(self):
        self.write({'state': 'cancel'})


    def action_view_sale_order(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Sale Order',
            'res_model': 'sale.order',
            'res_id': self.sale_order_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_view_customer_rfq(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Customer RFQ',
            'res_model': 'customer.rfq',
            'res_id': self.customer_rfq_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    @api.model
    def _next_order_reference(self):
        """Draw the next AUG-OFF-xxxxx reference.
        next_by_code() only looks at sequences belonging to the *active*
        company (or to no company at all). The live sequence is pinned to a
        single company, so users logged into any other company silently got
        back False and the order was saved literally named "New". Fall back to
        the sequence itself, whichever company owns it. """
        Sequence = self.env['ir.sequence'].sudo()
        name = Sequence.next_by_code('custom.sale.order')
        if name:
            return name
        seq = Sequence.search([('code', '=', 'custom.sale.order')],
                              order='company_id', limit=1)
        return seq.next_by_id() if seq else 'New'

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self._next_order_reference()
        return super(CustomSaleOrder, self).create(vals_list)


class CustomSaleOrderLine(models.Model):
    _name = 'custom.sale.order.line'

    _description = 'Custom Sale Order Line'
    _order = 'order_id, sequence, id'

    order_id = fields.Many2one('custom.sale.order', string='Order Reference', required=True, ondelete='cascade',
                               index=True)
    sequence = fields.Integer(string='Sequence', default=10)

    product_id = fields.Many2one('product.product', string='Product', domain="[('sale_ok', '=', True)]",
                                 change_default=True, ondelete='restrict')

    product_template_id = fields.Many2one('product.template', string='Product', domain="[('sale_ok', '=', True)]",
                                 change_default=True, ondelete='restrict')

    # Traceability back to the RFQ size-line that generated this order line.
    # Populated when action_create_offline_order expands size_line.quantity
    # into N per-stone rows. Blank on lines that predate this behaviour.
    rfq_size_line_id = fields.Many2one(
        'customer.rfq.size.line', string='RFQ Size Line',
        ondelete='set null', index=True, copy=False,
    )

    name = fields.Text(string='Description')

    certificate_number = fields.Char(related = 'product_template_id.certificate', string="Certificate Number",)
    # vendor_sku = fields.Char(string="Vendor SKU")
    lgd_stock_number = fields.Char(related = 'product_template_id.lgd_stock_number', string="LGD SKU")
    carat_weight = fields.Char(string="Carat Weight")
    shapes = fields.Char(string="Shapes")
    color = fields.Char(string="Color")
    clarity = fields.Char(string="Clarity")
    cut = fields.Char(related = 'product_template_id.cut',string="Cut")
    polish = fields.Char(related = 'product_template_id.polish',string="Polish")
    symmetry = fields.Char(related = 'product_template_id.symmetry',string="Symmetry")
    fluorescence_color = fields.Char(related = 'product_template_id.fluorescence_color', string="Fluorescence Color")
    treatments = fields.Char(related = 'product_template_id.treatments',string="Treatments")
    availability_status = fields.Selection([
        ('diamond_booked', 'Diamond Booked'),
        ('confirmed', 'Confirmed'),
        ('not_available', 'Not available'),
        ('cancelled', 'Cancelled'),
        ('in_qc_process', 'In QC process'),
        ('qc_fail', 'QC Fail'),
        ('payment_pending', 'Payment Pending'),
        ('payment_completed', 'Payment Completed'),
        ('dispatched', 'Dispatched'),
        ('return_of_order', 'Return of Order'),
        ('re_dispatched', 'Re - Dispatched'),
        ('delivered', 'Delivered'),
        ('order_completed', 'Order Completed'),
    ], string='Availability', copy=False, required=True, default='diamond_booked', tracking=True)

    product_uom_qty = fields.Float(string='Quantity', digits='Product Unit of Measure', required=True, default=1.0)
    product_uom = fields.Many2one('uom.uom', string='Unit of Measure',
                                  domain="[('category_id', '=', product_uom_category_id)]")
    product_uom_category_id = fields.Many2one(related='product_id.uom_id.category_id', readonly=True)

    price_unit = fields.Float('Unit Price', required=True, digits='Product Price', default=0.0)
    discount = fields.Float(string='Discount (%)', digits='Discount', default=0.0)

    tax_id = fields.Many2many('account.tax', string='Taxes', domain="[('type_tax_use', '=', 'sale')]")

    price_subtotal = fields.Monetary(compute='_compute_amount', string='Subtotal', store=True)
    price_tax = fields.Float(compute='_compute_amount', string='Tax', store=True)
    price_total = fields.Monetary(compute='_compute_amount', string='Total', store=True)

    currency_id = fields.Many2one(related='order_id.currency_id', store=True, string='Currency', readonly=True)
    company_id = fields.Many2one(related='order_id.company_id', string='Company', store=True, readonly=True)

    @api.depends('product_uom_qty', 'discount', 'price_unit', 'tax_id', 'availability_status')
    def _compute_amount(self):
        zero_lines = self.filtered(
            lambda l: l.availability_status in ('cancelled', 'not_available')
        )
        zero_lines.update({
            'price_subtotal': 0.0,
            'price_tax': 0.0,
            'price_total': 0.0,
        })

        active_lines = self - zero_lines
        for line in active_lines:
            price = line.price_unit * (1 - (line.discount or 0.0) / 100.0)
            taxes = line.tax_id.compute_all(price, line.order_id.currency_id, line.product_uom_qty,
                                            product=line.product_id, partner=line.order_id.partner_id)
            line.update({
                'price_tax': sum(t.get('amount', 0.0) for t in taxes.get('taxes', [])),
                'price_total': taxes['total_included'],
                'price_subtotal': taxes['total_excluded'],
            })

    @api.onchange('product_template_id')
    def _onchange_product_template_id_specs(self):
        if self.product_template_id:
            tmpl = self.product_template_id
            self.shapes = tmpl.shapes
            self.color = tmpl.color
            self.clarity = tmpl.clarity
            self.carat_weight = tmpl.weight_carat


    @api.onchange('product_id')
    def product_id_change(self):
        if not self.product_id:
            return

        self.product_uom = self.product_id.uom_id.id
        # self.name = self.product_id.display_name

        # Get price from pricelist
        if self.order_id.pricelist_id and self.product_id:
            self.price_unit = self.order_id.pricelist_id._get_product_price(
                self.product_id,
                self.product_uom_qty or 1.0,
                currency=self.order_id.currency_id,
                date=self.order_id.date_order
            )

        # Get taxes
        if self.product_id:
            self.tax_id = self.product_id.taxes_id.filtered(lambda t: t.company_id == self.order_id.company_id)

    def action_open_product_popup(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'product.template',
            'view_mode': 'form',
            'res_id': self.product_template_id.id,
            'target': 'new',  # popup instead of new page
        }
            # For when click add a product button create unlink (Delete)

    def remove_empty_order_lines(self):
        """Delete order lines where Product, LGD SKU, and Certificate Number are empty"""
        for order in self:
            empty_lines = order.order_line.filtered(
                lambda l: not (l.product_id or l.lgd_sku or l.certificate_number)
            )
            if empty_lines:
                empty_lines.unlink()
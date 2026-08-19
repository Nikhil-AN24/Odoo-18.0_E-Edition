from odoo import models, fields, api


class ResPartner(models.Model):
    _inherit = "res.partner"

    ein_number = fields.Char(string="EIN Number")
    gst_stages = fields.Selection([
        ('pending', 'Pending'),
        ('verified', 'Verified'),       
        ('not_verified', 'Not Verified'),
        ('manual_verified', 'Manual Verified'),
        ('failed', 'Failed')
    ], string='GST Status', tracking=True)

    ein_status = fields.Selection([
        ('pending', 'Pending'),
        ('verified', 'Verified'),       
        ('not_verified', 'Not Verified'),
        ('manual_verified', 'Manual Verified'),
        ('failed', 'Failed')
    ], string='EIN Status', tracking=True)
    first_name = fields.Char(string="First Name")
    last_name = fields.Char(string="Last Name")
    contact_person_name = fields.Char(string="Contact Person Name")
    job_position = fields.Char(string="Job Position")
    billing_address = fields.Text(string="Billing Address")
    shipping_address_differs = fields.Boolean(
        string="The Shipping Address does not match the Billing Address.",
        default=False
    )
    shipping_address = fields.Text(string="Shipping Address", compute="_compute_shipping_address", store=True, readonly=False)

    @api.depends('billing_address', 'shipping_address_differs')
    def _compute_shipping_address(self):
        for partner in self:
            if not partner.shipping_address_differs:
                partner.shipping_address = partner.billing_address

    terms = fields.Selection([
        ('advance', 'Advance'),
        ('credit', 'Credit'),
        ('cod', 'Cash on Delivery'),
    ], string="Terms", default='advance')
    stage_id = fields.Many2one('res.partner.stage',string="Status",tracking=True,group_expand="_group_expand_stage_id")

    # company_type = fields.Selection(string='Company Type',
    # selection=[('person', 'Individual'), ('company', 'Company')],
    # compute='_compute_company_type', inverse='_write_company_type', default='company')
    
    
    category_ids = fields.Many2many(
        comodel_name='custom.category',
        relation='res_partner_account_tag_rel',  
        column1='partner_id',
        column2='tag_id',
        string="Category",
        copy=False,
    )
    primary_category_ids = fields.Many2many(
        comodel_name='custom.category',
        relation='res_partner_account_tag_rel_1', 
        column1='partner_id',
        column2='tag_id',
        string="Primary Category",
        copy=False,
    )
    lead_source = fields.Selection([('website', 'Website'), ('cold_call', 'Cold Call'), ('email', 'Email'), ('marketing_campaign', 'Marketing Campaign')], string='Lead Source')
    graduation_rate = fields.Selection([('0', 'No Rating'),('1', '1'),('2', '2'),('3', '3'),('4', '4'),], string='Graduation Rate', default='0') 
   
    # vendor_per_carat_price = fields.Float(string="Vendor Price Per Carat")
    # vendor_final_price = fields.Float(string="Vendor Final Price")
    # can_see_all = fields.Boolean(string="Is Admin", compute='_compute_can_see_all',store=False)
    
    @api.model
    def _group_expand_stage_id(self, stages, domain):
        return self.env['res.partner.stage'].search([])

    @api.model_create_multi
    def create(self, vals_list):
        return super(ResPartner, self.sudo()).create(vals_list)

    @api.model
    def name_create(self, name):
        return super(ResPartner, self.sudo()).name_create(name)
                
    # @api.depends_context('uid')
    # def _compute_can_see_all(self):
    #     admin_group = self.env.ref('base.group_system')
    #     for rec in self:
    #         rec.can_see_all = admin_group in self.env.user.groups_id
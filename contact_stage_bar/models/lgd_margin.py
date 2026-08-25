from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

class LgdMargin(models.Model):
    """
    Singleton master record that holds the full carat-range → margin-percentage
    table.  Only one lgd.margin record ever exists.
    """
    _name        = 'lgd.margin'
    _description = 'LGD Margin Configuration'
    _order       = 'id'

    # Kept in the DB so existing records are not broken; never shown in the UI.
    name = fields.Char(
        string='Stone Category',
        default='Margin Config',
        help='Internal label – not displayed in the UI.',
    )

    line_ids = fields.One2many(
        comodel_name='lgd.margin.line',
        inverse_name='margin_id',
        string='Carat-Margin Lines',
        copy=True,
    )

    default_margin_percentage = fields.Float(
        string='Default Margin (%)',
        digits=(5, 2),
        default=2.0,
        help='Markup added to the cost Procurement quotes on a Customer RFQ.\n\n'
             'Applies to every RFQ whose carat weight has no entry in the '
             'Carat-Margin Table below. A band listed there wins for that band; '
             'this value covers everything else, including stones under 1 carat, '
             'which no band covers.',
    )

    @api.constrains('default_margin_percentage')
    def _check_default_margin_percentage(self):
        for rec in self:
            if rec.default_margin_percentage < 0:
                raise ValidationError(_('Default margin percentage cannot be negative.'))

    @api.model
    def _get_default_margin_percentage(self):
        """The house markup, or 0.0 when Margins has never been opened.

        sudo() because Sales and Procurement price RFQs but cannot read
        lgd.margin -- the same reason _compute_pricing_totals already sudoes
        its lookup of the carat table.
        """
        config = self.sudo().search([], limit=1)
        return config.default_margin_percentage if config else 0.0

    @api.model
    def create(self, vals):
        """Prevent a second lgd.margin record from being created."""
        if self.sudo().search([], limit=1):
            raise ValidationError(_(
                'Only one Margin Configuration record is allowed.\n'
                'Please edit the existing record instead of creating a new one.'
            ))
        return super().create(vals)

    @api.model
    def _setup_singleton(self):
        """
        Guaranteed to run on every module upgrade via the <function> element
        in lgd_margin_views.xml.
        Creates the singleton lgd.margin record if it does not exist yet.
        Using sudo() throughout so this works regardless of which user triggers
        the upgrade (typically the Odoo service user / Administrator).
        """
        record = self.sudo().search([], limit=1)
        if not record:
            record = super(LgdMargin, self.sudo()).create({'name': 'Margin Config'})

        action = self.env.ref(
            'contact_stage_bar.action_lgd_margin_procurement',
            raise_if_not_found=False,
        )
        if action and action.res_id != record.id:
            action.sudo().write({'res_id': record.id})


    @api.model
    def action_open_singleton(self):
        """
        Called from the Admin server action (bound to lgd.margin).
        Opens the editable form with res_id set.
        """
        record = self.search([], limit=1)
        if not record:
            record = super(LgdMargin, self).create({'name': 'Margin Config'})

        procurement_action = self.env.ref(
            'contact_stage_bar.action_lgd_margin_procurement',
            raise_if_not_found=False,
        )
        if procurement_action and procurement_action.res_id != record.id:
            procurement_action.sudo().write({'res_id': record.id})

        view_id = self.env.ref('contact_stage_bar.view_lgd_margin_form_admin').id

        return {
            'type'     : 'ir.actions.act_window',
            'name'     : 'Margins',
            'res_model': 'lgd.margin',
            'view_mode': 'form',
            'res_id'   : record.id,
            'views'    : [(view_id, 'form')],
            'target'   : 'current',
        }


class LgdMarginLine(models.Model):
    """
    One line = one carat band + the margin percentage applied to it.
    """
    _name        = 'lgd.margin.line'
    _description = 'LGD Margin Line'
    _order       = 'margin_id, carat_range'

    CARAT_RANGE_SELECTION = [
        ('1_to_1_49',   '1 to 1.49'),
        ('1_5_to_1_99', '1.5 to 1.99'),
        ('2_to_2_49',   '2 to 2.49'),
        ('2_5_to_2_99', '2.5 to 2.99'),
        ('3_to_3_99',   '3 to 3.99'),
        ('4_to_4_99',   '4 to 4.99'),
        ('5_to_5_99',   '5 to 5.99'),
        ('6_to_6_99',   '6 to 6.99'),
        ('7_plus',      '7+'),
    ]

    margin_id = fields.Many2one(
        comodel_name='lgd.margin',
        string='Margin Configuration',
        required=True,
        ondelete='cascade',
    )

    carat_range = fields.Selection(
        selection=CARAT_RANGE_SELECTION,
        string='Carat',
        required=True,
    )

    percentage = fields.Float(
        string='Percentage (%)',
        required=True,
        digits=(5, 2),
        help='Margin percentage to apply for this carat band (e.g. 3.00 means 3%)',
    )

    @api.constrains('percentage')
    def _check_percentage(self):
        for rec in self:
            if rec.percentage < 0:
                raise ValidationError(_('Margin percentage cannot be negative.'))

    @api.constrains('carat_range', 'margin_id')
    def _check_unique_carat_per_category(self):
        """Each carat band may appear only once in the table."""
        for rec in self:
            duplicate = self.search([
                ('margin_id',   '=', rec.margin_id.id),
                ('carat_range', '=', rec.carat_range),
                ('id',          '!=', rec.id),
            ], limit=1)
            if duplicate:
                raise ValidationError(_(
                    'The carat range "%s" is already in the table. '
                    'Each carat band can only appear once.',
                    dict(self.CARAT_RANGE_SELECTION).get(rec.carat_range, rec.carat_range)
                ))
from odoo import fields, models


class PurchaseMeleeValue(models.Model):
    """Shared list of selectable values for the Melee Many2many fields on
    Purchase Orders (used by both Discount-Melee and Payment-Terms-Melee).

    Records are created/managed by the user from Purchase > Configuration >
    Melee Values. The value shown in the Many2many dropdown is the Char
    ``name`` (relational dropdowns render plain text only, so HTML cannot be
    used as the selectable label)."""

    _name = 'purchase.melee.value'
    _description = 'Purchase Melee Value'
    _order = 'sequence, name'

    name = fields.Char(string='Value', required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True) 

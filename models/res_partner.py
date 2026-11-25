from odoo import models, fields, api


class ResPartner(models.Model):
    _inherit = "res.partner"

    fbr_buyer_type_id = fields.Many2one(
        'fbr.buyer.type',
        string='FBR Buyer Type',
        help='Select the buyer registration type (Registered / Unregistered)',
    )
    fbr_wht_236g_type = fields.Selection(
        selection=[
            ('none', 'None'),
            ('0.1', 'WHT 236G (0.1% - Registered Distributor)'),
            ('0.01', 'WHT 236G (0.01% - Unregistered Distributor)'),
        ],
        string='Default WHT 236G',
        default='none',
        help=(
            'Default Section 236G Advance Income Tax rate applied when creating '
            'Sale Orders or Customer Invoices for this distributor.'
        ),
    )
    fbr_apply_extra_tax = fields.Boolean(
        string='Default 4% Extra Tax (3rd Schedule)',
        default=False,
        help=(
            'If checked, 4% Extra Tax on Retail Value Excl. ST is automatically '
            'enabled on Sale Orders and Invoices for this customer.'
        ),
    )

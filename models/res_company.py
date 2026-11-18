# -*- coding: utf-8 -*-

from odoo import models, fields


class ResCompany(models.Model):
    _inherit = "res.company"

    fbr_strict_retail_base_from_tax = fields.Boolean(
        string="FBR Strict Retail Base from Tax (ST / 0.18)",
        default=False,
        help=(
            "If enabled, fixedNotifiedValueOrRetailPrice in FBR SN008 payload "
            "is calculated as round(salesTaxApplicable / 0.18, 2) instead of "
            "round(quantity * fbr_retail_price_excl, 2)."
        ),
    )

# -*- coding: utf-8 -*-

from . import controllers
from . import models
from .models.product_template import _parse_float_str


def _setup_fbr_taxes_and_backfill(env):
    """Ensure FBR 3rd Schedule & WHT 236G taxes, tax groups, and accounts exist,
    and backfill retail price breakdown fields on existing products and draft invoices.
    """
    AccountTaxGroup = env['account.tax.group'].sudo()
    AccountTax = env['account.tax'].sudo()
    Account = env['account.account'].sudo()

    for company in env['res.company'].sudo().search([]):
        country = company.account_fiscal_country_id or company.country_id or env.ref('base.pk', raise_if_not_found=False)

        # Find a reference liability account from existing sale taxes or chart of accounts
        existing_sale_tax = AccountTax.search([
            ('company_id', '=', company.id),
            ('type_tax_use', '=', 'sale'),
        ], limit=1)
        default_tax_account = False
        if existing_sale_tax:
            tax_rep = existing_sale_tax.invoice_repartition_line_ids.filtered(
                lambda r: r.repartition_type == 'tax' and r.account_id
            )[:1]
            if tax_rep:
                default_tax_account = tax_rep.account_id

        if not default_tax_account:
            default_tax_account = Account.search([
                ('company_ids', 'in', company.id),
                ('account_type', '=', 'liability_current'),
            ], limit=1)

        # Find or create a dedicated WHT 236G Advance Tax liability account
        wht_account = Account.search([
            ('company_ids', 'in', company.id),
            ('name', 'ilike', '236G'),
        ], limit=1)
        if not wht_account and default_tax_account:
            wht_code = '251236'
            while Account.search([('company_ids', 'in', company.id), ('code', '=', wht_code)], limit=1):
                wht_code = str(int(wht_code) + 1)
            try:
                wht_account = Account.create({
                    'name': 'WHT 236G Advance Tax Payable',
                    'code': wht_code,
                    'account_type': 'liability_current',
                    'company_ids': [(4, company.id)],
                })
            except Exception:
                wht_account = default_tax_account

        def _get_or_create_group(name, seq):
            grp = AccountTaxGroup.search([
                ('name', '=', name),
                ('company_id', '=', company.id),
            ], limit=1)
            if not grp:
                vals = {
                    'name': name,
                    'sequence': seq,
                    'company_id': company.id,
                }
                if country:
                    vals['country_id'] = country.id
                grp = AccountTaxGroup.create(vals)
            return grp

        grp_st = _get_or_create_group('Sales Tax 18%', 10)
        grp_extra = _get_or_create_group('Extra Tax 4%', 20)
        grp_wht = _get_or_create_group('WHT 236G', 30)

        tax_specs = [
            ('fbr_tax_3rd_schedule_st_18', '18% Sales Tax (3rd Schedule)', 18.0, '3rd_schedule_st', grp_st, default_tax_account, 10),
            ('fbr_tax_3rd_schedule_extra_4', '4% Extra Tax (3rd Schedule)', 4.0, '3rd_schedule_extra', grp_extra, default_tax_account, 20),
            ('fbr_tax_wht_236g_01', 'WHT 236G (0.1%)', 0.1, 'wht_236g', grp_wht, wht_account, 30),
            ('fbr_tax_wht_236g_001', 'WHT 236G (0.01%)', 0.01, 'wht_236g', grp_wht, wht_account, 31),
        ]

        for xml_id, name, amount, fbr_type, group, target_account, seq in tax_specs:
            tax = env.ref(f'fbr_pakistan.{xml_id}', raise_if_not_found=False)
            if tax and tax.company_id != company:
                tax = False
            if not tax:
                tax = AccountTax.search([
                    ('company_id', '=', company.id),
                    ('type_tax_use', '=', 'sale'),
                    '|',
                    ('name', '=', name),
                    '&',
                    ('fbr_tax_type', '=', fbr_type),
                    ('amount', '=', amount),
                ], limit=1)
            if not tax:
                vals = {
                    'name': name,
                    'amount': amount,
                    'amount_type': 'percent',
                    'type_tax_use': 'sale',
                    'price_include_override': 'tax_excluded',
                    'include_base_amount': False,
                    'is_base_affected': False,
                    'fbr_tax_type': fbr_type,
                    'tax_group_id': group.id,
                    'company_id': company.id,
                    'sequence': seq,
                }
                if country:
                    vals['country_id'] = country.id
                tax = AccountTax.create(vals)
            else:
                tax.write({
                    'fbr_tax_type': fbr_type,
                    'price_include_override': 'tax_excluded',
                    'include_base_amount': False,
                    'is_base_affected': False,
                    'tax_group_id': group.id,
                })

            if target_account:
                for rep_line in (tax.invoice_repartition_line_ids | tax.refund_repartition_line_ids).filtered(
                    lambda r: r.repartition_type == 'tax' and not r.account_id
                ):
                    rep_line.account_id = target_account.id

    # Backfill existing product.template records that have fbr_fixed_notified_value set
    products = env['product.template'].sudo().search([
        ('fbr_retail_price_inc', '=', 0.0),
        ('fbr_retail_price_excl', '=', 0.0),
        ('fbr_fixed_notified_value', 'not in', (False, '', '0', '0.0', '0.00')),
    ])
    for prod in products:
        inc = round(_parse_float_str(prod.fbr_fixed_notified_value), 2)
        if inc > 0.0:
            prod.write({'fbr_retail_price_inc': inc})

    # Backfill existing sale.order.line and draft account.move pages
    sols = env['sale.order.line'].sudo().search([
        ('fbr_retail_price_inc', '=', 0.0),
        ('fbr_retail_price_excl', '=', 0.0),
        ('product_id.fbr_retail_price_inc', '>', 0.0),
    ])
    for sol in sols:
        sol.write({
            'fbr_retail_price_inc': sol.product_id.fbr_retail_price_inc,
            'fbr_retail_price_excl': sol.product_id.fbr_retail_price_excl,
            'fbr_retail_unit_tax': sol.product_id.fbr_retail_unit_tax,
        })

    draft_moves = env['account.move'].sudo().search([
        ('state', '=', 'draft'),
        ('move_type', 'in', ('out_invoice', 'out_refund')),
    ])
    for move in draft_moves:
        if any(
            l.product_id.fbr_retail_price_inc > 0.0
            or (l.fbr_fixed_notified_value and l.fbr_fixed_notified_value not in ('0', '0.0', '0.00'))
            for l in move.invoice_line_ids
        ):
            move.action_recompute_fbr_taxes()


def post_init_hook(env):
    _setup_fbr_taxes_and_backfill(env)

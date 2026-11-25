# -*- coding: utf-8 -*-

from . import controllers
from . import models
from .models.product_template import _parse_float_str, _is_product_3rd_schedule


FBR_DEFAULT_SCENARIOS = [
    ('SN001', 'SN001 - Goods at standard rate to registered buyers'),
    ('SN002', 'SN002 - Goods at standard rate to unregistered buyers'),
    ('SN003', 'SN003 - Sale of Steel (Melted and Re-Rolled)'),
    ('SN004', 'SN004 - Sale by Ship Breakers'),
    ('SN005', 'SN005 - Reduced rate sale'),
    ('SN006', 'SN006 - Exempt goods sale'),
    ('SN007', 'SN007 - Zero rated sale'),
    ('SN008', 'SN008 - Sale of 3rd schedule goods'),
    ('SN009', 'SN009 - Cotton Spinners purchase from Cotton Ginners'),
    ('SN010', 'SN010 - Mobile Operators / Telecom Services'),
    ('SN011', 'SN011 - Toll Manufacturing sale by Steel sector'),
    ('SN012', 'SN012 - Sale of Petroleum products'),
    ('SN013', 'SN013 - Electricity Supply to Retailers'),
    ('SN014', 'SN014 - Sale of Gas to CNG stations'),
    ('SN015', 'SN015 - Sale of mobile phones'),
    ('SN016', 'SN016 - Processing / Conversion of Goods'),
    ('SN017', 'SN017 - Sale of Goods where FED is charged in ST mode'),
    ('SN018', 'SN018 - Sale of Services where FED is charged in ST mode'),
    ('SN019', 'SN019 - Sale of Services'),
    ('SN020', 'SN020 - Sale of Electric Vehicles'),
    ('SN021', 'SN021 - Sale of Cement / Concrete Block'),
    ('SN022', 'SN022 - Sale of Potassium Chlorate'),
    ('SN023', 'SN023 - Sale of CNG'),
    ('SN024', 'SN024 - Goods sold as per SRO 297(I)/2023'),
    ('SN025', 'SN025 - Drugs sold at fixed ST rate (Non-Adjustable Supplies)'),
    ('SN026', 'SN026 - Sale to End Consumer by retailers (Standard Rate)'),
    ('SN027', 'SN027 - Sale to End Consumer by retailers (3rd Schedule)'),
    ('SN028', 'SN028 - Sale to End Consumer by retailers (Reduced Rate)'),
]


def _setup_fbr_scenarios(env):
    """Ensure all 28 official FBR scenarios (SN001-SN028) exist without duplicating
    or colliding with manually created scenarios or XML records.
    """
    FbrScenario = env['fbr.scenarios'].sudo()
    for code, name in FBR_DEFAULT_SCENARIOS:
        existing = FbrScenario.search([
            '|',
            ('code', '=ilike', code),
            ('name', '=ilike', name),
        ], limit=1)
        if not existing:
            FbrScenario.create({
                'code': code,
                'name': name,
            })
        elif not existing.code:
            existing.write({'code': code})


def _setup_fbr_taxes_and_backfill(env):
    """Ensure all FBR scenarios, 3rd Schedule & WHT 236G taxes, tax groups, and accounts exist,
    and backfill retail price breakdown fields on existing products and draft invoices.
    """
    _setup_fbr_scenarios(env)
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

    # Backfill existing 3rd Schedule product.template records that have fbr_fixed_notified_value set
    products = env['product.template'].sudo().search([
        ('fbr_retail_price_inc', '=', 0.0),
        ('fbr_retail_price_excl', '=', 0.0),
        ('fbr_fixed_notified_value', 'not in', (False, '', '0', '0.0', '0.00')),
    ])
    for prod in products:
        if not _is_product_3rd_schedule(prod):
            continue
        inc = round(_parse_float_str(prod.fbr_fixed_notified_value), 2)
        if inc > 0.0:
            prod.write({'fbr_retail_price_inc': inc})

    # Clean stale 3rd-schedule 18% retail breakdown fields on explicitly non-3rd-schedule products
    # (e.g. Eighth Schedule / Reduced Rate) while preserving fbr_fixed_notified_value.
    non_3rd_products = env['product.template'].sudo().search([
        ('fbr_sro_id', '!=', False),
        ('fbr_sro_name', '!=', '3rd Schedule goods'),
        '|', '|',
        ('fbr_retail_price_inc', '!=', 0.0),
        ('fbr_retail_price_excl', '!=', 0.0),
        ('fbr_retail_unit_tax', '!=', 0.0),
    ])
    for prod in non_3rd_products:
        if _is_product_3rd_schedule(prod):
            continue
        prod.write({
            'fbr_retail_price_inc': 0.0,
            'fbr_retail_price_excl': 0.0,
            'fbr_retail_unit_tax': 0.0,
            'fbr_fixed_notified_value': prod.fbr_fixed_notified_value or '0',
        })

    # Backfill existing 3rd Schedule sale.order.line and draft account.move lines
    sols = env['sale.order.line'].sudo().search([
        ('fbr_retail_price_inc', '=', 0.0),
        ('fbr_retail_price_excl', '=', 0.0),
        ('product_id.fbr_retail_price_inc', '>', 0.0),
    ])
    for sol in sols:
        if sol.product_id.fbr_sro_id and not _is_product_3rd_schedule(sol.product_id):
            continue
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
        product_lines = move.invoice_line_ids.filtered(lambda l: l.display_type in (False, 'product'))
        is_3rd_move = move._is_fbr_3rd_schedule_move()
        if any(
            (
                is_3rd_move
                and (
                    l.fbr_retail_price_inc > 0.0
                    or l.fbr_retail_price_excl > 0.0
                    or l.fbr_retail_unit_tax > 0.0
                    or l.product_id.fbr_retail_price_inc > 0.0
                    or (l.fbr_fixed_notified_value and l.fbr_fixed_notified_value not in ('0', '0.0', '0.00'))
                )
            )
            or (
                not is_3rd_move
                and (l.fbr_retail_price_inc or l.fbr_retail_price_excl or l.fbr_retail_unit_tax)
            )
            for l in product_lines
        ):
            move.action_recompute_fbr_taxes()


def post_init_hook(env):
    _setup_fbr_taxes_and_backfill(env)

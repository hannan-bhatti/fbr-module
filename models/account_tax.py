# -*- coding: utf-8 -*-

from odoo import models, fields, api
from odoo.tools import float_round
from .product_template import _parse_float_str, _is_fbr_3rd_schedule


class AccountTax(models.Model):
    _inherit = "account.tax"

    fbr_tax_type = fields.Selection(
        selection=[
            ('none', 'Standard'),
            ('3rd_schedule_st', '18% 3rd Schedule Retail Sales Tax'),
            ('3rd_schedule_extra', '4% 3rd Schedule Extra Tax'),
            ('wht_236g', 'WHT 236G Advance Tax'),
        ],
        string='FBR Tax Calculation Type',
        default='none',
        required=True,
        help=(
            "Defines how this tax is calculated for FBR compliance:\n"
            "- Standard: Normal Odoo percentage/fixed calculation on untaxed Trade Value.\n"
            "- 18% 3rd Schedule Retail Sales Tax: Calculated as Quantity * Retail Sales Tax 18% (Unit).\n"
            "- 4% 3rd Schedule Extra Tax: Calculated as round(Quantity * Retail Value Excl. ST (Unit) * Rate%, 2).\n"
            "- WHT 236G Advance Tax: Calculated on untaxed Trade Sales Value (excluded from FBR Sales Tax payload)."
        ),
    )

    def _eval_taxes_computation_prepare_product_fields(self):
        res = super()._eval_taxes_computation_prepare_product_fields()
        res.update({
            'fbr_retail_price_inc',
            'fbr_retail_price_excl',
            'fbr_retail_unit_tax',
        })
        return res

    @api.model
    def _fbr_ensure_taxes_setup(self):
        from .. import _setup_fbr_taxes_and_backfill
        _setup_fbr_taxes_and_backfill(self.env)

    @api.model
    def _prepare_base_line_for_taxes_computation(self, record, **kwargs):
        base_line = super()._prepare_base_line_for_taxes_computation(record, **kwargs)

        def load(field, fallback):
            return self._get_base_line_field_value_from_record(record, field, kwargs, fallback)

        product = base_line.get('product_id') or self.env['product.product']
        taxes = base_line.get('tax_ids')
        is_invoice_line = bool(record and getattr(record, '_name', None) == 'account.move.line')

        if is_invoice_line:
            move = record.move_id
            is_3rd_schedule = bool(
                record._is_fbr_3rd_schedule_line()
                or (move and move._is_fbr_3rd_schedule_move())
            )
            if not is_3rd_schedule:
                base_line.update({
                    'fbr_retail_price_inc': 0.0,
                    'fbr_retail_price_excl': 0.0,
                    'fbr_retail_unit_tax': 0.0,
                    'fbr_is_3rd_schedule': False,
                })
                return base_line
            inc, excl, unit_tax, _ = move._resolve_line_3rd_schedule_retail(record) if move else (
                record.fbr_retail_price_inc or 0.0,
                record.fbr_retail_price_excl or 0.0,
                record.fbr_retail_unit_tax or 0.0,
                record.fbr_fixed_notified_value or '0',
            )
            retail_inc = kwargs.get('fbr_retail_price_inc') or inc
            retail_excl = kwargs.get('fbr_retail_price_excl') or excl
            retail_unit_tax = kwargs.get('fbr_retail_unit_tax') or unit_tax
            if retail_inc > 0.0 and retail_unit_tax > 0.0 and not retail_excl and retail_unit_tax < retail_inc:
                retail_excl = round(retail_inc - retail_unit_tax, 2)
            elif retail_inc > 0.0 and not retail_excl and not retail_unit_tax:
                retail_excl = round(retail_inc / 1.18, 2)
                retail_unit_tax = round(retail_inc - retail_excl, 2)
            base_line.update({
                'fbr_retail_price_inc': retail_inc,
                'fbr_retail_price_excl': retail_excl,
                'fbr_retail_unit_tax': retail_unit_tax,
                'fbr_is_3rd_schedule': True,
            })
            return base_line

        line_sro = getattr(record, 'fbr_sro_id', None) if record else None
        sro_name = (
            line_sro.name
            if line_sro
            else (product.fbr_sro_name if product else '')
        )

        is_explicit_3rd = _is_fbr_3rd_schedule(
            sro_name=sro_name,
            tax_ids=taxes,
            include_retail=False,
        )

        retail_inc = load('fbr_retail_price_inc', 0.0)
        retail_excl = load('fbr_retail_price_excl', 0.0)
        retail_unit_tax = load('fbr_retail_unit_tax', 0.0)
        fixed_notified = load('fbr_fixed_notified_value', '')

        # Fallback to product values if line fields are not populated yet
        if not retail_inc and not retail_excl and not retail_unit_tax and product:
            retail_inc = product.fbr_retail_price_inc or 0.0
            retail_excl = product.fbr_retail_price_excl or 0.0
            retail_unit_tax = product.fbr_retail_unit_tax or 0.0
            if not fixed_notified:
                fixed_notified = product.fbr_fixed_notified_value or ''

        # Fallback to legacy fbr_fixed_notified_value string ONLY for explicit 3rd Schedule items
        if is_explicit_3rd and not retail_inc and not retail_excl and not retail_unit_tax and fixed_notified:
            parsed_inc = round(_parse_float_str(fixed_notified), 2)
            if parsed_inc > 0.0:
                retail_inc = parsed_inc
                retail_excl = round(parsed_inc / 1.18, 2)
                retail_unit_tax = round(parsed_inc - retail_excl, 2)

        if retail_inc > 0.0 and not retail_excl and not retail_unit_tax:
            retail_excl = round(retail_inc / 1.18, 2)
            retail_unit_tax = round(retail_inc - retail_excl, 2)
        elif retail_excl > 0.0 and not retail_unit_tax:
            if retail_inc > 0.0 and abs(retail_inc - round(retail_excl * 1.18, 2)) <= 0.05:
                retail_unit_tax = round(retail_inc - retail_excl, 2)
            else:
                retail_unit_tax = round(retail_excl * 0.18, 2)
                retail_inc = round(retail_excl + retail_unit_tax, 2)

        is_3rd_schedule = _is_fbr_3rd_schedule(
            sro_name=sro_name,
            tax_ids=taxes,
            retail_unit_tax=retail_unit_tax,
            retail_excl=retail_excl,
        )

        if not is_3rd_schedule:
            retail_inc = 0.0
            retail_excl = 0.0
            retail_unit_tax = 0.0

        base_line.update({
            'fbr_retail_price_inc': retail_inc,
            'fbr_retail_price_excl': retail_excl,
            'fbr_retail_unit_tax': retail_unit_tax,
            'fbr_is_3rd_schedule': is_3rd_schedule,
        })
        return base_line

    @api.model
    def _prepare_base_line_grouping_key(self, base_line):
        res = super()._prepare_base_line_grouping_key(base_line)
        # When called with fake_base_line (record=None) from account.move._sync_tax_lines,
        # register FBR retail fields as tracked base-line fields so changing them on a draft
        # invoice line automatically triggers tax line recomputation.
        if base_line.get('record') is None:
            res.update({
                'fbr_retail_price_inc': 0.0,
                'fbr_retail_price_excl': 0.0,
                'fbr_retail_unit_tax': 0.0,
                'fbr_fixed_notified_value': '',
            })
        return res

    @api.model
    def _add_tax_details_in_base_line(self, base_line, company, rounding_method=None):
        tax_ids = base_line.get('tax_ids')
        if tax_ids:
            base_line['tax_ids'] = tax_ids.with_context(
                fbr_base_line_retail_price_inc=base_line.get('fbr_retail_price_inc', 0.0),
                fbr_base_line_retail_price_excl=base_line.get('fbr_retail_price_excl', 0.0),
                fbr_base_line_retail_unit_tax=base_line.get('fbr_retail_unit_tax', 0.0),
                fbr_base_line_is_3rd_schedule=base_line.get('fbr_is_3rd_schedule', False),
            )
        try:
            super()._add_tax_details_in_base_line(base_line, company, rounding_method=rounding_method)
        finally:
            if tax_ids:
                base_line['tax_ids'] = tax_ids

        # Ensure WHT 236G (or sub-0.1% advance tax on untaxed Trade Value) preserves unrounded
        # raw_tax_amount so invoice-level WHT 236G rounds on total untaxed Trade Sales Value
        # (e.g. 60,480.00 * 0.01% = 6.048 -> 6.05 on Mujahid Traders).
        rate = base_line.get('rate') or 1.0
        for tax_data in base_line.get('tax_details', {}).get('taxes_data', []):
            tax = tax_data.get('tax')
            if tax and (
                tax.fbr_tax_type == 'wht_236g'
                or (
                    tax.amount_type == 'percent'
                    and 0.0 < abs(tax.amount) < 1.0
                    and 'reduced' not in (tax.name or '').lower()
                )
            ):
                unrounded_currency = tax_data['raw_base_amount_currency'] * (tax.amount / 100.0)
                tax_data['raw_tax_amount_currency'] = unrounded_currency
                tax_data['raw_tax_amount'] = unrounded_currency / rate if rate else 0.0

    @api.model
    def _round_base_lines_tax_details(self, base_lines, company, tax_lines=None):
        super()._round_base_lines_tax_details(base_lines, company, tax_lines=tax_lines)
        # Re-synchronize total_included after _round_tax_details_tax_amounts distributes any
        # invoice-level WHT 236G delta across base_lines.
        for base_line in base_lines:
            tax_details = base_line.get('tax_details')
            if not tax_details:
                continue
            for suffix in ('_currency', ''):
                tax_details[f'total_included{suffix}'] = (
                    tax_details[f'total_excluded{suffix}']
                    + sum(td[f'tax_amount{suffix}'] for td in tax_details.get('taxes_data', []))
                )

    def _eval_tax_amount_price_excluded(self, batch, raw_base, evaluation_context):
        self.ensure_one()
        if self.amount_type == 'percent':
            product_ctx = evaluation_context.get('product') or {}
            if 'fbr_base_line_is_3rd_schedule' in self.env.context:
                is_3rd_schedule = bool(self.env.context.get('fbr_base_line_is_3rd_schedule'))
                retail_unit_tax = (
                    (self.env.context.get('fbr_base_line_retail_unit_tax') or 0.0)
                    if is_3rd_schedule
                    else 0.0
                )
                retail_price_excl = (
                    (self.env.context.get('fbr_base_line_retail_price_excl') or 0.0)
                    if is_3rd_schedule
                    else 0.0
                )
            else:
                retail_unit_tax = product_ctx.get('fbr_retail_unit_tax') or 0.0
                retail_price_excl = product_ctx.get('fbr_retail_price_excl') or 0.0
                is_3rd_schedule = bool(retail_unit_tax > 0.0 or retail_price_excl > 0.0)
            quantity = evaluation_context.get('quantity', 0.0)
            sign = -1.0 if evaluation_context.get('price_unit', 0.0) < 0.0 else 1.0

            # 18% 3rd Schedule Sales Tax (either dedicated fbr_tax_type='3rd_schedule_st'
            # or standard 18% tax applied on a 3rd Schedule product line with retail unit tax > 0)
            if (
                self.fbr_tax_type == '3rd_schedule_st'
                or (self.fbr_tax_type in ('none', False) and self.amount == 18.0 and is_3rd_schedule)
            ) and retail_unit_tax > 0.0:
                return float_round(sign * quantity * retail_unit_tax, precision_rounding=0.01)

            # 4% 3rd Schedule Extra Tax (either dedicated fbr_tax_type='3rd_schedule_extra'
            # or 4% Extra Tax applied on a 3rd Schedule product line with retail price excl > 0)
            if (
                self.fbr_tax_type == '3rd_schedule_extra'
                or (
                    self.fbr_tax_type in ('none', False)
                    and self.amount == 4.0
                    and is_3rd_schedule
                    and 'further' not in (self.name or '').lower()
                )
            ) and retail_price_excl > 0.0:
                return float_round(
                    sign * quantity * retail_price_excl * (self.amount / 100.0),
                    precision_rounding=0.01,
                )

        return super()._eval_tax_amount_price_excluded(batch, raw_base, evaluation_context)

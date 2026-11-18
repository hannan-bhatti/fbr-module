# -*- coding: utf-8 -*-

from odoo import models, fields, api
from .product_template import _parse_float_str, _compute_retail_breakdown_vals


def _extract_product_retail_vals(product):
    if not product:
        return 0.0, 0.0, 0.0, '0'
    inc = round(product.fbr_retail_price_inc or 0.0, 2)
    excl = round(product.fbr_retail_price_excl or 0.0, 2)
    tax = round(product.fbr_retail_unit_tax or 0.0, 2)
    fixed_str = product.fbr_fixed_notified_value or '0'
    if not inc and not excl and not tax and fixed_str:
        parsed = round(_parse_float_str(fixed_str), 2)
        if parsed > 0.0:
            inc = parsed
            excl = round(parsed / 1.18, 2)
            tax = round(parsed - excl, 2)
    if inc > 0.0 and not excl and not tax:
        excl = round(inc / 1.18, 2)
        tax = round(inc - excl, 2)
    elif excl > 0.0 and not tax:
        if inc > 0.0 and abs(inc - round(excl * 1.18, 2)) <= 0.05:
            tax = round(inc - excl, 2)
        else:
            tax = round(excl * 0.18, 2)
            inc = round(excl + tax, 2)
    if inc > 0.0 and (not fixed_str or fixed_str == '0'):
        fixed_str = f"{inc:g}"
    return inc, excl, tax, fixed_str


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    fbr_retail_price_inc = fields.Float(
        string='Retail Price (Inc. 18% ST)',
        digits=(16, 2),
        compute='_compute_fbr_retail_fields',
        store=True,
        readonly=False,
        precompute=True,
        help='Tax-inclusive Consumer MRP (Rate per Unit) for 3rd Schedule Goods',
    )
    fbr_retail_price_excl = fields.Float(
        string='Retail Value Excl. ST (Unit)',
        digits=(16, 2),
        compute='_compute_fbr_retail_fields',
        store=True,
        readonly=False,
        precompute=True,
        help='Per-unit Retail Value Excluding 18% Sales Tax',
    )
    fbr_retail_unit_tax = fields.Float(
        string='Retail Sales Tax 18% (Unit)',
        digits=(16, 2),
        compute='_compute_fbr_retail_fields',
        store=True,
        readonly=False,
        precompute=True,
        help='Per-unit 18% Sales Tax on Retail Price',
    )
    fbr_line_sales_tax = fields.Float(
        string='Sales Tax (18% Retail)',
        digits=(16, 2),
        compute='_compute_fbr_line_amounts',
        store=True,
        help='Line 18% Sales Tax computed on Retail Price',
    )
    fbr_line_extra_tax = fields.Float(
        string='Extra Tax (4% Retail)',
        digits=(16, 2),
        compute='_compute_fbr_line_amounts',
        store=True,
        help='Line 4% Extra Tax computed on Retail Value Excl. ST',
    )
    fbr_line_value_inc_st = fields.Float(
        string='Value Inc. of ST',
        digits=(16, 2),
        compute='_compute_fbr_line_amounts',
        store=True,
        help='Line Sales Value + Line 18% Sales Tax (before Extra Tax and WHT 236G)',
    )

    @api.depends('product_id')
    def _compute_fbr_retail_fields(self):
        for line in self:
            if not line.product_id:
                if not line.fbr_retail_price_inc and not line.fbr_retail_price_excl:
                    line.fbr_retail_price_inc = 0.0
                    line.fbr_retail_price_excl = 0.0
                    line.fbr_retail_unit_tax = 0.0
                continue
            if not line.fbr_retail_price_inc and not line.fbr_retail_price_excl and not line.fbr_retail_unit_tax:
                inc, excl, tax, _ = _extract_product_retail_vals(line.product_id)
                line.fbr_retail_price_inc = inc
                line.fbr_retail_price_excl = excl
                line.fbr_retail_unit_tax = tax

    @api.onchange('product_id')
    def _onchange_product_id_fbr_retail(self):
        for line in self:
            if line.product_id:
                inc, excl, tax, _ = _extract_product_retail_vals(line.product_id)
                line.fbr_retail_price_inc = inc
                line.fbr_retail_price_excl = excl
                line.fbr_retail_unit_tax = tax

    @api.onchange('fbr_retail_price_inc')
    def _onchange_fbr_retail_price_inc(self):
        for line in self:
            inc = round(line.fbr_retail_price_inc or 0.0, 2)
            if inc <= 0.0:
                line.fbr_retail_price_excl = 0.0
                line.fbr_retail_unit_tax = 0.0
            elif (
                line.fbr_retail_price_excl > 0.0
                and abs(inc - round(line.fbr_retail_price_excl * 1.18, 2)) <= 0.05
                and round(line.fbr_retail_price_excl + line.fbr_retail_unit_tax, 2) == inc
            ):
                continue
            else:
                excl = round(inc / 1.18, 2)
                line.fbr_retail_price_excl = excl
                line.fbr_retail_unit_tax = round(inc - excl, 2)

    @api.onchange('fbr_retail_price_excl')
    def _onchange_fbr_retail_price_excl(self):
        for line in self:
            excl = round(line.fbr_retail_price_excl or 0.0, 2)
            inc = round(line.fbr_retail_price_inc or 0.0, 2)
            if excl <= 0.0:
                line.fbr_retail_unit_tax = 0.0
                line.fbr_retail_price_inc = 0.0
            elif inc > 0.0 and abs(inc - round(excl * 1.18, 2)) <= 0.05:
                line.fbr_retail_unit_tax = round(inc - excl, 2)
            else:
                unit_tax = round(excl * 0.18, 2)
                line.fbr_retail_unit_tax = unit_tax
                line.fbr_retail_price_inc = round(excl + unit_tax, 2)

    @api.onchange('fbr_retail_unit_tax')
    def _onchange_fbr_retail_unit_tax(self):
        for line in self:
            tax = round(line.fbr_retail_unit_tax or 0.0, 2)
            inc = round(line.fbr_retail_price_inc or 0.0, 2)
            excl = round(line.fbr_retail_price_excl or 0.0, 2)
            if tax > 0.0 and inc > 0.0 and abs(inc - round(tax * (1.18 / 0.18), 2)) <= 0.10:
                line.fbr_retail_price_excl = round(inc - tax, 2)
            elif tax > 0.0 and excl > 0.0:
                line.fbr_retail_price_inc = round(excl + tax, 2)

    @api.model_create_multi
    def create(self, vals_list):
        processed = []
        for vals in vals_list:
            vals = dict(vals)
            retail_keys = {'fbr_retail_price_inc', 'fbr_retail_price_excl', 'fbr_retail_unit_tax'}
            if retail_keys & set(vals):
                vals = _compute_retail_breakdown_vals(vals)
                vals.pop('fbr_fixed_notified_value', None)
            elif vals.get('product_id'):
                product = self.env['product.product'].browse(vals['product_id'])
                inc, excl, tax, _ = _extract_product_retail_vals(product)
                if inc or excl or tax:
                    vals.update({
                        'fbr_retail_price_inc': inc,
                        'fbr_retail_price_excl': excl,
                        'fbr_retail_unit_tax': tax,
                    })
            processed.append(vals)
        return super().create(processed)

    def write(self, vals):
        retail_keys = {'fbr_retail_price_inc', 'fbr_retail_price_excl', 'fbr_retail_unit_tax'}
        if retail_keys & set(vals):
            if len(self) == 1:
                new_vals = _compute_retail_breakdown_vals(
                    vals,
                    current_inc=self.fbr_retail_price_inc,
                    current_excl=self.fbr_retail_price_excl,
                    current_tax=self.fbr_retail_unit_tax,
                )
                new_vals.pop('fbr_fixed_notified_value', None)
                return super().write(new_vals)
            res = True
            for rec in self:
                rec_vals = _compute_retail_breakdown_vals(
                    vals,
                    current_inc=rec.fbr_retail_price_inc,
                    current_excl=rec.fbr_retail_price_excl,
                    current_tax=rec.fbr_retail_unit_tax,
                )
                rec_vals.pop('fbr_fixed_notified_value', None)
                res = super(SaleOrderLine, rec).write(rec_vals) and res
            return res
        return super().write(vals)

    def _prepare_base_line_for_taxes_computation(self, **kwargs):
        self.ensure_one()
        kwargs.setdefault('fbr_retail_price_inc', self.fbr_retail_price_inc)
        kwargs.setdefault('fbr_retail_price_excl', self.fbr_retail_price_excl)
        kwargs.setdefault('fbr_retail_unit_tax', self.fbr_retail_unit_tax)
        return super()._prepare_base_line_for_taxes_computation(**kwargs)

    @api.depends(
        'product_uom_qty',
        'discount',
        'price_unit',
        'tax_ids',
        'fbr_retail_price_inc',
        'fbr_retail_price_excl',
        'fbr_retail_unit_tax',
    )
    def _compute_amount(self):
        return super()._compute_amount()

    @api.depends(
        'product_uom_qty',
        'price_subtotal',
        'tax_ids',
        'fbr_retail_price_inc',
        'fbr_retail_price_excl',
        'fbr_retail_unit_tax',
    )
    def _compute_fbr_line_amounts(self):
        for line in self:
            qty = line.product_uom_qty or 0.0
            unit_tax = line.fbr_retail_unit_tax or 0.0
            excl = line.fbr_retail_price_excl or 0.0
            has_st = any(
                t.fbr_tax_type == '3rd_schedule_st' or (t.fbr_tax_type in ('none', False) and t.amount == 18.0)
                for t in line.tax_ids
            )
            extra_taxes = line.tax_ids.filtered(
                lambda t: t.fbr_tax_type == '3rd_schedule_extra'
                or (t.fbr_tax_type in ('none', False) and t.amount == 4.0 and 'further' not in (t.name or '').lower())
            )
            if unit_tax > 0.0 and has_st:
                line_st = round(qty * unit_tax, 2)
            else:
                line_st = round(
                    sum((line.price_subtotal or 0.0) * (t.amount / 100.0) for t in line.tax_ids if t.amount == 18.0),
                    2,
                )
            if excl > 0.0 and extra_taxes:
                line_extra = round(sum(qty * excl * (t.amount / 100.0) for t in extra_taxes), 2)
            else:
                line_extra = 0.0
            line.fbr_line_sales_tax = line_st
            line.fbr_line_extra_tax = line_extra
            line.fbr_line_value_inc_st = round((line.price_subtotal or 0.0) + line_st, 2)

    def _prepare_invoice_line(self, **optional_values):
        res = super()._prepare_invoice_line(**optional_values)
        if not self.display_type:
            inc = self.fbr_retail_price_inc
            excl = self.fbr_retail_price_excl
            tax = self.fbr_retail_unit_tax
            if not inc and not excl and not tax and self.product_id:
                inc, excl, tax, _ = _extract_product_retail_vals(self.product_id)
            res.update({
                'fbr_hs_code': self.product_id.fbr_hs_code or False,
                'fbr_uom_id': self.product_id.fbr_uom_id.id or False,
                'fbr_sro_id': self.product_id.fbr_sro_id.id or False,
                'fbr_sro_item_serial_id': self.product_id.fbr_sro_item_serial_id.id or False,
                'fbr_fixed_notified_value': (
                    f"{inc:g}" if inc else (self.product_id.fbr_fixed_notified_value or '0')
                ),
                'fbr_retail_price_inc': inc,
                'fbr_retail_price_excl': excl,
                'fbr_retail_unit_tax': tax,
            })
        return res

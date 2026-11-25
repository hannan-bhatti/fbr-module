# -*- coding: utf-8 -*-

import re
from odoo import models, fields, api
from odoo.exceptions import ValidationError
from .product_template import (
    _parse_float_str,
    _compute_retail_breakdown_vals,
    _is_fbr_3rd_schedule,
    _is_explicit_non_3rd_schedule_scenario,
)
from .sale_order_line import _extract_product_retail_vals


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    fbr_uom_id = fields.Many2one(
        'fbr.uom',
        string='FBR UOM',
        compute='_compute_fbr_product_metadata',
        store=True,
        readonly=False,
        help="Select the FBR unit of measurement for this product"
    )

    fbr_hs_code = fields.Char(
        string='HS Code',
        compute='_compute_fbr_product_metadata',
        store=True,
        readonly=False,
        help="Harmonized System Code for FBR reporting"
    )

    fbr_sro_item_serial_id = fields.Many2one(
        'fbr.sro.item.serial',
        string='SRO Item Serial No.',
        compute='_compute_fbr_product_metadata',
        store=True,
        readonly=False,
        help='Select the SRO Item Serial Number'
    )

    fbr_sro_id = fields.Many2one(
        'fbr.sro',
        string='FBR SRO',
        compute='_compute_fbr_product_metadata',
        store=True,
        readonly=False,
        help='Select the applicable SRO (Statutory Regulatory Order)'
    )

    fbr_fixed_notified_value = fields.Char(
        string='Fixed/Notified Value or Retail Price',
        compute='_compute_fbr_retail_fields',
        store=True,
        readonly=False,
        precompute=True,
        help='Fixed or notified value or retail price for FBR'
    )

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

    fbr_sales_tax_withheld_id = fields.Many2one(
        'account.tax',
        string='Sales Tax Withheld at Source',
        domain="[('type_tax_use', '=', 'sale')]",
        help='Sales tax withheld at source'
    )
    fbr_extra_tax_id = fields.Many2one(
        'account.tax',
        string='Extra Tax',
        domain="[('type_tax_use', '=', 'sale')]",
        help='Extra tax for FBR'
    )
    fbr_further_tax_id = fields.Many2one(
        'account.tax',
        string='Further Tax',
        domain="[('type_tax_use', '=', 'sale')]",
        help='Further tax for FBR'
    )
    fbr_fed_payable_id = fields.Many2one(
        'account.tax',
        related='product_id.fbr_fed_payable_id',
        store=True,
        readonly=False,
        string='FED Payable',
        help='Federal Excise Duty payable'
    )

    @api.depends('product_id')
    def _compute_fbr_product_metadata(self):
        for line in self:
            if not line.product_id:
                continue
            if not line.fbr_uom_id:
                line.fbr_uom_id = line.product_id.fbr_uom_id
            if not line.fbr_hs_code:
                line.fbr_hs_code = line.product_id.fbr_hs_code
            if not line.fbr_sro_id:
                line.fbr_sro_id = line.product_id.fbr_sro_id
            if not line.fbr_sro_item_serial_id:
                line.fbr_sro_item_serial_id = line.product_id.fbr_sro_item_serial_id

    def _is_fbr_3rd_schedule_line(self, include_retail=True):
        """Return True if this invoice line should use 3rd Schedule retail tax calculation.
        Crucially, default SN001/SN002 on a newly created invoice does NOT block 3rd Schedule
        lines coming from a Sale Order or manually entered by the user.
        """
        if not self:
            return False
        self.ensure_one()
        move = self.move_id
        if move and move._is_fbr_explicit_3rd_schedule_move():
            return True
        if move and _is_explicit_non_3rd_schedule_scenario(move.fbr_scenario_id):
            return False

        sro_name = (
            self.fbr_sro_id.name
            if self.fbr_sro_id
            else (self.product_id.fbr_sro_name if self.product_id else '')
        )
        return _is_fbr_3rd_schedule(
            scenario_code=move.fbr_scenario_id.code if (move and move.fbr_scenario_id) else '',
            sale_type_name=move.fbr_sale_type_id.name if (move and move.fbr_sale_type_id) else '',
            sro_name=sro_name,
            tax_ids=self.tax_ids,
            retail_unit_tax=self.fbr_retail_unit_tax or (self.product_id.fbr_retail_unit_tax if self.product_id else 0.0),
            retail_excl=self.fbr_retail_price_excl or (self.product_id.fbr_retail_price_excl if self.product_id else 0.0),
            retail_inc=self.fbr_retail_price_inc or (self.product_id.fbr_retail_price_inc if self.product_id else 0.0),
            include_retail=include_retail,
        )

    def _vals_is_fbr_3rd_schedule(self, vals, rec=None):
        """Check whether create/write vals on account.move.line represent a 3rd Schedule line."""
        if any(
            float(vals.get(k) or 0.0) > 0.0
            for k in ('fbr_retail_price_inc', 'fbr_retail_price_excl', 'fbr_retail_unit_tax')
        ):
            return True

        move = (
            self.env['account.move'].browse(vals['move_id'])
            if vals.get('move_id')
            else (rec.move_id if rec else self.env['account.move'])
        )
        if move and move._is_fbr_explicit_3rd_schedule_move():
            return True
        if move and _is_explicit_non_3rd_schedule_scenario(move.fbr_scenario_id):
            return False

        if vals.get('fbr_sro_id'):
            sro = self.env['fbr.sro'].browse(vals['fbr_sro_id'])
            sro_name = (sro.name or '').strip().lower()
            if sro_name == '3rd schedule goods':
                return True
            if sro_name:
                return False

        product = (
            self.env['product.product'].browse(vals['product_id'])
            if vals.get('product_id')
            else (rec.product_id if rec else self.env['product.product'])
        )
        if product:
            sro_name = (product.fbr_sro_name or '').strip().lower()
            if sro_name == '3rd schedule goods':
                return True
            if sro_name:
                return False
            if product.fbr_retail_price_inc > 0.0 or product.fbr_retail_price_excl > 0.0 or product.fbr_retail_unit_tax > 0.0:
                return True

        if rec and rec._is_fbr_3rd_schedule_line():
            return True
        return False

    @api.depends('product_id')
    def _compute_fbr_retail_fields(self):
        for line in self:
            if line.display_type and line.display_type != 'product':
                line.fbr_retail_price_inc = 0.0
                line.fbr_retail_price_excl = 0.0
                line.fbr_retail_unit_tax = 0.0
                line.fbr_fixed_notified_value = '0'
                continue

            # If retail values are already present (e.g. transferred from sale.order.line
            # or manually typed on the line), fill any missing sibling breakdown field and keep them!
            inc = round(line.fbr_retail_price_inc or 0.0, 2)
            excl = round(line.fbr_retail_price_excl or 0.0, 2)
            tax = round(line.fbr_retail_unit_tax or 0.0, 2)
            if inc or excl or tax:
                if inc > 0.0 and tax > 0.0 and not excl and tax < inc:
                    excl = round(inc - tax, 2)
                elif inc > 0.0 and not excl and not tax:
                    excl = round(inc / 1.18, 2)
                    tax = round(inc - excl, 2)
                elif excl > 0.0 and not tax:
                    if inc > 0.0 and abs(inc - round(excl * 1.18, 2)) <= 0.05:
                        tax = round(inc - excl, 2)
                    else:
                        tax = round(excl * 0.18, 2)
                        inc = round(excl + tax, 2)
                line.fbr_retail_price_inc = inc
                line.fbr_retail_price_excl = excl
                line.fbr_retail_unit_tax = tax
                if inc > 0.0:
                    line.fbr_fixed_notified_value = f"{inc:g}"
                continue

            if not line._is_fbr_3rd_schedule_line():
                if not line.fbr_fixed_notified_value:
                    line.fbr_fixed_notified_value = (
                        line.product_id.fbr_fixed_notified_value or '0'
                        if line.product_id
                        else '0'
                    )
                line.fbr_retail_price_inc = 0.0
                line.fbr_retail_price_excl = 0.0
                line.fbr_retail_unit_tax = 0.0
                continue

            if line.move_id:
                inc, excl, tax, fixed_str = line.move_id._resolve_line_3rd_schedule_retail(line)
            elif line.product_id:
                inc, excl, tax, fixed_str = _extract_product_retail_vals(line.product_id, force_3rd_schedule=True)
            else:
                inc, excl, tax, fixed_str = 0.0, 0.0, 0.0, '0'
            line.fbr_retail_price_inc = inc
            line.fbr_retail_price_excl = excl
            line.fbr_retail_unit_tax = tax
            line.fbr_fixed_notified_value = fixed_str

    def _trigger_live_move_tax_recompute(self):
        """Recompute line totals and parent move tax totals immediately during UI onchange."""
        for line in self:
            line._compute_totals()
            line._compute_fbr_line_amounts()
            if line.move_id:
                if (
                    (line.fbr_retail_price_inc > 0.0 or line.fbr_retail_price_excl > 0.0 or line.fbr_retail_unit_tax > 0.0)
                    and not _is_explicit_non_3rd_schedule_scenario(line.move_id.fbr_scenario_id)
                ):
                    line.move_id._ensure_sn008_scenario_if_default()
                line.move_id._compute_tax_totals()

    @api.onchange('product_id')
    def _onchange_product_id_fbr_fields(self):
        """Auto-populate FBR fields when product is selected"""
        for line in self:
            if line.product_id:
                line.fbr_uom_id = line.product_id.fbr_uom_id
                line.fbr_hs_code = line.product_id.fbr_hs_code
                line.fbr_sro_id = line.product_id.fbr_sro_id
                line.fbr_sro_item_serial_id = line.product_id.fbr_sro_item_serial_id
                if line._is_fbr_3rd_schedule_line():
                    inc, excl, tax, fixed_str = _extract_product_retail_vals(
                        line.product_id, force_3rd_schedule=True
                    )
                    line.fbr_retail_price_inc = inc
                    line.fbr_retail_price_excl = excl
                    line.fbr_retail_unit_tax = tax
                    line.fbr_fixed_notified_value = fixed_str
                else:
                    line.fbr_retail_price_inc = 0.0
                    line.fbr_retail_price_excl = 0.0
                    line.fbr_retail_unit_tax = 0.0
                    line.fbr_fixed_notified_value = line.product_id.fbr_fixed_notified_value or '0'
        self._trigger_live_move_tax_recompute()

    @api.onchange('fbr_retail_price_inc')
    def _onchange_fbr_retail_price_inc(self):
        for line in self:
            inc = round(line.fbr_retail_price_inc or 0.0, 2)
            line.fbr_fixed_notified_value = f"{inc:g}" if inc else '0'
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
        self._trigger_live_move_tax_recompute()

    @api.onchange('fbr_fixed_notified_value')
    def _onchange_fbr_fixed_notified_value(self):
        for line in self:
            if not line._is_fbr_3rd_schedule_line():
                line.fbr_retail_price_inc = 0.0
                line.fbr_retail_price_excl = 0.0
                line.fbr_retail_unit_tax = 0.0
                continue
            val = round(_parse_float_str(line.fbr_fixed_notified_value), 2)
            if abs(val - (line.fbr_retail_price_inc or 0.0)) > 0.001:
                line.fbr_retail_price_inc = val
                line._onchange_fbr_retail_price_inc()

    @api.onchange('fbr_retail_price_excl')
    def _onchange_fbr_retail_price_excl(self):
        for line in self:
            excl = round(line.fbr_retail_price_excl or 0.0, 2)
            inc = round(line.fbr_retail_price_inc or 0.0, 2)
            if excl <= 0.0:
                line.fbr_retail_unit_tax = 0.0
                line.fbr_retail_price_inc = 0.0
                line.fbr_fixed_notified_value = '0'
            elif inc > 0.0 and abs(inc - round(excl * 1.18, 2)) <= 0.05:
                line.fbr_retail_unit_tax = round(inc - excl, 2)
            else:
                unit_tax = round(excl * 0.18, 2)
                line.fbr_retail_unit_tax = unit_tax
                line.fbr_retail_price_inc = round(excl + unit_tax, 2)
                line.fbr_fixed_notified_value = f"{line.fbr_retail_price_inc:g}"
        self._trigger_live_move_tax_recompute()

    @api.onchange('fbr_retail_unit_tax')
    def _onchange_fbr_retail_unit_tax(self):
        for line in self:
            tax = round(line.fbr_retail_unit_tax or 0.0, 2)
            inc = round(line.fbr_retail_price_inc or 0.0, 2)
            excl = round(line.fbr_retail_price_excl or 0.0, 2)
            if tax > 0.0 and inc > 0.0 and tax < inc:
                line.fbr_retail_price_excl = round(inc - tax, 2)
            elif tax > 0.0 and excl > 0.0:
                line.fbr_retail_price_inc = round(excl + tax, 2)
                line.fbr_fixed_notified_value = f"{line.fbr_retail_price_inc:g}"
            elif tax > 0.0:
                excl_calc = round(tax / 0.18, 2)
                line.fbr_retail_price_excl = excl_calc
                line.fbr_retail_price_inc = round(excl_calc + tax, 2)
                line.fbr_fixed_notified_value = f"{line.fbr_retail_price_inc:g}"
        self._trigger_live_move_tax_recompute()

    @api.model_create_multi
    def create(self, vals_list):
        processed = []
        retail_keys = {'fbr_retail_price_inc', 'fbr_retail_price_excl', 'fbr_retail_unit_tax', 'fbr_fixed_notified_value'}
        for vals in vals_list:
            vals = dict(vals)
            if vals.get('display_type') in (None, False, 'product'):
                is_3rd = self._vals_is_fbr_3rd_schedule(vals)
                if is_3rd:
                    if retail_keys & set(vals):
                        vals = _compute_retail_breakdown_vals(vals, sync_legacy_to_retail=True)
                    elif vals.get('product_id'):
                        product = self.env['product.product'].browse(vals['product_id'])
                        inc, excl, tax, fixed_str = _extract_product_retail_vals(
                            product, force_3rd_schedule=True
                        )
                        if inc or excl or tax:
                            vals.update({
                                'fbr_retail_price_inc': inc,
                                'fbr_retail_price_excl': excl,
                                'fbr_retail_unit_tax': tax,
                                'fbr_fixed_notified_value': fixed_str,
                            })
                else:
                    vals['fbr_retail_price_inc'] = 0.0
                    vals['fbr_retail_price_excl'] = 0.0
                    vals['fbr_retail_unit_tax'] = 0.0
                    if 'fbr_fixed_notified_value' not in vals and vals.get('product_id'):
                        product = self.env['product.product'].browse(vals['product_id'])
                        if product.fbr_fixed_notified_value:
                            vals['fbr_fixed_notified_value'] = product.fbr_fixed_notified_value
            processed.append(vals)
        lines = super().create(processed)
        for move in lines.move_id:
            if move and move.state == 'draft' and move.is_invoice(include_receipts=True):
                if any(
                    l.fbr_retail_price_inc > 0.0 or l.fbr_retail_price_excl > 0.0 or l.fbr_retail_unit_tax > 0.0
                    for l in move.invoice_line_ids
                    if l.display_type in (False, 'product')
                ):
                    move._ensure_sn008_scenario_if_default()
        return lines

    def write(self, vals):
        retail_keys = {'fbr_retail_price_inc', 'fbr_retail_price_excl', 'fbr_retail_unit_tax', 'fbr_fixed_notified_value'}
        has_retail_edit = bool((retail_keys & set(vals)) and not self.env.context.get('fbr_skip_retail_sync'))
        if has_retail_edit:
            if len(self) == 1:
                is_3rd = self._vals_is_fbr_3rd_schedule(vals, rec=self)
                if is_3rd:
                    new_vals = _compute_retail_breakdown_vals(
                        vals,
                        current_inc=self.fbr_retail_price_inc,
                        current_excl=self.fbr_retail_price_excl,
                        current_tax=self.fbr_retail_unit_tax,
                        sync_legacy_to_retail=True,
                    )
                else:
                    new_vals = dict(vals)
                    if 'fbr_fixed_notified_value' in new_vals:
                        new_vals['fbr_retail_price_inc'] = 0.0
                        new_vals['fbr_retail_price_excl'] = 0.0
                        new_vals['fbr_retail_unit_tax'] = 0.0
                res = super(AccountMoveLine, self.with_context(fbr_skip_retail_sync=True)).write(new_vals)
            else:
                res = True
                for rec in self:
                    is_3rd = self._vals_is_fbr_3rd_schedule(vals, rec=rec)
                    if is_3rd:
                        rec_vals = _compute_retail_breakdown_vals(
                            vals,
                            current_inc=rec.fbr_retail_price_inc,
                            current_excl=rec.fbr_retail_price_excl,
                            current_tax=rec.fbr_retail_unit_tax,
                            sync_legacy_to_retail=True,
                        )
                    else:
                        rec_vals = dict(vals)
                        if 'fbr_fixed_notified_value' in rec_vals:
                            rec_vals['fbr_retail_price_inc'] = 0.0
                            rec_vals['fbr_retail_price_excl'] = 0.0
                            rec_vals['fbr_retail_unit_tax'] = 0.0
                    res = super(AccountMoveLine, rec.with_context(fbr_skip_retail_sync=True)).write(rec_vals) and res

            if not self.env.context.get('fbr_recomputing_taxes'):
                draft_moves = self.move_id.filtered(
                    lambda m: m.state == 'draft' and m.move_type in ('out_invoice', 'out_refund', 'out_receipt')
                )
                for move in draft_moves:
                    move._ensure_sn008_scenario_if_default()
                if draft_moves:
                    draft_moves.with_context(fbr_recomputing_taxes=True).action_recompute_fbr_taxes()
            return res
        return super().write(vals)

    @api.depends(
        'quantity',
        'discount',
        'price_unit',
        'tax_ids',
        'currency_id',
        'amount_currency',
        'move_id.fbr_scenario_id',
        'fbr_retail_price_inc',
        'fbr_retail_price_excl',
        'fbr_retail_unit_tax',
        'fbr_fixed_notified_value',
    )
    def _compute_totals(self):
        return super()._compute_totals()

    @api.depends(
        'quantity',
        'price_subtotal',
        'tax_ids',
        'fbr_extra_tax_id',
        'fbr_sro_id',
        'move_id.fbr_scenario_id',
        'move_id.fbr_sale_type_id',
        'fbr_retail_price_inc',
        'fbr_retail_price_excl',
        'fbr_retail_unit_tax',
        'fbr_fixed_notified_value',
    )
    def _compute_fbr_line_amounts(self):
        for line in self:
            if line.display_type not in ('product', 'cogs', 'non_deductible_product', 'non_deductible_product_total') or not line.move_id:
                line.fbr_line_sales_tax = 0.0
                line.fbr_line_extra_tax = 0.0
                line.fbr_line_value_inc_st = 0.0
                continue
            qty = line.quantity or 0.0
            is_3rd = line._is_fbr_3rd_schedule_line()
            if is_3rd:
                _, excl, unit_tax, _ = line.move_id._resolve_line_3rd_schedule_retail(line)
            else:
                excl = 0.0
                unit_tax = 0.0

            has_st = any(
                t.fbr_tax_type == '3rd_schedule_st' or (t.fbr_tax_type in ('none', False) and t.amount == 18.0)
                for t in line.tax_ids
            )
            extra_taxes = line.tax_ids.filtered(
                lambda t: t.fbr_tax_type == '3rd_schedule_extra'
                or (is_3rd and t.fbr_tax_type in ('none', False) and t.amount == 4.0 and 'further' not in (t.name or '').lower())
            )
            if not extra_taxes and line.fbr_extra_tax_id:
                extra_taxes = line.fbr_extra_tax_id

            if is_3rd and unit_tax > 0.0 and has_st:
                line_st = round(qty * unit_tax, 2)
            else:
                line_st = round(
                    sum((line.price_subtotal or 0.0) * (t.amount / 100.0) for t in line.tax_ids if t.amount == 18.0),
                    2,
                )
            if is_3rd and excl > 0.0 and extra_taxes:
                line_extra = round(sum(qty * excl * (t.amount / 100.0) for t in extra_taxes), 2)
            else:
                line_extra = 0.0

            line.fbr_line_sales_tax = line_st
            line.fbr_line_extra_tax = line_extra
            line.fbr_line_value_inc_st = round((line.price_subtotal or 0.0) + line_st, 2)

    @api.constrains('fbr_hs_code')
    def _check_fbr_hs_code(self):
        """Validate HS Code format"""
        for record in self:
            if record.fbr_hs_code:
                hs_code = record.fbr_hs_code.replace(' ', '')
                if not re.match(r'^\d{4}\.\d{4}$', hs_code):
                    raise ValidationError(
                        "HS Code must be in format XXXX.XXXX (e.g., 0101.2100). "
                        "It should contain exactly 8 digits with a dot after the first 4 digits."
                    )

    @api.onchange('fbr_sales_tax_withheld_id', 'fbr_extra_tax_id', 'fbr_further_tax_id', 'fbr_fed_payable_id')
    def _onchange_fbr_taxes(self):
        for line in self:
            tax_ids = line.tax_ids or self.env['account.tax'].browse()
            fbr_taxes = filter(None, [
                line.fbr_sales_tax_withheld_id,
                line.fbr_extra_tax_id,
                line.fbr_further_tax_id,
                line.fbr_fed_payable_id
            ])
            for tax in fbr_taxes:
                if tax not in tax_ids:
                    tax_ids |= tax
            line.tax_ids = tax_ids

    @api.onchange('product_id')
    def _onchange_set_default_fbr_further_tax(self):
        for rec in self:
            buyer_type = rec.move_id.partner_id.fbr_buyer_type_id
            tax_obj = self.env['account.tax']

            if rec.product_id.fbr_sro_name == '3rd Schedule goods' or rec._is_fbr_3rd_schedule_line():
                rec.fbr_further_tax_id = False
            elif buyer_type and buyer_type.name == 'Unregistered':
                tax = tax_obj.search([('amount', '=', 4.0), ('type_tax_use', '=', 'sale')], limit=1)
                if not tax:
                    tax = tax_obj.create({
                        'name': '4%',
                        'amount': 4.0,
                        'type_tax_use': 'sale',
                        'amount_type': 'percent',
                    })
                rec.fbr_further_tax_id = tax
            else:
                rec.fbr_further_tax_id = False

            rec._onchange_fbr_taxes()

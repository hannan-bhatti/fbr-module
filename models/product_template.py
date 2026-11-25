# -*- coding: utf-8 -*-

import re
from odoo import models, fields, api
from odoo.exceptions import ValidationError


def _parse_float_str(val):
    if not val:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    cleaned = str(val).replace(',', '').strip()
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return 0.0


FBR_3RD_SCHEDULE_SCENARIOS = ('SN008', 'SN027')
# Scenarios that are explicitly non-3rd-schedule (note: SN001/SN002 are default standard scenarios
# on new invoices and must NOT block 3rd Schedule lines when a 3rd Schedule product or retail price is used).
FBR_EXPLICIT_NON_3RD_SCENARIOS = (
    'SN003', 'SN004', 'SN005', 'SN006', 'SN007', 'SN009', 'SN010',
    'SN011', 'SN012', 'SN013', 'SN014', 'SN015', 'SN016', 'SN017',
    'SN018', 'SN019', 'SN020', 'SN021', 'SN022', 'SN023', 'SN024',
    'SN025', 'SN028',
)


def _is_3rd_schedule_scenario(scenario):
    """Return True when a 3rd Schedule scenario (SN008 / SN027) is selected."""
    if not scenario:
        return False
    code = (getattr(scenario, 'code', '') or '').strip().upper()
    if code in FBR_3RD_SCHEDULE_SCENARIOS:
        return True
    name = (getattr(scenario, 'name', '') or '').strip().lower()
    if '3rd schedule' in name or 'third schedule' in name:
        return True
    return False


def _is_explicit_non_3rd_schedule_scenario(scenario):
    """Return True only when an invoice scenario is explicitly set to a non-3rd-schedule
    special scenario (e.g. SN005 Reduced Rate, SN006 Exempt, SN007 Zero Rated).
    Default SN001/SN002 returns False so invoices created from Sale Orders don't lose 3rd Schedule data.
    """
    if not scenario:
        return False
    code = (getattr(scenario, 'code', '') or '').strip().upper()
    return code in FBR_EXPLICIT_NON_3RD_SCENARIOS


def _is_product_3rd_schedule(product):
    """Return True if a product.template/product.product is configured for 3rd Schedule."""
    if not product:
        return False
    sro_name = (
        getattr(product, 'fbr_sro_name', '')
        or (product.fbr_sro_id.name if getattr(product, 'fbr_sro_id', False) else '')
        or ''
    ).strip().lower()
    if sro_name == '3rd schedule goods':
        return True
    if sro_name and sro_name != '3rd schedule goods':
        return False
    taxes = getattr(product, 'taxes_id', False)
    if taxes and any(
        getattr(t, 'fbr_tax_type', None) in ('3rd_schedule_st', '3rd_schedule_extra')
        for t in taxes
    ):
        return True
    if (
        (getattr(product, 'fbr_retail_price_inc', 0.0) or 0.0) > 0.0
        or (getattr(product, 'fbr_retail_price_excl', 0.0) or 0.0) > 0.0
        or (getattr(product, 'fbr_retail_unit_tax', 0.0) or 0.0) > 0.0
    ):
        return True
    return False


def _is_fbr_3rd_schedule(
    scenario_code='',
    sale_type_name='',
    sro_name='',
    tax_ids=None,
    retail_unit_tax=0.0,
    retail_excl=0.0,
    retail_inc=0.0,
    include_retail=True,
):
    """Determine whether an invoice/order line should use 3rd Schedule (18% retail) calculation."""
    scenario = (scenario_code or '').strip().upper()
    sale_type = (sale_type_name or '').strip().lower()
    sro = (sro_name or '').strip().lower()
    taxes = tax_ids or ()

    if (
        scenario in FBR_3RD_SCHEDULE_SCENARIOS
        or sale_type == '3rd schedule goods'
        or sro == '3rd schedule goods'
        or any(
            getattr(t, 'fbr_tax_type', None) in ('3rd_schedule_st', '3rd_schedule_extra')
            for t in taxes
        )
    ):
        return True

    # If the user entered retail breakdown values on the line, honor them unless an explicit
    # non-3rd-schedule SRO (like Eighth Schedule) or scenario (like SN005) is set.
    if sro and sro != '3rd schedule goods':
        return False
    if scenario in FBR_EXPLICIT_NON_3RD_SCENARIOS:
        return False

    if include_retail and (retail_unit_tax > 0.0 or retail_excl > 0.0 or retail_inc > 0.0):
        return True

    return False


def _compute_retail_breakdown_vals(
    vals,
    current_inc=0.0,
    current_excl=0.0,
    current_tax=0.0,
    sync_legacy_to_retail=True,
):
    """Synchronize fbr_retail_price_inc, fbr_retail_price_excl, fbr_retail_unit_tax,
    and legacy fbr_fixed_notified_value across Top-Down, Bottom-Up, and Manual Override modes.
    """
    vals = dict(vals)
    has_inc = 'fbr_retail_price_inc' in vals
    has_excl = 'fbr_retail_price_excl' in vals
    has_tax = 'fbr_retail_unit_tax' in vals
    has_legacy = 'fbr_fixed_notified_value' in vals

    if has_legacy and not (has_inc or has_excl or has_tax):
        if not sync_legacy_to_retail:
            vals['fbr_retail_price_inc'] = 0.0
            vals['fbr_retail_price_excl'] = 0.0
            vals['fbr_retail_unit_tax'] = 0.0
            return vals
        inc = round(_parse_float_str(vals.get('fbr_fixed_notified_value')), 2)
        vals['fbr_retail_price_inc'] = inc
        has_inc = True

    if has_inc and has_excl:
        inc = round(float(vals.get('fbr_retail_price_inc') or 0.0), 2)
        excl = round(float(vals.get('fbr_retail_price_excl') or 0.0), 2)
        tax_in_vals = round(float(vals.get('fbr_retail_unit_tax') or 0.0), 2) if has_tax else 0.0

        if inc > 0.0 and excl > 0.0:
            vals['fbr_retail_price_inc'] = inc
            # Check if the user manually edited fbr_retail_unit_tax (e.g. changed 305.08 -> 305.09)
            # while fbr_retail_price_excl still had the old value (1694.92)
            if (
                has_tax
                and tax_in_vals > 0.0
                and round(inc - excl, 2) != tax_in_vals
                and abs(excl - current_excl) < 0.001
                and abs(tax_in_vals - current_tax) >= 0.005
                and abs(inc - round(tax_in_vals * (1.18 / 0.18), 2)) <= 0.15
            ):
                excl = round(inc - tax_in_vals, 2)
                vals['fbr_retail_price_excl'] = excl
                vals['fbr_retail_unit_tax'] = tax_in_vals
            else:
                vals['fbr_retail_price_excl'] = excl
                if not has_tax or round(inc - excl, 2) != tax_in_vals:
                    vals['fbr_retail_unit_tax'] = round(inc - excl, 2)
                else:
                    vals['fbr_retail_unit_tax'] = tax_in_vals
        elif excl > 0.0 and inc == 0.0:
            tax = tax_in_vals if (has_tax and tax_in_vals > 0.0) else round(excl * 0.18, 2)
            vals['fbr_retail_price_excl'] = excl
            vals['fbr_retail_unit_tax'] = tax
            vals['fbr_retail_price_inc'] = round(excl + tax, 2)
        elif inc > 0.0 and excl == 0.0:
            if has_tax and tax_in_vals > 0.0 and tax_in_vals < inc:
                excl = round(inc - tax_in_vals, 2)
                tax = tax_in_vals
            else:
                excl = round(inc / 1.18, 2)
                tax = round(inc - excl, 2)
            vals['fbr_retail_price_inc'] = inc
            vals['fbr_retail_price_excl'] = excl
            vals['fbr_retail_unit_tax'] = tax
        else:
            vals['fbr_retail_price_inc'] = 0.0
            vals['fbr_retail_price_excl'] = 0.0
            vals['fbr_retail_unit_tax'] = 0.0

    elif has_excl and not has_inc:
        excl = round(float(vals.get('fbr_retail_price_excl') or 0.0), 2)
        vals['fbr_retail_price_excl'] = excl
        if excl <= 0.0:
            vals['fbr_retail_unit_tax'] = 0.0
            vals['fbr_retail_price_inc'] = 0.0
        elif current_inc > 0.0 and abs(current_inc - round(excl * 1.18, 2)) <= 0.05:
            # Manual override on an existing MRP (e.g. 2000.00 -> 1694.91 => tax 305.09)
            vals['fbr_retail_unit_tax'] = round(current_inc - excl, 2)
            vals['fbr_retail_price_inc'] = current_inc
        else:
            # Bottom-up mode (e.g. 1059.40 => tax 190.69, inc 1250.09)
            tax = (
                round(float(vals['fbr_retail_unit_tax']), 2)
                if (has_tax and float(vals.get('fbr_retail_unit_tax') or 0.0) > 0.0)
                else round(excl * 0.18, 2)
            )
            vals['fbr_retail_unit_tax'] = tax
            vals['fbr_retail_price_inc'] = round(excl + tax, 2)

    elif has_inc and not has_excl:
        inc = round(float(vals.get('fbr_retail_price_inc') or 0.0), 2)
        vals['fbr_retail_price_inc'] = inc
        tax_in_vals = round(float(vals.get('fbr_retail_unit_tax') or 0.0), 2) if has_tax else 0.0
        if inc <= 0.0:
            vals['fbr_retail_price_excl'] = 0.0
            vals['fbr_retail_unit_tax'] = 0.0
        elif has_tax and tax_in_vals > 0.0 and tax_in_vals < inc:
            vals['fbr_retail_unit_tax'] = tax_in_vals
            vals['fbr_retail_price_excl'] = round(inc - tax_in_vals, 2)
        elif (
            current_excl > 0.0
            and abs(inc - round(current_excl * 1.18, 2)) <= 0.05
            and round(current_excl + current_tax, 2) == inc
        ):
            vals['fbr_retail_price_excl'] = current_excl
            vals['fbr_retail_unit_tax'] = current_tax
        else:
            # Top-down mode (e.g. 830.00 => excl 703.39, tax 126.61)
            excl = round(inc / 1.18, 2)
            tax = round(inc - excl, 2)
            vals['fbr_retail_price_excl'] = excl
            vals['fbr_retail_unit_tax'] = tax

    elif has_tax and not has_inc and not has_excl:
        tax = round(float(vals.get('fbr_retail_unit_tax') or 0.0), 2)
        vals['fbr_retail_unit_tax'] = tax
        if tax <= 0.0:
            if current_inc <= 0.0:
                vals['fbr_retail_price_excl'] = 0.0
        elif current_inc > 0.0 and tax < current_inc:
            vals['fbr_retail_price_excl'] = round(current_inc - tax, 2)
        elif current_excl > 0.0:
            vals['fbr_retail_price_inc'] = round(current_excl + tax, 2)
        else:
            excl = round(tax / 0.18, 2)
            vals['fbr_retail_price_excl'] = excl
            vals['fbr_retail_price_inc'] = round(excl + tax, 2)

    if 'fbr_retail_price_inc' in vals and not has_legacy:
        inc_val = vals['fbr_retail_price_inc']
        if inc_val or sync_legacy_to_retail:
            vals['fbr_fixed_notified_value'] = f"{inc_val:g}" if inc_val else '0'

    return vals


def _vals_is_product_3rd_schedule(env, vals, rec=None):
    """Determine if a product create/write payload targets a 3rd Schedule product."""
    if any(
        float(vals.get(k) or 0.0) > 0.0
        for k in ('fbr_retail_price_inc', 'fbr_retail_price_excl', 'fbr_retail_unit_tax')
    ):
        return True
    if 'fbr_sro_id' in vals:
        sro_id = vals.get('fbr_sro_id')
        if sro_id:
            sro = env['fbr.sro'].browse(sro_id)
            sro_name = (sro.name or '').strip().lower()
            if sro_name == '3rd schedule goods':
                return True
            if sro_name:
                return False
    elif rec:
        sro_name = (rec.fbr_sro_name or '').strip().lower()
        if sro_name == '3rd schedule goods':
            return True
        if sro_name:
            return False
    if rec and _is_product_3rd_schedule(rec):
        return True
    return False


class ProductTemplate(models.Model):
    _inherit = "product.template"

    fbr_image = fields.Image(
        string='FBR Product Image',
        help="Product image for FBR reporting and documentation"
    )

    fbr_uom_id = fields.Many2one(
        'fbr.uom',
        string='FBR UOM',
        help="Select the FBR unit of measurement for this product"
    )
    fbr_hs_code = fields.Char(
        string='HS Code',
        help="Harmonized System Code for FBR reporting"
    )

    fbr_sro_item_serial_id = fields.Many2one(
        'fbr.sro.item.serial',
        string='SRO Serial No.',
        help='Select the SRO Item Serial Number'
    )

    fbr_fixed_notified_value = fields.Char(
        string='Fixed/Notified Value or Retail Price',
        default='0',
        help='Fixed or notified value or retail price for FBR'
    )

    fbr_retail_price_inc = fields.Float(
        string='Retail Price (Inc. 18% ST)',
        digits=(16, 2),
        default=0.0,
        help='Tax-inclusive Consumer MRP (Rate per Unit) for 3rd Schedule Goods'
    )

    fbr_retail_price_excl = fields.Float(
        string='Retail Value Excl. ST (Unit)',
        digits=(16, 2),
        default=0.0,
        help='Per-unit Retail Value Excluding 18% Sales Tax (editable for Bottom-Up or 2,000 Tin override)'
    )

    fbr_retail_unit_tax = fields.Float(
        string='Retail Sales Tax 18% (Unit)',
        digits=(16, 2),
        default=0.0,
        help='Per-unit 18% Sales Tax on Retail Price'
    )

    fbr_sales_tax_withheld = fields.Char(
        string='Sales Tax Withheld at Source',
        default='0',
        help='Sales tax withheld at source amount'
    )

    fbr_sro_id = fields.Many2one(
        'fbr.sro',
        string='FBR SRO - Schedule',
        help='Select the applicable SRO (Statutory Regulatory Order)'
    )

    fbr_sro_name = fields.Char(
        string='SRO Name',
        related='fbr_sro_id.name',
        store=True,
        help='Name of the SRO for reference'
    )

    fbr_extra_tax = fields.Char(
        string='Extra Tax',
        default='0.00',
        help='Extra tax amount for FBR'
    )

    fbr_further_tax = fields.Char(
        string='Further Tax',
        default='0',
        help='Further tax amount for FBR'
    )

    fbr_fed_payable_id = fields.Many2one(
        'account.tax',
        string='FED Payable',
        domain="[('type_tax_use', '=', 'sale')]",
        help='Federal Excise Duty payable'
    )

    @api.onchange('fbr_sro_id')
    def _onchange_fbr_sro_id(self):
        for rec in self:
            sro_name = (rec.fbr_sro_id.name if rec.fbr_sro_id else '').strip().lower()
            if sro_name and sro_name != '3rd schedule goods':
                rec.fbr_retail_price_inc = 0.0
                rec.fbr_retail_price_excl = 0.0
                rec.fbr_retail_unit_tax = 0.0
            elif sro_name == '3rd schedule goods' and not rec.fbr_retail_price_inc and rec.fbr_fixed_notified_value:
                val = round(_parse_float_str(rec.fbr_fixed_notified_value), 2)
                if val > 0.0:
                    rec.fbr_retail_price_inc = val
                    rec._onchange_fbr_retail_price_inc()

    @api.onchange('fbr_retail_price_inc')
    def _onchange_fbr_retail_price_inc(self):
        for rec in self:
            inc = round(rec.fbr_retail_price_inc or 0.0, 2)
            if inc > 0.0 or _is_product_3rd_schedule(rec):
                rec.fbr_fixed_notified_value = f"{inc:g}" if inc else '0'
            if inc <= 0.0:
                rec.fbr_retail_price_excl = 0.0
                rec.fbr_retail_unit_tax = 0.0
            elif (
                rec.fbr_retail_price_excl > 0.0
                and abs(inc - round(rec.fbr_retail_price_excl * 1.18, 2)) <= 0.05
                and round(rec.fbr_retail_price_excl + rec.fbr_retail_unit_tax, 2) == inc
            ):
                continue
            else:
                excl = round(inc / 1.18, 2)
                rec.fbr_retail_price_excl = excl
                rec.fbr_retail_unit_tax = round(inc - excl, 2)

    @api.onchange('fbr_fixed_notified_value')
    def _onchange_fbr_fixed_notified_value(self):
        for rec in self:
            if not _is_product_3rd_schedule(rec):
                rec.fbr_retail_price_inc = 0.0
                rec.fbr_retail_price_excl = 0.0
                rec.fbr_retail_unit_tax = 0.0
                continue
            val = round(_parse_float_str(rec.fbr_fixed_notified_value), 2)
            if abs(val - (rec.fbr_retail_price_inc or 0.0)) > 0.001:
                rec.fbr_retail_price_inc = val
                rec._onchange_fbr_retail_price_inc()

    @api.onchange('fbr_retail_price_excl')
    def _onchange_fbr_retail_price_excl(self):
        for rec in self:
            excl = round(rec.fbr_retail_price_excl or 0.0, 2)
            inc = round(rec.fbr_retail_price_inc or 0.0, 2)
            if excl <= 0.0:
                rec.fbr_retail_unit_tax = 0.0
                rec.fbr_retail_price_inc = 0.0
                if _is_product_3rd_schedule(rec):
                    rec.fbr_fixed_notified_value = '0'
            elif inc > 0.0 and abs(inc - round(excl * 1.18, 2)) <= 0.05:
                # Override on existing MRP (e.g. 2000.00 & 1694.91 -> 305.09)
                rec.fbr_retail_unit_tax = round(inc - excl, 2)
            else:
                # Bottom-Up Mode (e.g. 1059.40 -> 190.69 & 1250.09)
                unit_tax = round(excl * 0.18, 2)
                rec.fbr_retail_unit_tax = unit_tax
                rec.fbr_retail_price_inc = round(excl + unit_tax, 2)
                rec.fbr_fixed_notified_value = f"{rec.fbr_retail_price_inc:g}"

    @api.onchange('fbr_retail_unit_tax')
    def _onchange_fbr_retail_unit_tax(self):
        for rec in self:
            tax = round(rec.fbr_retail_unit_tax or 0.0, 2)
            inc = round(rec.fbr_retail_price_inc or 0.0, 2)
            excl = round(rec.fbr_retail_price_excl or 0.0, 2)
            if tax > 0.0 and inc > 0.0 and tax < inc:
                rec.fbr_retail_price_excl = round(inc - tax, 2)
            elif tax > 0.0 and excl > 0.0:
                rec.fbr_retail_price_inc = round(excl + tax, 2)
                rec.fbr_fixed_notified_value = f"{rec.fbr_retail_price_inc:g}"
            elif tax > 0.0:
                excl_calc = round(tax / 0.18, 2)
                rec.fbr_retail_price_excl = excl_calc
                rec.fbr_retail_price_inc = round(excl_calc + tax, 2)
                rec.fbr_fixed_notified_value = f"{rec.fbr_retail_price_inc:g}"

    @api.model_create_multi
    def create(self, vals_list):
        retail_keys = {'fbr_retail_price_inc', 'fbr_retail_price_excl', 'fbr_retail_unit_tax', 'fbr_fixed_notified_value'}
        processed_vals_list = [
            _compute_retail_breakdown_vals(
                vals,
                sync_legacy_to_retail=_vals_is_product_3rd_schedule(self.env, vals),
            ) if (retail_keys & set(vals)) else vals
            for vals in vals_list
        ]
        return super().create(processed_vals_list)

    def write(self, vals):
        retail_keys = {'fbr_retail_price_inc', 'fbr_retail_price_excl', 'fbr_retail_unit_tax', 'fbr_fixed_notified_value'}
        if retail_keys & set(vals):
            if len(self) == 1:
                vals = _compute_retail_breakdown_vals(
                    vals,
                    current_inc=self.fbr_retail_price_inc,
                    current_excl=self.fbr_retail_price_excl,
                    current_tax=self.fbr_retail_unit_tax,
                    sync_legacy_to_retail=_vals_is_product_3rd_schedule(self.env, vals, rec=self),
                )
                return super().write(vals)
            res = True
            for rec in self:
                rec_vals = _compute_retail_breakdown_vals(
                    vals,
                    current_inc=rec.fbr_retail_price_inc,
                    current_excl=rec.fbr_retail_price_excl,
                    current_tax=rec.fbr_retail_unit_tax,
                    sync_legacy_to_retail=_vals_is_product_3rd_schedule(self.env, vals, rec=rec),
                )
                res = super(ProductTemplate, rec).write(rec_vals) and res
            return res
        return super().write(vals)

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

    @api.onchange('fbr_hs_code')
    def _onchange_fbr_hs_code(self):
        """Auto-format HS Code on change"""
        if self.fbr_hs_code:
            cleaned = re.sub(r'[^\d.]', '', self.fbr_hs_code)
            if cleaned.isdigit() and len(cleaned) == 8:
                self.fbr_hs_code = f"{cleaned[:4]}.{cleaned[4:]}"
            elif cleaned.isdigit() and len(cleaned) > 8:
                self.fbr_hs_code = f"{cleaned[:4]}.{cleaned[4:8]}"
            elif '.' in cleaned:
                parts = cleaned.split('.')
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    if len(parts[0]) <= 4 and len(parts[1]) <= 4:
                        self.fbr_hs_code = f"{parts[0].zfill(4)}.{parts[1].zfill(4)}"


class ProductProduct(models.Model):
    _inherit = "product.product"

    @api.model_create_multi
    def create(self, vals_list):
        retail_keys = {'fbr_retail_price_inc', 'fbr_retail_price_excl', 'fbr_retail_unit_tax', 'fbr_fixed_notified_value'}
        processed_vals_list = [
            _compute_retail_breakdown_vals(
                vals,
                sync_legacy_to_retail=_vals_is_product_3rd_schedule(self.env, vals),
            ) if (retail_keys & set(vals)) else vals
            for vals in vals_list
        ]
        return super().create(processed_vals_list)

    def write(self, vals):
        retail_keys = {'fbr_retail_price_inc', 'fbr_retail_price_excl', 'fbr_retail_unit_tax', 'fbr_fixed_notified_value'}
        if retail_keys & set(vals):
            if len(self) == 1:
                vals = _compute_retail_breakdown_vals(
                    vals,
                    current_inc=self.fbr_retail_price_inc,
                    current_excl=self.fbr_retail_price_excl,
                    current_tax=self.fbr_retail_unit_tax,
                    sync_legacy_to_retail=_vals_is_product_3rd_schedule(self.env, vals, rec=self),
                )
                return super().write(vals)
            res = True
            for rec in self:
                rec_vals = _compute_retail_breakdown_vals(
                    vals,
                    current_inc=rec.fbr_retail_price_inc,
                    current_excl=rec.fbr_retail_price_excl,
                    current_tax=rec.fbr_retail_unit_tax,
                    sync_legacy_to_retail=_vals_is_product_3rd_schedule(self.env, vals, rec=rec),
                )
                res = super(ProductProduct, rec).write(rec_vals) and res
            return res
        return super().write(vals)

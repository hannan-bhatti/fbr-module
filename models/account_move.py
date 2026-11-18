# -*- coding: utf-8 -*-

from odoo import models, fields, api, tools
import requests
import json
from datetime import datetime
from markupsafe import Markup
import qrcode
import io
import base64
from .product_template import _parse_float_str
from .sale_order_line import _extract_product_retail_vals


class Account_move(models.Model):
    _inherit = "account.move"

    fbr_response = fields.Text(string="FBR Response", readonly=False)
    fbr_sent = fields.Boolean(string="Sent to FBR", default=False)
    payload = fields.Text(
        string="Payload", help="JSON payload sent to FBR"
    )
    fbr_qr_code = fields.Image(
        string="FBR QR Code",
        help="QR code generated from FBR invoice number"
    )
    fbr_invoice_number = fields.Char(
        string="FBR Invoice Number",
        help="Invoice number as required by FBR"
    )
    fbr_digital_invoice_logo = fields.Image(
        string="FBR Digital Invoice Logo",
        help="FBR Digital Invoice logo",
        readonly=True,
        default=lambda self: self._get_default_fbr_logo()
    )

    fbr_document_type_id = fields.Many2one(
        "fbr.document.type",
        string="FBR Document Type",
        help="Select the FBR document type",
        default=lambda self: self.env['fbr.document.type'].search([('name', '=', 'Sale Invoice')], limit=1)
    )

    fbr_sale_type_id = fields.Many2one(
        "fbr.sale.type", string="FBR Sale Type", help="Select the FBR sale type",
        default=lambda self: self.env['fbr.sale.type'].search([('name', '=ilike', 'Goods at standard rate (default)')], limit=1)
    )

    fbr_scenario_id = fields.Many2one(
        "fbr.scenarios", string="FBR Scenario", help="Select the FBR scenario code",
        default=lambda self: self.env['fbr.scenarios'].search([('code', '=', 'SN001')], limit=1)
    )

    fbr_reason_id = fields.Many2one(
        "fbr.reason",
        string="FBR Reason",
        help="Select the reason for return/credit note (if applicable)",
    )

    fbr_province_id = fields.Many2one(
        "fbr.province", string="FBR Province", help="Select the FBR province",
        default=lambda self: self.env['fbr.province'].search([('name', '=', 'PUNJAB')], limit=1)
    )

    fbr_buyer_type_id = fields.Many2one(
        "fbr.buyer.type",
        related="partner_id.fbr_buyer_type_id", string="FBR Buyer Type", readonly=True,
        help="FBR buyer type selected in partner",
    )

    fbr_strict_retail_base_from_tax = fields.Boolean(
        related="company_id.fbr_strict_retail_base_from_tax",
        readonly=False,
        string="Strict Retail Base from Tax (ST / 0.18)",
        help=(
            "If enabled, fixedNotifiedValueOrRetailPrice in FBR SN008 payload "
            "is sent as round(salesTaxApplicable / 0.18, 2) instead of "
            "round(quantity * fbr_retail_price_excl, 2)."
        ),
    )

    @api.model
    def _get_default_fbr_logo(self):
        image = False
        img_path = tools.misc.file_path("fbr_pakistan/static/description/di.png")
        if img_path:
            with open(img_path, "rb") as f:
                image = f.read()
        return base64.b64encode(image) if image else False

    def _prepare_product_base_line_for_taxes_computation(self, product_line):
        base_line = super()._prepare_product_base_line_for_taxes_computation(product_line)
        inc = product_line.fbr_retail_price_inc or 0.0
        excl = product_line.fbr_retail_price_excl or 0.0
        unit_tax = product_line.fbr_retail_unit_tax or 0.0
        if not inc and not excl and not unit_tax:
            if product_line.fbr_fixed_notified_value and product_line.fbr_fixed_notified_value != '0':
                parsed = round(_parse_float_str(product_line.fbr_fixed_notified_value), 2)
                if parsed > 0.0:
                    inc = parsed
                    excl = round(parsed / 1.18, 2)
                    unit_tax = round(parsed - excl, 2)
            elif product_line.product_id:
                inc, excl, unit_tax, _ = _extract_product_retail_vals(product_line.product_id)

        is_3rd_schedule = bool(
            unit_tax > 0.0
            or excl > 0.0
            or (self.fbr_scenario_id and self.fbr_scenario_id.code in ('SN008', 'SN027'))
            or (self.fbr_sale_type_id and self.fbr_sale_type_id.name == '3rd Schedule Goods')
            or (product_line.fbr_sro_id and product_line.fbr_sro_id.name == '3rd Schedule goods')
            or (product_line.product_id and product_line.product_id.fbr_sro_name == '3rd Schedule goods')
        )
        base_line.update({
            'fbr_retail_price_inc': inc,
            'fbr_retail_price_excl': excl,
            'fbr_retail_unit_tax': unit_tax,
            'fbr_is_3rd_schedule': is_3rd_schedule,
        })
        return base_line

    def _get_rounded_base_and_tax_lines(self, round_from_tax_lines=True):
        if self.state == 'draft' and any(
            (l.fbr_retail_unit_tax or l.fbr_retail_price_excl or l.fbr_retail_price_inc)
            for l in self.invoice_line_ids
            if l.display_type in (False, 'product')
        ):
            round_from_tax_lines = False
        return super()._get_rounded_base_and_tax_lines(round_from_tax_lines=round_from_tax_lines)

    def action_recompute_fbr_taxes(self):
        """Synchronize FBR retail price fields on draft invoice lines and recompute tax journal items."""
        for move in self:
            if move.state != 'draft' or not move.is_invoice(include_receipts=True):
                continue
            product_lines = move.invoice_line_ids.filtered(
                lambda l: l.display_type in (False, 'product')
            )
            for line in product_lines:
                if not line.fbr_retail_price_inc and not line.fbr_retail_price_excl and not line.fbr_retail_unit_tax:
                    if line.fbr_fixed_notified_value and line.fbr_fixed_notified_value != '0':
                        parsed = round(_parse_float_str(line.fbr_fixed_notified_value), 2)
                        if parsed > 0.0:
                            line.write({'fbr_retail_price_inc': parsed})
                            continue
                    if line.product_id:
                        inc, excl, tax, fixed_str = _extract_product_retail_vals(line.product_id)
                        if inc or excl or tax:
                            line.write({
                                'fbr_retail_price_inc': inc,
                                'fbr_retail_price_excl': excl,
                                'fbr_retail_unit_tax': tax,
                                'fbr_fixed_notified_value': fixed_str,
                            })
            # Force _sync_tax_lines to recompute tax lines from base lines
            for line in product_lines:
                line._compute_totals()
                # Trigger tracked field write inside _sync_dynamic_lines context
                line.write({'price_unit': line.price_unit})

    def action_dynamic_payload(self):
        """Generate dynamic payload for FBR"""
        self.ensure_one()
        if self.state == 'draft':
            self.action_recompute_fbr_taxes()
        self.payload = json.dumps(self._prepare_fbr_data(), indent=2)

    def action_send_to_fbr(self):
        """Send invoice data to FBR"""
        self.ensure_one()

        invoice_data = self._prepare_fbr_data()
        self.payload = json.dumps(invoice_data, indent=2)

        url = self.env["ir.config_parameter"].sudo().get_param("fbr.api.url", "")
        token = self.env["ir.config_parameter"].sudo().get_param("fbr.api.token", "")

        if not token or not url:
            return self._show_notification(
                "FBR API token not configured. Please set 'fbr.api.token' in system parameters.",
                "danger",
            )

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Odoo/19.0",
            "Connection": "keep-alive",
            "Cache-Control": "no-cache",
        }

        try:
            response = requests.post(
                url=url, headers=headers, data=json.dumps(invoice_data), timeout=30
            )

            self.write(
                {
                    "fbr_response": response.text,
                    "fbr_sent": False,
                }
            )

            if response.status_code == 200:
                try:
                    response_json = response.json()
                except ValueError:
                    self._post_fbr_error_message(response)
                    return self._show_error_notification(response)

                validation_status = response_json.get("validationResponse", {}).get("statusCode", "")
                if validation_status == "00":
                    self.write({"fbr_sent": True})
                    self._process_fbr_success_response(response)
                    self._post_fbr_success_message(response)
                    return self._show_success_notification_with_reload()
                else:
                    self._post_fbr_error_message(response)
                    return self._show_error_notification(response)
            else:
                self._post_fbr_error_message(response)
                return self._show_error_notification(response)

        except requests.exceptions.Timeout:
            error_msg = "FBR API request timed out. Please try again."
            self._post_fbr_timeout_message()
            return self._show_notification(error_msg, "warning")

        except requests.exceptions.ConnectionError:
            error_msg = (
                "Could not connect to FBR API. Please check your internet connection."
            )
            self._post_fbr_connection_error_message()
            return self._show_notification(error_msg, "danger")

        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            self._post_fbr_exception_message(str(e))
            return self._show_notification(error_msg, "danger")

    def _post_fbr_success_message(self, response):
        """Post success message to chatter"""
        success_message = Markup(
            f"""
        <div class="alert alert-success border-0 shadow-sm" role="alert">
            <div class="d-flex align-items-center mb-3">
                <div class="flex-shrink-0">
                    <i class="fa fa-check-circle fa-2x text-success"></i>
                </div>
                <div class="flex-grow-1 ms-3">
                    <h5 class="alert-heading mb-1">
                        <i class="fa fa-paper-plane me-2"></i>Successfully Sent to FBR
                    </h5>
                    <p class="mb-0 text-muted">Invoice data has been successfully submitted to Pakistan FBR system.</p>
                </div>
            </div>

            <div class="row g-2 mb-3">
                <div class="col-md-4">
                    <div class="d-flex align-items-center">
                        <i class="fa fa-calendar-check me-2 text-success"></i>
                        <div>
                            <small class="text-muted d-block">Submission Time</small>
                            <strong>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</strong>
                        </div>
                    </div>
                </div>
                <div class="col-md-4">
                    <div class="d-flex align-items-center">
                        <i class="fa fa-info-circle me-2 text-success"></i>
                        <div>
                            <small class="text-muted d-block">Status Code</small>
                            <strong>{response.status_code}</strong>
                        </div>
                    </div>
                </div>
                <div class="col-md-4">
                    <div class="d-flex align-items-center">
                        <i class="fa fa-file-invoice me-2 text-success"></i>
                        <div>
                            <small class="text-muted d-block">Invoice</small>
                            <strong>{self.name}</strong>
                        </div>
                    </div>
                </div>
            </div>

            <div class="bg-light rounded p-3">
                <h6 class="mb-2">
                    <i class="fa fa-server me-2"></i>FBR Response
                </h6>
                <pre class="mb-0 small text-wrap" style="white-space: pre-wrap; word-break: break-word; max-height: 200px; overflow-y: auto;">{response.text}</pre>
            </div>
        </div>
        """
        )

        self.message_post(
            body=success_message,
            subject="FBR Submission Successful",
            message_type="comment",
        )

    def _post_fbr_error_message(self, response):
        """Post error message to chatter"""
        error_message = Markup(
            f"""
        <div class="alert alert-danger border-0 shadow-sm" role="alert">
            <div class="d-flex align-items-center mb-3">
                <div class="flex-shrink-0">
                    <i class="fa fa-exclamation-triangle fa-2x text-danger"></i>
                </div>
                <div class="flex-grow-1 ms-3">
                    <h5 class="alert-heading mb-1">
                        <i class="fa fa-times-circle me-2"></i>FBR Submission Failed
                    </h5>
                    <p class="mb-0">There was an error submitting the invoice to FBR. Please review and try again.</p>
                </div>
            </div>

            <div class="row g-2 mb-3">
                <div class="col-md-4">
                    <div class="d-flex align-items-center">
                        <i class="fa fa-calendar-times me-2 text-danger"></i>
                        <div>
                            <small class="text-muted d-block">Failed At</small>
                            <strong>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</strong>
                        </div>
                    </div>
                </div>
                <div class="col-md-4">
                    <div class="d-flex align-items-center">
                        <i class="fa fa-exclamation-circle me-2 text-danger"></i>
                        <div>
                            <small class="text-muted d-block">Status Code</small>
                            <strong>{response.status_code}</strong>
                        </div>
                    </div>
                </div>
                <div class="col-md-4">
                    <div class="d-flex align-items-center">
                        <i class="fa fa-file-invoice me-2 text-danger"></i>
                        <div>
                            <small class="text-muted d-block">Invoice</small>
                            <strong>{self.name}</strong>
                        </div>
                    </div>
                </div>
            </div>

            <div class="bg-light rounded p-3">
                <h6 class="mb-2 text-danger">
                    <i class="fa fa-bug me-2"></i>Error Details
                </h6>
                <pre class="mb-0 small text-wrap text-danger" style="white-space: pre-wrap; word-break: break-word; max-height: 200px; overflow-y: auto;">{response.text}</pre>
            </div>
        </div>
        """
        )

        self.message_post(
            body=error_message, subject="FBR Submission Failed", message_type="comment"
        )

    def _post_fbr_timeout_message(self):
        """Post timeout message to chatter"""
        timeout_message = Markup(
            f"""
        <div class="alert alert-warning border-0 shadow-sm" role="alert">
            <div class="d-flex align-items-center">
                <div class="flex-shrink-0">
                    <i class="fa fa-hourglass-end fa-2x text-warning"></i>
                </div>
                <div class="flex-grow-1 ms-3">
                    <h5 class="alert-heading mb-1">
                        <i class="fa fa-spinner me-2"></i>FBR Submission Timeout
                    </h5>
                    <p class="mb-2">The request to FBR API timed out. This might be due to network issues or high server load.</p>
                    <small class="text-muted">
                        <i class="fa fa-info-circle me-1"></i>
                        Attempted at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Invoice: {self.name}
                    </small>
                </div>
            </div>
        </div>
        """
        )

        self.message_post(
            body=timeout_message,
            subject="FBR Submission Timeout",
            message_type="comment",
        )

    def _post_fbr_connection_error_message(self):
        """Post connection error message to chatter"""
        connection_message = Markup(
            f"""
        <div class="alert alert-danger border-0 shadow-sm" role="alert">
            <div class="d-flex align-items-center">
                <div class="flex-shrink-0">
                    <i class="fa fa-wifi fa-2x text-danger"></i>
                </div>
                <div class="flex-grow-1 ms-3">
                    <h5 class="alert-heading mb-1">
                        <i class="fa fa-unlink me-2"></i>FBR Connection Failed
                    </h5>
                    <p class="mb-2">Could not establish connection to FBR API. Please check your internet connection and try again.</p>
                    <small class="text-muted">
                        <i class="fa fa-info-circle me-1"></i>
                        Attempted at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Invoice: {self.name}
                    </small>
                </div>
            </div>
        </div>
        """
        )

        self.message_post(
            body=connection_message,
            subject="FBR Connection Error",
            message_type="comment",
        )

    def _post_fbr_exception_message(self, error_details):
        """Post exception message to chatter"""
        exception_message = Markup(
            f"""
        <div class="alert alert-danger border-0 shadow-sm" role="alert">
            <div class="d-flex align-items-center mb-3">
                <div class="flex-shrink-0">
                    <i class="fa fa-bug fa-2x text-danger"></i>
                </div>
                <div class="flex-grow-1 ms-3">
                    <h5 class="alert-heading mb-1">
                        <i class="fa fa-exclamation-triangle me-2"></i>FBR System Error
                    </h5>
                    <p class="mb-0">An unexpected error occurred while processing the FBR submission.</p>
                </div>
            </div>

            <div class="bg-light rounded p-3">
                <h6 class="mb-2 text-danger">
                    <i class="fa fa-code me-2"></i>Technical Details
                </h6>
                <pre class="mb-0 small text-wrap text-danger" style="white-space: pre-wrap; word-break: break-word;">{error_details}</pre>
                <small class="text-muted d-block mt-2">
                    <i class="fa fa-info-circle me-1"></i>
                    Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Invoice: {self.name}
                </small>
            </div>
        </div>
        """
        )

        self.message_post(
            body=exception_message, subject="FBR System Error", message_type="comment"
        )

    def _show_success_notification_with_reload(self):
        """Show success notification and soft reload"""
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "FBR Integration Success",
                "message": "Invoice successfully sent to FBR! Check chatter for details.",
                "type": "success",
                "sticky": False,
                "next": {
                    "type": "ir.actions.client",
                    "tag": "reload",
                },
            },
        }

    def _show_error_notification(self, response):
        """Show error notification"""
        if response.status_code == 403:
            message = "Authentication failed. Please verify your FBR token."
        elif response.status_code == 401:
            message = "Invalid or expired token. Please update your FBR token."
        else:
            message = f"Failed to send to FBR (Status: {response.status_code}). Check chatter for details."

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "FBR Integration Failed",
                "message": message,
                "type": "danger",
                "sticky": True,
                "next": {
                    "type": "ir.actions.client",
                    "tag": "reload",
                },
            },
        }

    def _prepare_fbr_line_item(self, line):
        """Prepare a single invoice line item dictionary for FBR Digital Invoicing payload."""
        company = self.company_id
        inc = line.fbr_retail_price_inc or 0.0
        excl = line.fbr_retail_price_excl or 0.0
        unit_tax = line.fbr_retail_unit_tax or 0.0
        if not inc and not excl and not unit_tax:
            if line.fbr_fixed_notified_value and line.fbr_fixed_notified_value != '0':
                parsed = round(_parse_float_str(line.fbr_fixed_notified_value), 2)
                if parsed > 0.0:
                    inc = parsed
                    excl = round(parsed / 1.18, 2)
                    unit_tax = round(parsed - excl, 2)
            elif line.product_id:
                inc, excl, unit_tax, _ = _extract_product_retail_vals(line.product_id)

        sro_name = (
            line.fbr_sro_id.name
            if line.fbr_sro_id
            else (line.product_id.fbr_sro_id.name if line.product_id.fbr_sro_id else "")
        )
        scenario_code = self.fbr_scenario_id.code if self.fbr_scenario_id else ""
        sale_type_name = self.fbr_sale_type_id.name if self.fbr_sale_type_id else ""

        is_3rd_schedule = bool(
            scenario_code in ('SN008', 'SN027')
            or sale_type_name == '3rd Schedule Goods'
            or sro_name == '3rd Schedule goods'
            or unit_tax > 0.0
            or excl > 0.0
        )

        qty = float(line.quantity or 0.0)
        value_sales_excl_st = round(float(line.price_subtotal or 0.0), 2)

        # Identify line taxes by role (excluding WHT 236G from all FBR sales tax fields)
        extra_tax_records = line.tax_ids.filtered(
            lambda t: t.fbr_tax_type == '3rd_schedule_extra'
            or (
                is_3rd_schedule
                and t.fbr_tax_type in ('none', False)
                and t.amount == 4.0
                and 'further' not in (t.name or '').lower()
            )
        )
        if not extra_tax_records and line.fbr_extra_tax_id:
            extra_tax_records = line.fbr_extra_tax_id

        excluded_tax_ids = (
            extra_tax_records
            | line.fbr_further_tax_id
            | line.fbr_sales_tax_withheld_id
            | line.fbr_fed_payable_id
            | line.tax_ids.filtered(lambda t: t.fbr_tax_type == 'wht_236g' or (0.0 < abs(t.amount) < 1.0))
        )
        main_st_taxes = line.tax_ids - excluded_tax_ids

        # 1. Rate string
        if is_3rd_schedule:
            rate_str = "18%"
        elif main_st_taxes:
            st_rate = sum(main_st_taxes.mapped('amount'))
            rate_str = f"{int(st_rate)}%" if float(st_rate).is_integer() else f"{st_rate}%"
        elif scenario_code == 'SN007':
            rate_str = "0%"
        else:
            rate_str = "Exempt"

        # 2. Sales Tax Applicable (strictly 18% Sales Tax, excluding Extra Tax and WHT 236G)
        if is_3rd_schedule and unit_tax > 0.0:
            sales_tax_applicable = round(qty * unit_tax, 2)
        elif main_st_taxes:
            sales_tax_applicable = round(
                sum(value_sales_excl_st * (t.amount / 100.0) for t in main_st_taxes),
                2,
            )
        else:
            sales_tax_applicable = 0.0

        # 3. Fixed / Notified Value or Retail Price (Excl. ST)
        if is_3rd_schedule and excl > 0.0:
            if company.fbr_strict_retail_base_from_tax:
                fixed_notified_retail_price = round(sales_tax_applicable / 0.18, 2)
            else:
                fixed_notified_retail_price = round(qty * excl, 2)
        else:
            fixed_notified_retail_price = round(
                _parse_float_str(line.fbr_fixed_notified_value or line.product_id.fbr_fixed_notified_value),
                2,
            )

        # 4. Extra Tax (4% on Retail Excl. ST for 3rd Schedule when applied, else 0.0)
        if extra_tax_records:
            if is_3rd_schedule and excl > 0.0:
                extra_tax = round(
                    sum(qty * excl * (t.amount / 100.0) for t in extra_tax_records),
                    2,
                )
            else:
                extra_tax = round(
                    sum(abs(value_sales_excl_st * (t.amount / 100.0)) for t in extra_tax_records),
                    2,
                )
        else:
            extra_tax = 0.0

        # 5. Further Tax (0.0 for 3rd Schedule Goods)
        if is_3rd_schedule:
            further_tax = 0.0
        elif line.fbr_further_tax_id:
            further_tax = round(abs(value_sales_excl_st * line.fbr_further_tax_id.amount / 100.0), 2)
        else:
            further_tax = 0.0

        # 6. Sales Tax Withheld at Source (strictly Sales Tax Withholding, never WHT 236G)
        if (
            line.fbr_sales_tax_withheld_id
            and line.fbr_sales_tax_withheld_id.fbr_tax_type != 'wht_236g'
        ):
            st_withheld = round(abs(value_sales_excl_st * line.fbr_sales_tax_withheld_id.amount / 100.0), 2)
        else:
            st_withheld = 0.0

        # 7. FED Payable
        if line.fbr_fed_payable_id:
            fed_payable = round(abs(value_sales_excl_st * line.fbr_fed_payable_id.amount / 100.0), 2)
        else:
            fed_payable = 0.0

        # 8. Discount & Total Values
        discount_val = round(float(line.discount or 0.0), 2)
        total_values = round(
            value_sales_excl_st
            + sales_tax_applicable
            + extra_tax
            + further_tax
            + fed_payable
            - discount_val,
            2,
        )

        uom_name = (
            line.fbr_uom_id.name
            if line.fbr_uom_id
            else (line.product_id.fbr_uom_id.name if line.product_id.fbr_uom_id else "")
        )
        sro_serial_name = (
            line.fbr_sro_item_serial_id.name
            if line.fbr_sro_item_serial_id
            else (line.product_id.fbr_sro_item_serial_id.name if line.product_id.fbr_sro_item_serial_id else "")
        )

        return {
            "hsCode": line.fbr_hs_code or line.product_id.fbr_hs_code or "",
            "productDescription": line.product_id.name or line.name or "",
            "rate": rate_str,
            "uoM": uom_name,
            "quantity": qty,
            "totalValues": total_values,
            "valueSalesExcludingST": value_sales_excl_st,
            "salesTaxApplicable": sales_tax_applicable,
            "fixedNotifiedValueOrRetailPrice": fixed_notified_retail_price,
            "salesTaxWithheldAtSource": st_withheld,
            "extraTax": extra_tax,
            "furtherTax": further_tax,
            "sroScheduleNo": sro_name or ("3rd Schedule goods" if is_3rd_schedule else ""),
            "fedPayable": fed_payable,
            "discount": discount_val,
            "saleType": sale_type_name or ("3rd Schedule Goods" if is_3rd_schedule else ""),
            "sroItemSerialNo": sro_serial_name,
        }

    def _prepare_fbr_data(self):
        """Prepare invoice data in FBR format"""
        self.ensure_one()
        company = self.company_id
        partner = self.partner_id

        product_lines = self.invoice_line_ids.filtered(
            lambda l: l.display_type not in ["line_section", "line_subsection", "line_note"]
        )

        return {
            "invoiceType": (
                self.fbr_document_type_id.name if self.fbr_document_type_id else ""
            ),
            "invoiceDate": (
                self.invoice_date.strftime("%Y-%m-%d")
                if self.invoice_date
                else (self.date.strftime("%Y-%m-%d") if self.date else "")
            ),
            "sellerNTNCNIC": company.vat or "",
            "sellerBusinessName": company.name or "",
            "sellerProvince": self.fbr_province_id.name if self.fbr_province_id else "",
            "sellerAddress": company.street or "",
            "buyerNTNCNIC": partner.vat or "",
            "buyerBusinessName": partner.name or "",
            "buyerProvince": partner.state_id.name or "",
            "buyerAddress": partner.street or "",
            "invoiceRefNo": self.name or "",
            "buyerRegistrationType": (
                partner.fbr_buyer_type_id.name if partner.fbr_buyer_type_id else ""
            ),
            "scenarioId": self.fbr_scenario_id.code if self.fbr_scenario_id else "",
            "reasonForReturn": self.fbr_reason_id.name if self.fbr_reason_id else "",
            "items": [self._prepare_fbr_line_item(line) for line in product_lines],
        }

    def _show_notification(self, message, type="info"):
        """Helper method to show notifications"""
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "message": message,
                "type": type,
                "sticky": type == "danger",
            },
        }

    def action_test_fbr_messages(self):
        """Test all FBR message types at once"""
        self.ensure_one()

        class MockResponse:
            def __init__(self, status_code, text):
                self.status_code = status_code
                self.text = text

        success_response = MockResponse(
            200,
            '{"status": "success", "invoice_id": "12345", "message": "Invoice successfully submitted to FBR"}',
        )
        self._post_fbr_success_message(success_response)

        error_response = MockResponse(
            400,
            '{"error": "Invalid invoice data", "details": "Missing required field: buyer_ntn"}',
        )
        self._post_fbr_error_message(error_response)

        self._post_fbr_timeout_message()
        self._post_fbr_connection_error_message()
        self._post_fbr_exception_message(
            "ValueError: Invalid JSON format in API response"
        )

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "FBR Message Test Complete",
                "message": "All FBR message types have been posted to the chatter. Check the message log below.",
                "type": "success",
                "sticky": False,
                "next": {
                    "type": "ir.actions.client",
                    "tag": "soft_reload",
                },
            },
        }

    def _process_fbr_success_response(self, response):
        """Process successful FBR response and extract invoice number"""
        try:
            response_data = json.loads(response.text)
            invoice_number = response_data.get('invoiceNumber', '')
            if invoice_number:
                qr_code_data = self._generate_fbr_qr_code(invoice_number)
                self.write({
                    'fbr_invoice_number': invoice_number,
                    'fbr_qr_code': qr_code_data if qr_code_data else False,
                })
        except (json.JSONDecodeError, KeyError) as e:
            self.message_post(
                body=f"Warning: Could not extract invoice number from FBR response: {str(e)}",
                message_type="comment"
            )

    def _generate_fbr_qr_code(self, data):
        """Generate QR code from given data"""
        try:
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=10,
                border=4,
            )
            qr.add_data(data)
            qr.make(fit=True)
            qr_image = qr.make_image(fill_color="black", back_color="white")
            buffer = io.BytesIO()
            qr_image.save(buffer, format='PNG')
            return base64.b64encode(buffer.getvalue())
        except Exception as e:
            self.message_post(
                body=f"Error generating QR code: {str(e)}",
                message_type="comment"
            )
            return False

    def action_generate_fbr_qr_code(self):
        """Manual action to generate QR code from FBR invoice number"""
        self.ensure_one()
        if not self.fbr_invoice_number:
            return self._show_notification(
                "No FBR invoice number found. Please send to FBR first.",
                "warning"
            )
        qr_code_data = self._generate_fbr_qr_code(self.fbr_invoice_number)
        if qr_code_data:
            self.fbr_qr_code = qr_code_data
            return self._show_notification(
                "QR code generated successfully!",
                "success"
            )
        return self._show_notification(
            "Failed to generate QR code.",
            "danger"
        )

    @api.onchange('fbr_scenario_id')
    def _onchange_fbr_scenario_set_sale_type(self):
        scenario_to_sale_type = {
            'SN001': 'Goods at standard rate (default)',
            'SN002': 'Goods at standard rate (default)',
            'SN003': 'Steel melting and re-rolling',
            'SN004': 'Ship breaking',
            'SN005': 'Goods at Reduced Rate',
            'SN006': 'Exempt goods',
            'SN007': 'Goods at zero-rate',
            'SN008': '3rd Schedule Goods',
            'SN009': 'Cotton ginners',
            'SN010': 'Telecommunication services',
            'SN011': 'Toll Manufacturing',
            'SN012': 'Petroleum Products',
            'SN013': 'Electricity Supply to Retailers',
            'SN014': 'Gas to CNG stations',
            'SN015': 'Mobile Phones',
            'SN016': 'Processing/Conversion of Goods',
            'SN017': 'Goods (FED in ST Mode)',
            'SN018': 'Services (FED in ST Mode)',
            'SN019': 'Services',
            'SN020': 'Electric Vehicle',
            'SN021': 'Cement /Concrete Block',
            'SN022': 'Potassium Chlorate',
            'SN023': 'CNG Sales',
            'SN024': 'Goods as per SRO.297(|)/2023',
            'SN025': 'Non-Adjustable Supplies',
            'SN026': 'Goods at standard rate (default)',
            'SN027': '3rd Schedule Goods',
            'SN028': 'Goods at Reduced Rate',
        }

        for rec in self:
            if rec.fbr_scenario_id and rec.fbr_scenario_id.code in scenario_to_sale_type:
                sale_type_name = scenario_to_sale_type[rec.fbr_scenario_id.code]
                sale_type = self.env['fbr.sale.type'].search([('name', '=ilike', sale_type_name)], limit=1)
                rec.fbr_sale_type_id = sale_type or False
            else:
                rec.fbr_sale_type_id = False

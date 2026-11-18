# -*- coding: utf-8 -*-
{
    'name': 'FBR Pakistan Digital Invoicing – Azentra Consulting',
    'version': '19.0.1.1.0',
    'category': 'Accounting/Localization',
    'summary': 'Pakistan FBR Digital Invoicing (SN001–SN028) & 3rd Schedule Retail Tax Engine by Azentra Consulting',
    'description': """
FBR Pakistan Digital Invoicing – Azentra Consulting
===================================================

Proprietary Odoo 19 FBR Digital Invoicing and statutory tax engine developed and maintained by **Azentra Consulting**.

Key Capabilities:
-----------------
* Real-time Pakistan FBR PRAL/WSO2 Digital Invoicing integration across scenarios SN001–SN028
* 3rd Schedule Goods (SN008) Retail-Price Tax & Payload Engine (18% Retail Sales Tax, 4% Extra Tax, WHT 236G)
* Flexible Retail Price entry on Product Form, Sale Order Line, or Invoice Line (Top-Down MRP, Bottom-Up Excl. ST, and Manual Override)
* Pre-configured FBR master data (UOMs, Provinces, Document Types, Sale Types, SRO Schedules, and Serial Numbers)
* Automated QR Code generation and FBR-compliant printable PDF invoices

Developed & Maintained by:
--------------------------
* **Company:** Azentra Consulting
* **Website:** https://azentraconsulting.vercel.app
* **Email:** azentraconsulting@gmail.com
* **Phone / WhatsApp:** +92 343 2513431
    """,
    'author': 'Azentra Consulting',
    'website': 'https://azentraconsulting.vercel.app',
    'maintainers': ['Azentra Consulting'],
    'support': 'azentraconsulting@gmail.com',
    'license': 'OPL-1',
    'depends': [
        'base',
        'account',
        'sale',
        'stock',
        'product',
        'contacts',
    ],

    'data': [
        'security/ir.model.access.csv',
        'data/config_data.xml',
        'data/fbr_buyer_type_data.xml',
        'data/fbr_document_type_data.xml',
        'data/fbr_province_data.xml',
        'data/fbr_reason_data.xml',
        'data/fbr_sale_type_data.xml',
        'data/fbr_scenarios_data.xml',
        'data/fbr_sro_data.xml',
        'data/fbr_uom_data.xml',
        'data/fbr_sro_item_serial_data.xml',
        'data/fbr_tax_data.xml',
        'views/fbr_uom_views.xml',
        'views/account_tax_views.xml',
        'views/sale_order_views.xml',
        'views/account_move_line_views.xml',
        'views/account_move_views_copy.xml',
        'views/fbr_buyer_type_views.xml',
        'views/fbr_document_type_views.xml',
        'views/fbr_province_views.xml',
        'views/fbr_reason_views.xml',
        'views/fbr_sale_type_views.xml',
        'views/fbr_scenarios_views.xml',
        'views/fbr_sro_item_serial_views.xml',
        'views/fbr_sro_views.xml',
        'views/menus.xml',
        'views/product_template_views.xml',
        'views/res_partner_views.xml',
        'views/templates.xml',
        'reports/invoice_report_template.xml',
    ],

    'assets': {
        'web.assets_backend': [],
        'web.assets_frontend': [],
    },

    'images': [
        'static/description/banner.png',
    ],

    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': True,
    'auto_install': False,
    'external_dependencies': {
        'python': ['qrcode', 'requests', 'Pillow'],
    },
    'price': 39.0,
    'currency': 'USD',
}

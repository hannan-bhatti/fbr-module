# FBR Pakistan Digital Invoicing for Odoo 19 — Azentra Consulting

This module connects Odoo 19 directly to Pakistan's Federal Board of Revenue (FBR) PRAL Digital Invoicing system and includes the 3rd Schedule Goods (`SN008`) retail-price tax engine.

## Key Features

* **Real-Time FBR API Submission:** Generates and submits validated JSON payloads across scenarios `SN001`–`SN028` directly from Odoo customer invoices.
* **3rd Schedule Retail Tax Engine (`SN008`):** Calculates 18% Sales Tax and 4% Extra Tax on Consumer Retail Price while billing the customer on Trade Price (Net Rate), plus `WHT 236G` advance tax on untaxed Trade Value.
* **Flexible Price Entry:** Set retail prices on the Product form or enter/override them per transaction directly on Sale Order lines and Invoice lines.
* **QR Code & PDF Reporting:** Generates the official FBR QR code and prints FBR-compliant tax invoices.
* **Pre-Loaded FBR Master Data:** Ships with FBR UOMs, Provinces, Buyer Types, Document Types, Sale Types, SRO Schedules, and Serial Numbers.

## Installation

1. Place the `fbr_pakistan` folder into your Odoo `addons` directory.
2. Restart the Odoo server and click **Update Apps List** under **Apps**.
3. Search for **FBR Pakistan Digital Invoicing – Azentra Consulting** and click **Install** (or **Upgrade**).

## Configuration

1. Enable **Developer Mode** in Odoo.
2. Open **Settings → Technical → System Parameters** and set:
   * `fbr.api.url`: Your FBR PRAL endpoint URL
   * `fbr.api.token`: Your FBR bearer token

## Support & Contact

* **Company:** Azentra Consulting
* **Email:** azentraconsulting@gmail.com
* **Phone / WhatsApp:** +92 343 2513431
* **Website:** https://azentraconsulting.vercel.app

## License

Proprietary (OPL-1) — Azentra Consulting.

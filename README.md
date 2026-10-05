# DocFlow ⚡ — Headless B2B Document & PO to Webhook Engine

> **Turn messy vendor emails and PDF purchase orders into clean, math-verified JSON webhooks in under 3 seconds.**

---

## 💡 The Business Model & Strategy (2026 Wedge)

### The Problem
Millions of mid-market distributors, construction contractors, HVAC suppliers, and B2B merchants receive purchase orders and invoices as scanned PDFs or image attachments in vendor emails. 
- **Enterprise IDP tools** (Rossum, ABBYY) cost $10,000+/year and require multi-month sales cycles.
- **Raw Developer APIs** (AWS Textract, Document AI) lack a review UI, math verification, or email webhook handling.
- **The Result:** Businesses pay staff $15–$25/hour to manually re-type numbers from PDF emails into ERPs and accounting sheets.

### The DocFlow Solution
1. **Magic Email Inbox:** Customers forward vendor emails to `orders-company@yourdomain.com`.
2. **Deterministic Math Verification:** Automatically verifies that `Quantity × Unit Price == Line Total`, `Sum(Lines) == Subtotal`, and `Subtotal + Tax + Shipping == Grand Total` down to the penny.
3. **Instant Webhook Dispatch:** If math balances 100%, pushes clean structured JSON directly to their Zapier, Make, ERP, or QuickBooks webhook.
4. **1-Click Human Review:** If math doesn't balance or a critical field is blurry, flags it for 1-click human verification with side-by-side PDF preview.

### Micro-Pricing Strategy ($0 Sales Friction)
- **Free Tier:** First 50 documents / 14-day trial.
- **Starter Plan:** $29/mo (up to 250 documents).
- **Pro Plan:** $79/mo (up to 1,000 documents).
- **Overages:** $0.08 per extra document.

---

## 🚀 Features

- **Multi-Engine Extraction:** 
  - Google Gemini 2.5 Flash Vision API (high-accuracy multimodal recognition for scans/photos).
  - OpenAI GPT-4o-mini support.
  - Built-in Heuristic PDF Table & Token Parser (works locally with zero API keys).
- **Automated Arithmetic Cross-Checking:** Cents-level verification on line items, subtotals, and taxes.
- **Inbound Email Webhooks:** Pre-built connectors for **Postmark Inbound** and **SendGrid Inbound Parse**.
- **HMAC Signed Outbound Webhooks:** Delivers JSON payloads with `X-DocFlow-Signature` (SHA-256), latency tracking, and delivery logs.
- **Modern Side-by-Side Review UI:** Responsive Tailwind CSS interface with PDF preview, editable table rows, live recalculation, and CSV/JSON export.

---

## 🛠️ Quickstart

### 1. Run with Local Virtualenv
```bash
cd /home/manvendrasingh_/Projects/docflow
./run.sh
```

Then visit:
- **Interactive Dashboard:** [http://localhost:8000](http://localhost:8000)
- **API Documentation (Swagger):** [http://localhost:8000/docs](http://localhost:8000/docs)
- **Settings & Webhooks:** [http://localhost:8000/settings-view](http://localhost:8000/settings-view)

### 2. Run Tests
```bash
source venv/bin/activate
PYTHONPATH=. pytest tests
```

---

## 📁 Project Structure

```text
docflow/
├── backend/
│   ├── config.py              # Configuration & environment variables
│   ├── database.py            # SQLite document repository & webhook delivery logs
│   ├── main.py                # FastAPI application entrypoint & template routes
│   ├── models/
│   │   └── document.py        # Pydantic schemas (PO, LineItems, Verification, Summary)
│   ├── services/
│   │   ├── extractor.py       # Multi-engine extraction (Gemini Vision, OpenAI, PDF heuristic)
│   │   ├── validator.py       # Arithmetic integrity & cross-checking engine
│   │   ├── email_receiver.py  # Inbound email webhook parser (Postmark, SendGrid)
│   │   └── webhook_dispatcher.py # Outbound HMAC-signed webhooks
│   └── api/
│       ├── documents.py       # Upload, list, inspect, edit, dispatch, CSV export
│       ├── inbound_email.py   # Inbound email webhook receivers & mock simulator
│       └── settings.py        # Webhook destination & API key configuration
├── sample_data/               # Test documents (sample_po_verified.pdf, sample_invoice_discrepancy.pdf)
├── scripts/
│   └── generate_samples.py    # Generates realistic test PDF purchase orders
├── static/                    # CSS, JavaScript, and client assets
├── templates/                 # Jinja2 HTML templates
│   ├── base.html
│   ├── index.html             # Dashboard with metrics, dropzone, and document stream
│   ├── document_detail.html   # Side-by-side inspection & math verification view
│   └── settings.html          # Webhook endpoint and API keys configuration
├── tests/                     # Unit and integration test suite
├── run.sh                     # One-click startup script
└── requirements.txt
```

---

## 📡 API Reference

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/api/documents/upload` | Upload single or batch PDF / image purchase orders. |
| `GET` | `/api/documents` | List processed documents with filtering by status. |
| `GET` | `/api/documents/{id}` | Get full document details, lines, and verification status. |
| `PUT` | `/api/documents/{id}` | Edit fields and re-run arithmetic integrity verification. |
| `POST` | `/api/documents/{id}/dispatch` | Manually dispatch document payload to webhook URL. |
| `GET` | `/api/documents/{id}/export/csv` | Download itemized line items as CSV. |
| `POST` | `/api/inbound/postmark` | Postmark Inbound email webhook receiver. |
| `POST` | `/api/inbound/sendgrid` | SendGrid Inbound Parse email webhook receiver. |
| `POST` | `/api/inbound/test-mock` | Simulate receiving an inbound email for instant testing. |
| `GET` | `/api/settings` | Get current webhook destination and provider settings. |
| `POST` | `/api/settings` | Save webhook destination URL, secret, and API keys. |

# DocFlow

Invoice intake, extraction, review, and webhook delivery.

---



## Features

- PDF text and table extraction, with optional Gemini or OpenAI vision per account.
- Arithmetic checks for line totals, subtotals, tax, shipping, discounts, and grand totals.
- Editable invoice review, private document previews, and CSV/JSON export.
- Signed webhook delivery and token-authenticated inbound attachment endpoints.
- Account isolation, scrypt password hashes, CSRF-protected requests, and encrypted integration credentials.

Extraction scores are not calibrated accuracy estimates. The current validator checks arithmetic and a small set of required fields; it does not measure OCR field-level precision or recall. Scanned PDFs require a configured vision provider. Without one, documents that have no text layer are marked failed rather than populated with invented demo data.

---

## Quickstart

### 1. Configure PostgreSQL
The app reads `DATABASE_URL`, or `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, and `DB_PASSWORD`, from `.env`. Create the database and grant the configured user access before starting the app. The app creates its tables at startup. SQLite is used when PostgreSQL settings are absent and for automated tests.

For deployments, set a strong shared `APP_SECRET_KEY`, set `SECURE_COOKIES=true` behind HTTPS, and back up the database and `data/encryption.key`. Losing the encryption key makes saved provider and webhook secrets unreadable.

### 2. Start and create an account
```bash
cd /home/manvendrasingh_/Projects/docflow
./run.sh
```

Open [http://localhost:8000/register](http://localhost:8000/register) to create the first account, then sign in at `/login`. Each account has its own invoices, provider keys, webhook settings, and email intake token. Add Gemini or OpenAI credentials under Settings to enable vision extraction. No model API keys are included in the repository.

### 3. Run Tests
```bash
source venv/bin/activate
PYTHONPATH=. pytest tests
```

---

## Project Structure

```text
docflow/
├── backend/
│   ├── config.py              # Configuration & environment variables
│   ├── database.py            # PostgreSQL/SQLite repositories and account ownership
│   ├── main.py                # FastAPI application entrypoint & template routes
│   ├── models/
│   │   └── document.py        # Pydantic schemas (PO, LineItems, Verification, Summary)
│   ├── services/
│   │   ├── extractor.py       # Gemini/OpenAI vision and text-layout extraction
│   │   ├── validator.py       # Arithmetic integrity & cross-checking engine
│   │   ├── email_receiver.py  # Inbound email webhook parser (Postmark, SendGrid)
│   │   └── webhook_dispatcher.py # Outbound HMAC-signed webhooks
│   └── api/
│       ├── documents.py       # Upload, list, inspect, edit, dispatch, CSV export
│       ├── inbound_email.py   # Token-authenticated inbound email receivers
│       ├── settings.py        # Account profile, provider keys, and webhook settings
│       └── auth.py            # Registration, login, and logout
├── sample_data/               # Test documents (sample_po_verified.pdf, sample_invoice_discrepancy.pdf)
├── scripts/
│   └── generate_samples.py    # Generates realistic test PDF purchase orders
├── static/                    # CSS, JavaScript, and client assets
├── templates/                 # Jinja2 HTML templates
│   ├── base.html
│   ├── index.html             # Dashboard with metrics, dropzone, and document stream
│   ├── document_detail.html   # Side-by-side inspection & math verification view
│   ├── auth.html              # Login and registration
│   └── account_settings.html  # Profile and integration settings
├── tests/                     # Unit and integration test suite
├── run.sh                     # One-click startup script
└── requirements.txt
```

---

## API Reference

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/api/documents/upload` | Upload single or batch PDF / image purchase orders. |
| `GET` | `/api/documents` | List processed documents with filtering by status. |
| `GET` | `/api/documents/{id}` | Get full document details, lines, and verification status. |
| `PUT` | `/api/documents/{id}` | Edit fields and re-run arithmetic integrity verification. |
| `POST` | `/api/documents/{id}/dispatch` | Manually dispatch document payload to webhook URL. |
| `GET` | `/api/documents/{id}/export/csv` | Download itemized line items as CSV. |
| `POST` | `/api/inbound/postmark` | Receive attachments with `X-DocFlow-Inbound-Token` or a Bearer token. |
| `POST` | `/api/inbound/sendgrid` | Receive attachments with the account token in `docflow_token`. |
| `GET` | `/api/settings` | Get the signed-in account's non-secret settings. |
| `POST` | `/api/settings` | Save account-scoped provider and webhook settings. |
| `PUT` | `/api/settings/profile` | Update account name, email, and company. |
| `PUT` | `/api/settings/password` | Change the account password. |

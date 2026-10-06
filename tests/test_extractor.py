import asyncio
from unittest.mock import AsyncMock

from pypdf import PdfWriter

from backend.services.extractor import DocumentExtractor
from backend.config import settings
from backend.models.document import ParsedDocumentData


def test_table_headers_extract_ordered_service_and_embedded_quantity():
    text = "INVOICE\n0012820\nConIncorporated SHIP TO: Caitlin Roberts"
    table = [
        ["", "Item #", "", "", "Ordered Service", "", "", "Item Price", "", "", "Total", ""],
        ["", "1", "", "", "10-700 - Exterior Protection (10)", "", "", "40.29", "", "", "402.90", ""],
    ]

    document = DocumentExtractor._parse_layout_document(text, [text], [table], "invoice-2-1.pdf")

    assert document.document_type == "INVOICE"
    assert document.document_number == "0012820"
    assert document.vendor.name == "ConIncorporated"
    assert len(document.line_items) == 1
    item = document.line_items[0]
    assert item.sku == "10-700"
    assert item.description == "Exterior Protection"
    assert item.quantity == 10
    assert item.unit_price == 40.29
    assert item.line_total == 402.90


def test_currency_is_not_guessed_when_invoice_has_no_currency_marker():
    document = DocumentExtractor._parse_layout_document(
        "Invoice 12345\nSubtotal 100.00\nTotal Due 100.00",
        ["Invoice 12345"],
        [],
        "invoice.pdf",
    )

    assert document.summary.currency == "UNKNOWN"


def test_invoice_metadata_can_come_from_secondary_text_extraction():
    document = DocumentExtractor._parse_layout_document(
        "Neid\nMake/Model Product Code Unit Price",
        ["Neid"],
        [],
        "invoice-3-0.pdf",
        metadata_text="Invoice 4235",
    )

    assert document.document_type == "INVOICE"
    assert document.document_number == "4235"


def test_superstore_invoice_layout_extracts_wrapped_items_discount_and_balance():
    text = """SuperStore INVOICE
# 36258
Date: Mar 06 2012
Bill To: Ship To:
Ship Mode: First Class
Aaron Bergman 98103, Seattle,
Washington, United
Balance Due: $50.10
States
Item Quantity Rate Amount
Global Push Button Manager's Chair, Indigo 1 $48.71 $48.71
Chairs, Furniture, FUR-CH-4421
Subtotal: $48.71
Discount (20%): $9.74
Shipping: $11.13
Total: $50.10
Notes:
Thanks for your business!
Terms:
Order ID : CA-2012-AB10015140-40974"""

    document = DocumentExtractor._parse_layout_document(text, [text], [[['Item', 'Quantity', 'Rate', 'Amount']]], "invoice_36258.pdf")

    assert document.document_number == "36258"
    assert document.vendor.name == "SuperStore"
    assert len(document.line_items) == 1
    item = document.line_items[0]
    assert item.sku == "FUR-CH-4421"
    assert item.description == "Global Push Button Manager's Chair, Indigo Chairs, Furniture"
    assert item.quantity == 1
    assert item.unit_price == 48.71
    assert item.line_total == 48.71
    assert document.summary.subtotal == 48.71
    assert document.summary.discount_amount == 9.74
    assert document.summary.shipping_amount == 11.13
    assert document.summary.grand_total == 50.10
    assert document.vendor.phone is None


def test_empty_invoice_does_not_invent_vendor_or_document_number():
    text = """INVOICE
Date: Jun 5, 2023
Bill To: Balance Due: $0.00
Item Quantity Rate Amount
Total: $0.00"""

    document = DocumentExtractor._parse_layout_document(text, [text], [], "empty_invoice.pdf")

    assert document.document_number is None
    assert document.vendor.name is None
    assert document.vendor.phone is None
    assert document.line_items == []


def test_textless_pdf_does_not_return_fabricated_invoice_data(tmp_path):
    pdf_path = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with pdf_path.open("wb") as pdf_file:
        writer.write(pdf_file)

    image_data_urls = DocumentExtractor._pdf_image_data_urls(pdf_path)
    assert len(image_data_urls) == 1
    assert image_data_urls[0].startswith("data:image/jpeg;base64,")

    document = asyncio.run(DocumentExtractor.extract(pdf_path, account_settings={"default_provider": "heuristic"}))

    assert document.document_number is None
    assert document.vendor.name is None
    assert document.line_items == []
    assert document.verification.status.value == "FAILED"
    assert "No text could be extracted" in document.notes


def test_account_provider_uses_only_account_credentials(monkeypatch, tmp_path):
    file_path = tmp_path / "invoice.pdf"
    monkeypatch.setattr(settings, "gemini_api_key", "server-global-key")
    monkeypatch.setattr(settings, "default_provider", "auto")
    gemini_extractor = AsyncMock(return_value=ParsedDocumentData())
    monkeypatch.setattr(DocumentExtractor, "_extract_with_gemini", gemini_extractor)

    document = asyncio.run(DocumentExtractor.extract(file_path, account_settings={
        "default_provider": "auto",
        "gemini_api_key": "account-specific-key",
    }))

    gemini_extractor.assert_awaited_once_with(file_path, "application/pdf", "account-specific-key")
    assert document.document_number is None


def test_missing_account_provider_key_does_not_use_global_api_key(monkeypatch, tmp_path):
    file_path = tmp_path / "invoice.pdf"
    monkeypatch.setattr(settings, "gemini_api_key", "server-global-key")
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(settings, "default_provider", "auto")

    document = asyncio.run(DocumentExtractor.extract(file_path, account_settings={"default_provider": "auto"}))

    assert document.notes.startswith("No text could be extracted")

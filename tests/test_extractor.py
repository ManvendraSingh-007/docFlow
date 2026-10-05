from backend.services.extractor import DocumentExtractor


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
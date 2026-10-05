import pytest
from backend.models.document import (
    ParsedDocumentData,
    VendorInfo,
    BuyerInfo,
    LineItem,
    FinancialSummary,
    DocumentStatus
)
from backend.services.validator import DocumentValidator

def test_perfect_math_document_passes():
    doc = ParsedDocumentData(
        document_number="PO-99100",
        document_date="2026-10-01",
        line_items=[
            LineItem(line_no=1, sku="A1", description="Industrial Pump", quantity=2.0, unit_price=150.00, line_total=300.00),
            LineItem(line_no=2, sku="B2", description="Pressure Gauge", quantity=3.0, unit_price=25.00, line_total=75.00),
        ],
        summary=FinancialSummary(
            subtotal=375.00,
            tax_amount=30.00,
            shipping_amount=20.00,
            discount_amount=0.00,
            grand_total=425.00
        )
    )

    res = DocumentValidator.validate(doc)
    assert res.status == DocumentStatus.VERIFIED
    assert res.confidence_score >= 0.95
    assert len(res.flags) == 0
    assert all(c.passed for c in res.checks)

def test_line_item_math_discrepancy_flagged():
    doc = ParsedDocumentData(
        document_number="PO-99101",
        document_date="2026-10-01",
        line_items=[
            # 2 * 150 should be 300, but document says 350
            LineItem(line_no=1, sku="A1", description="Industrial Pump", quantity=2.0, unit_price=150.00, line_total=350.00),
        ],
        summary=FinancialSummary(
            subtotal=350.00,
            tax_amount=0.00,
            shipping_amount=0.00,
            grand_total=350.00
        )
    )

    res = DocumentValidator.validate(doc)
    assert res.status == DocumentStatus.REVIEW_REQUIRED
    assert any("Line Items Arithmetic" in c.name and not c.passed for c in res.checks)
    assert len(res.flags) > 0

def test_grand_total_mismatch_flagged():
    doc = ParsedDocumentData(
        document_number="PO-99102",
        document_date="2026-10-01",
        line_items=[
            LineItem(line_no=1, sku="A1", description="Valve", quantity=1.0, unit_price=100.00, line_total=100.00),
        ],
        summary=FinancialSummary(
            subtotal=100.00,
            tax_amount=10.00,
            shipping_amount=15.00,
            discount_amount=0.00,
            grand_total=180.00 # Expected is 125, mismatch of 55
        )
    )

    res = DocumentValidator.validate(doc)
    assert res.status == DocumentStatus.REVIEW_REQUIRED
    assert any("Grand Total Balance" in c.name and not c.passed for c in res.checks)

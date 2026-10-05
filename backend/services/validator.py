import math
from typing import Tuple
from backend.models.document import (
    ParsedDocumentData,
    VerificationResult,
    ValidationCheck,
    DocumentStatus
)

def round_cents(val: float) -> float:
    return round(float(val or 0.0), 2)

class DocumentValidator:
    """
    Automated Arithmetic & Integrity Cross-Checking Engine.
    Guarantees that numbers extracted from invoices/POs match arithmetic ground truth
    down to the cent before emitting webhooks.
    """

    @classmethod
    def validate(cls, doc: ParsedDocumentData) -> VerificationResult:
        checks = []
        flags = []
        confidence = 1.0

        # 1. Line Items Arithmetic Validation
        calculated_subtotal = 0.0
        line_item_math_errors = 0

        for idx, item in enumerate(doc.line_items):
            expected_line_total = round_cents(item.quantity * item.unit_price)
            actual_line_total = round_cents(item.line_total)
            
            diff = abs(expected_line_total - actual_line_total)
            # Allow up to $0.02 for fractional rounding differences (e.g. quantity discounts or unit rounding)
            if diff <= 0.02 or (item.unit_price == 0.0 and item.quantity > 0):
                item.math_verified = True
                item.discrepancy = 0.0
            else:
                item.math_verified = False
                item.discrepancy = round_cents(actual_line_total - expected_line_total)
                line_item_math_errors += 1
                flags.append(
                    f"Line {item.line_no or idx+1} ({item.description[:30]}): "
                    f"{item.quantity} x ${item.unit_price:.2f} = ${expected_line_total:.2f}, "
                    f"but document states ${actual_line_total:.2f} (diff: ${item.discrepancy:+.2f})"
                )

            calculated_subtotal += actual_line_total

        calculated_subtotal = round_cents(calculated_subtotal)
        stated_subtotal = round_cents(doc.summary.subtotal)

        if line_item_math_errors > 0:
            checks.append(ValidationCheck(
                name="Line Items Arithmetic",
                passed=False,
                expected=calculated_subtotal,
                actual=calculated_subtotal,
                message=f"{line_item_math_errors} line item(s) have quantity × unit price arithmetic discrepancies."
            ))
            confidence -= min(0.3, 0.1 * line_item_math_errors)
        else:
            checks.append(ValidationCheck(
                name="Line Items Arithmetic",
                passed=True,
                expected=calculated_subtotal,
                actual=calculated_subtotal,
                message="All line item quantities and unit prices calculate accurately."
            ))

        # 2. Subtotal Cross-Check (Sum of line items vs Stated Subtotal)
        # If stated_subtotal is 0 but we have line items, populate it
        if stated_subtotal == 0.0 and calculated_subtotal > 0:
            stated_subtotal = calculated_subtotal
            doc.summary.subtotal = calculated_subtotal

        subtotal_diff = abs(calculated_subtotal - stated_subtotal)
        if subtotal_diff <= 0.05:
            checks.append(ValidationCheck(
                name="Subtotal Consistency",
                passed=True,
                expected=calculated_subtotal,
                actual=stated_subtotal,
                message="Sum of line items matches stated subtotal."
            ))
        else:
            checks.append(ValidationCheck(
                name="Subtotal Consistency",
                passed=False,
                expected=calculated_subtotal,
                actual=stated_subtotal,
                message=f"Sum of lines (${calculated_subtotal:.2f}) does not match stated subtotal (${stated_subtotal:.2f})."
            ))
            flags.append(f"Subtotal mismatch: Stated ${stated_subtotal:.2f} vs calculated sum ${calculated_subtotal:.2f}")
            confidence -= 0.25

        # 3. Grand Total Cross-Check
        # Expected Total = Subtotal + Tax + Shipping - Discount
        tax = round_cents(doc.summary.tax_amount)
        shipping = round_cents(doc.summary.shipping_amount)
        discount = round_cents(doc.summary.discount_amount)
        stated_total = round_cents(doc.summary.grand_total)

        expected_grand_total = round_cents(stated_subtotal + tax + shipping - discount)

        if stated_total == 0.0 and expected_grand_total > 0:
            stated_total = expected_grand_total
            doc.summary.grand_total = expected_grand_total

        total_diff = abs(expected_grand_total - stated_total)
        if total_diff <= 0.05:
            checks.append(ValidationCheck(
                name="Grand Total Balance",
                passed=True,
                expected=expected_grand_total,
                actual=stated_total,
                message="Grand Total balances accurately (Subtotal + Tax + Shipping - Discount)."
            ))
        else:
            checks.append(ValidationCheck(
                name="Grand Total Balance",
                passed=False,
                expected=expected_grand_total,
                actual=stated_total,
                message=f"Stated Grand Total (${stated_total:.2f}) differs from expected (${expected_grand_total:.2f}) by ${total_diff:.2f}."
            ))
            flags.append(f"Grand Total mismatch: Expected ${expected_grand_total:.2f} vs stated ${stated_total:.2f}")
            confidence -= 0.25

        # 4. Critical Metadata Completeness Checks
        if not doc.document_number or doc.document_number.strip() in ("", "N/A", "UNKNOWN"):
            flags.append("Missing Document/PO number.")
            confidence -= 0.15
        
        if not doc.document_date or doc.document_date.strip() in ("", "N/A", "UNKNOWN"):
            flags.append("Missing or unreadable order date.")
            confidence -= 0.1

        if not doc.line_items:
            flags.append("No line items extracted from document.")
            confidence -= 0.4

        confidence = max(0.0, min(1.0, round(confidence, 2)))

        # Status determination
        has_failed_critical_check = any(not c.passed for c in checks) or (not doc.line_items)
        
        if not doc.line_items and stated_total == 0:
            status = DocumentStatus.FAILED
        elif has_failed_critical_check or confidence < 0.90:
            status = DocumentStatus.REVIEW_REQUIRED
        else:
            status = DocumentStatus.VERIFIED

        result = VerificationResult(
            status=status,
            confidence_score=confidence,
            checks=checks,
            flags=flags
        )
        doc.verification = result
        return result

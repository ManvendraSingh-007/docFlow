import re
from backend.models.document import (
    ParsedDocumentData,
    VerificationResult,
    ValidationCheck,
    DocumentStatus
)

def round_cents(val: float) -> float:
    if val in (None, ""):
        return 0.0
    try:
        return round(float(val), 2)
    except (TypeError, ValueError):
        match = re.search(r"[-+]?\d[\d,]*(?:\.\d+)?", str(val))
        return round(float(match.group(0).replace(",", "")), 2) if match else 0.0

class DocumentValidator:
    """
    Validate only arithmetic rules identified from the current document.
    """

    @staticmethod
    def _expected_line_total(item, basis):
        values = item.pricing_adjustments or {}
        if not all(role in values for role in ("quantity", "unit_price", "line_total")):
            return None
        quantity = item.quantity
        price = item.unit_price
        if basis == "quantity_times_unit_price":
            return round_cents(quantity * price)
        if basis == "quantity_times_unit_price_less_discount_amount":
            if "line_discount_amount" not in values:
                return None
            return round_cents(quantity * price - round_cents(values["line_discount_amount"]))
        if basis == "quantity_times_unit_price_less_discount_rate":
            if "line_discount_rate" not in values:
                return None
            return round_cents(quantity * price * (1 - round_cents(values["line_discount_rate"]) / 100))
        if basis == "quantity_times_net_unit_price":
            if "net_unit_price" not in values:
                return None
            return round_cents(quantity * round_cents(values["net_unit_price"]))
        if basis == "quantity_times_unit_price_plus_line_tax":
            if "line_tax_amount" not in values:
                return None
            return round_cents(quantity * price + round_cents(values["line_tax_amount"]))
        if basis == "quantity_times_unit_price_less_discount_amount_plus_line_tax":
            if not all(role in values for role in ("line_discount_amount", "line_tax_amount")):
                return None
            return round_cents(quantity * price - round_cents(values["line_discount_amount"]) + round_cents(values["line_tax_amount"]))
        if basis == "quantity_times_unit_price_less_discount_rate_plus_line_tax":
            if not all(role in values for role in ("line_discount_rate", "line_tax_amount")):
                return None
            net = quantity * price * (1 - round_cents(values["line_discount_rate"]) / 100)
            return round_cents(net + round_cents(values["line_tax_amount"]))
        return None

    @staticmethod
    def _document_roles(doc):
        roles = set()
        data = doc.document_data or {}
        for section in data.get("sections", []):
            if isinstance(section, dict):
                roles.update(
                    field.get("role") for field in section.get("fields", [])
                    if isinstance(field, dict) and field.get("role") and field.get("value") not in (None, "")
                )
        for table in data.get("tables", []):
            if isinstance(table, dict):
                columns = table.get("columns", [])
                rows = table.get("rows", [])
                for index, column in enumerate(columns):
                    if not isinstance(column, dict) or not column.get("role"):
                        continue
                    if any(isinstance(row, list) and index < len(row) and row[index] not in (None, "") for row in rows):
                        roles.add(column["role"])
        return roles

    @classmethod
    def _expected_grand_total(cls, summary, basis):
        formulas = {
            "subtotal_plus_tax_shipping_less_discount": ("subtotal", "tax_amount", "shipping_amount", "discount_amount"),
            "subtotal_plus_tax_shipping": ("subtotal", "tax_amount", "shipping_amount"),
            "subtotal_plus_tax_less_discount": ("subtotal", "tax_amount", "discount_amount"),
            "subtotal_plus_tax": ("subtotal", "tax_amount"),
            "subtotal_plus_shipping": ("subtotal", "shipping_amount"),
            "subtotal_less_discount": ("subtotal", "discount_amount"),
        }
        required = formulas.get(basis)
        if not required:
            return None
        values = {"subtotal": summary.subtotal, "tax_amount": summary.tax_amount,
                  "shipping_amount": summary.shipping_amount, "discount_amount": summary.discount_amount}
        if not all(value is not None for value in (values.get(role) for role in required)):
            return None
        value = values["subtotal"]
        if "tax_amount" in required:
            value += values["tax_amount"]
        if "shipping_amount" in required:
            value += values["shipping_amount"]
        if "discount_amount" in required:
            value -= values["discount_amount"]
        return round_cents(value)

    @classmethod
    def validate(cls, doc: ParsedDocumentData) -> VerificationResult:
        checks = []
        flags = []
        confidence = 1.0

        rules = (doc.document_data or {}).get("arithmetic_rules") or {}
        if not isinstance(rules, dict):
            rules = {}
        line_basis = rules.get("line_total_basis")
        subtotal_basis = rules.get("subtotal_basis")
        grand_basis = rules.get("grand_total_basis")
        calculated_subtotal = 0.0
        line_item_mismatches = 0
        line_items_unchecked = 0
        expected_lines_total = 0.0
        unresolved = False

        for idx, item in enumerate(doc.line_items):
            actual_line_total = round_cents(item.line_total)
            expected_line_total = cls._expected_line_total(item, line_basis)
            if expected_line_total is None:
                item.math_verified = None
                item.discrepancy = 0.0
                line_items_unchecked += 1
                unresolved = True
                calculated_subtotal += actual_line_total
                continue
            diff = abs(expected_line_total - actual_line_total)
            expected_lines_total += expected_line_total
            if diff <= 0.02:
                item.math_verified = True
                item.discrepancy = 0.0
            else:
                item.math_verified = False
                item.discrepancy = round_cents(actual_line_total - expected_line_total)
                line_item_mismatches += 1

            calculated_subtotal += actual_line_total

        calculated_subtotal = round_cents(calculated_subtotal)
        stated_subtotal = round_cents(doc.summary.subtotal)

        if not doc.line_items or line_items_unchecked > 0:
            checks.append(ValidationCheck(
                name="Line Items Arithmetic",
                passed=False,
                applicable=False,
                expected=0.0,
                actual=0.0,
                message=("Not checked because the document has no recognized item table." if not doc.line_items else
                         "Not checked because the AI could not identify a supported line amount formula and its required values for every row.")
            ))
            unresolved = True
        elif line_item_mismatches > 0:
            checks.append(ValidationCheck(
                name="Line Items Arithmetic",
                passed=False,
                expected=round_cents(expected_lines_total),
                actual=calculated_subtotal,
                message=f"{line_item_mismatches} row(s) do not match the line amount rule identified from this document."
            ))
            flags.extend(
                f"Line {item.line_no} ({item.description[:30]}): calculated amount differs from the extracted line amount by ${item.discrepancy:+.2f}."
                for item in doc.line_items if item.math_verified is False
            )
            confidence -= min(0.3, 0.1 * line_item_mismatches)
        else:
            checks.append(ValidationCheck(
                name="Line Items Arithmetic",
                passed=True,
                expected=calculated_subtotal,
                actual=calculated_subtotal,
                message="All line amounts match the formula identified from this document."
            ))

        subtotal_diff = abs(calculated_subtotal - stated_subtotal)
        has_stated_subtotal = "subtotal" in cls._document_roles(doc)
        if subtotal_basis != "sum_of_line_totals" or not doc.line_items or not has_stated_subtotal or stated_subtotal == 0.0 or line_items_unchecked:
            checks.append(ValidationCheck(
                name="Subtotal Consistency",
                passed=False,
                applicable=False,
                expected=calculated_subtotal,
                actual=stated_subtotal,
                message="Not checked because the AI did not identify the stated subtotal as the sum of the displayed line totals."
            ))
            unresolved = True
        elif subtotal_diff <= 0.05:
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
                message=f"The line totals sum to ${calculated_subtotal:.2f}; the stated subtotal is ${stated_subtotal:.2f}."
            ))
            flags.append(f"Subtotal differs from the sum of the line totals by ${stated_subtotal - calculated_subtotal:+.2f}.")
            confidence -= 0.25

        stated_total = round_cents(doc.summary.grand_total)
        has_extracted_content = bool(
            doc.document_number or doc.document_date or doc.vendor.name or doc.buyer.company
            or doc.line_items or stated_subtotal or stated_total
            or any(isinstance(section, dict) and section.get("fields") for section in (doc.document_data or {}).get("sections", []))
            or any(isinstance(table, dict) and table.get("rows") for table in (doc.document_data or {}).get("tables", []))
        )

        checks.append(ValidationCheck(
            name="Invoice data detected",
            passed=has_extracted_content,
            expected=1.0,
            actual=1.0 if has_extracted_content else 0.0,
            message="Invoice details were extracted." if has_extracted_content else "No invoice details were recognized in this file."
        ))

        grand_roles_required = {
            "subtotal_plus_tax_shipping_less_discount": ("subtotal", "tax_amount", "shipping_amount", "discount_amount", "grand_total"),
            "subtotal_plus_tax_shipping": ("subtotal", "tax_amount", "shipping_amount", "grand_total"),
            "subtotal_plus_tax_less_discount": ("subtotal", "tax_amount", "discount_amount", "grand_total"),
            "subtotal_plus_tax": ("subtotal", "tax_amount", "grand_total"),
            "subtotal_plus_shipping": ("subtotal", "shipping_amount", "grand_total"),
            "subtotal_less_discount": ("subtotal", "discount_amount", "grand_total"),
        }
        grand_roles = cls._document_roles(doc)
        required_roles = grand_roles_required.get(grand_basis, ())
        expected_grand_total = cls._expected_grand_total(doc.summary, grand_basis) if required_roles and all(role in grand_roles for role in required_roles) else None
        total_diff = abs(expected_grand_total - stated_total) if expected_grand_total is not None else 0.0
        if expected_grand_total is None or stated_total == 0.0:
            checks.append(ValidationCheck(
                name="Grand Total Balance",
                passed=False,
                applicable=False,
                expected=expected_grand_total or 0.0,
                actual=stated_total,
                message="Not checked because the AI could not identify a supported grand total formula and its required values."
            ))
            unresolved = True
        elif total_diff <= 0.05:
            checks.append(ValidationCheck(
                name="Grand Total Balance",
                passed=True,
                expected=expected_grand_total,
                actual=stated_total,
                message="The stated grand total matches the formula identified from this document."
            ))
        else:
            checks.append(ValidationCheck(
                name="Grand Total Balance",
                passed=False,
                expected=expected_grand_total,
                actual=stated_total,
                message=f"The grand total differs from this document's identified formula result of ${expected_grand_total:.2f}."
            ))
            flags.append(f"Grand total differs from the identified formula result by ${stated_total - expected_grand_total:+.2f}.")
            confidence -= 0.25

        if unresolved:
            flags.append("Some arithmetic could not be checked because the document-specific pricing formula or required values were not explicit in the extracted data.")
            confidence = min(confidence, 0.7)
        confidence = max(0.0, min(1.0, round(confidence, 2)))

        financial_checks = [
            check for check in checks
            if check.name in {"Line Items Arithmetic", "Subtotal Consistency", "Grand Total Balance"}
            and check.applicable
        ]
        has_failed_check = any(not check.passed for check in financial_checks)

        if not has_extracted_content:
            status = DocumentStatus.FAILED
        elif unresolved or not financial_checks or has_failed_check:
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

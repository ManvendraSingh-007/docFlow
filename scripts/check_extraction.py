#!/usr/bin/env python3
"""Run a selected AI extraction provider over every PDF in a file or directory."""

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from backend.models.document import DocumentStatus
from backend.config import settings
from backend.services.extractor import DocumentExtractor


def display(value: str | None, fallback: str = "—") -> str:
    return " ".join(value.split()) if value and value.strip() else fallback


async def inspect_pdf(path: Path, provider: str) -> tuple[str, str]:
    try:
        credentials = {
            "default_provider": provider,
            "gemini_api_key": settings.gemini_api_key,
            "openai_api_key": settings.openai_api_key,
            "mistral_api_key": os.getenv("MISTRAL_API_KEY", ""),
            "openrouter_api_key": os.getenv("OPENROUTER_API_KEY", ""),
            "gemini_model": os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite"),
            "openai_model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            "mistral_model": os.getenv("MISTRAL_MODEL", "mistral-small-2506"),
            "openrouter_model": os.getenv("OPENROUTER_MODEL", "google/gemini-2.5-flash"),
        }
        document = await DocumentExtractor.extract(
            path,
            "application/pdf",
            account_settings=credentials,
        )
        verification = document.verification
        status = verification.status if verification else DocumentStatus.FAILED
        label = {
            DocumentStatus.VERIFIED: "PASS",
            DocumentStatus.REVIEW_REQUIRED: "CHECK",
            DocumentStatus.FAILED: "FAIL",
        }.get(status, "FAIL")
        sparse_fields = []
        if not document.vendor.name:
            sparse_fields.append("vendor")
        if not document.line_items:
            sparse_fields.append("line items")
        if not document.summary or document.summary.grand_total <= 0:
            sparse_fields.append("grand total")
        if label == "PASS" and sparse_fields:
            label = "CHECK"
        vendor = display(document.vendor.name)
        number = display(document.document_number)
        total = document.summary.grand_total if document.summary else None
        total_text = f"{document.summary.currency} {total:,.2f}" if total is not None else "—"
        print(
            f"[{label:<5}] {path} | {status.value} | {document.document_type}"
            f" | No. {number} | Vendor: {vendor} | Lines: {len(document.line_items)}"
            f" | Total: {total_text}"
        )
        if document.notes:
            print(f"        Note: {display(document.notes)}")
        if sparse_fields:
            print(f"        Review fields: {', '.join(sparse_fields)}")
        if verification and verification.flags:
            print(f"        Flags: {'; '.join(verification.flags)}")
        return label, display(document.notes, "")
    except Exception as exc:
        print(f"[ERROR] {path} | {type(exc).__name__}: {exc}")
        return "ERROR", str(exc)


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract PDFs with a selected AI provider and summarize validation results."
    )
    parser.add_argument("--provider", required=True, choices=("gemini", "openai", "mistral", "openrouter"), help="AI provider to call for every PDF")
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=Path("sample_data"),
        help="PDF file or directory to scan recursively (default: sample_data)",
    )
    args = parser.parse_args()
    target = args.path.resolve()
    if not target.exists():
        parser.error(f"Path does not exist: {target}")
    if target.is_file():
        pdfs = [target] if target.suffix.lower() == ".pdf" else []
    else:
        pdfs = sorted(path for path in target.rglob("*") if path.is_file() and path.suffix.lower() == ".pdf")
    if not pdfs:
        parser.error(f"No PDF files found at: {target}")

    print(f"Checking {len(pdfs)} PDF(s) with {args.provider}. This sends each PDF to that provider and may incur usage charges.\n")
    counts = {"PASS": 0, "CHECK": 0, "FAIL": 0, "ERROR": 0}
    for path in pdfs:
        label, _ = await inspect_pdf(path, args.provider)
        counts[label] += 1

    completed = counts["PASS"] + counts["CHECK"] + counts["FAIL"]
    print("\nExtraction summary")
    print(f"  PDFs found:          {len(pdfs)}")
    print(f"  Processed:           {completed}")
    print(f"  Verified:            {counts['PASS']}")
    print(f"  Review / sparse data:{counts['CHECK']:>6}")
    print(f"  Failed validation:   {counts['FAIL']}")
    print(f"  Extraction errors:   {counts['ERROR']}")
    print("\nPASS means built-in validation passed with basic fields present; CHECK means review is needed.")
    print("This report checks extraction and arithmetic validation, not field-level accuracy against ground truth.")
    return 1 if counts["FAIL"] or counts["ERROR"] else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

import asyncio
from pathlib import Path
from backend.database import init_db
from backend.api.documents import process_and_dispatch
from backend.database import DocumentRepository

BASE_DIR = Path(__file__).resolve().parent.parent
SAMPLES_DIR = BASE_DIR / "sample_data"

async def seed():
    init_db()
    
    # 1. Seed Verified PO
    po_file = SAMPLES_DIR / "sample_po_verified.pdf"
    if po_file.exists():
        doc_id = "demo-verified-po"
        existing = DocumentRepository.get_by_id(doc_id)
        if not existing:
            DocumentRepository.create(
                doc_id=doc_id,
                filename="sample_po_verified.pdf",
                original_filename="PO_Apex_Precision_98241.pdf",
                file_path=str(po_file),
                file_size=po_file.stat().st_size,
                file_type="PDF",
                source="email_inbound",
                sender_email="orders@vanguardtooling.com"
            )
            # Copy to uploads so preview works
            upload_target = BASE_DIR / "data" / "uploads" / "sample_po_verified.pdf"
            with open(po_file, "rb") as src, open(upload_target, "wb") as dst:
                dst.write(src.read())

            await process_and_dispatch(doc_id, po_file, "application/pdf")
            print("✓ Seeded sample_po_verified.pdf")

    # 2. Seed Discrepancy Invoice
    inv_file = SAMPLES_DIR / "sample_invoice_discrepancy.pdf"
    if inv_file.exists():
        doc_id = "demo-review-invoice"
        existing = DocumentRepository.get_by_id(doc_id)
        if not existing:
            DocumentRepository.create(
                doc_id=doc_id,
                filename="sample_invoice_discrepancy.pdf",
                original_filename="INV_Midwest_Hydraulics_30491.pdf",
                file_path=str(inv_file),
                file_size=inv_file.stat().st_size,
                file_type="PDF",
                source="manual_upload"
            )
            # Copy to uploads so preview works
            upload_target = BASE_DIR / "data" / "uploads" / "sample_invoice_discrepancy.pdf"
            with open(inv_file, "rb") as src, open(upload_target, "wb") as dst:
                dst.write(src.read())

            await process_and_dispatch(doc_id, inv_file, "application/pdf")
            print("✓ Seeded sample_invoice_discrepancy.pdf")

if __name__ == "__main__":
    asyncio.run(seed())

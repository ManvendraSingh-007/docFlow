import uuid
import csv
import io
from pathlib import Path
from typing import List, Optional
from fastapi import APIRouter, UploadFile, File, HTTPException, BackgroundTasks, Query
from fastapi.responses import JSONResponse, StreamingResponse
from backend.config import settings
from backend.database import DocumentRepository, WebhookLogRepository
from backend.models.document import (
    ParsedDocumentData,
    DocumentStatus,
    WebhookStatus
)
from backend.services.extractor import DocumentExtractor
from backend.services.validator import DocumentValidator
from backend.services.webhook_dispatcher import WebhookDispatcher

router = APIRouter(prefix="/api/documents", tags=["documents"])

async def process_and_dispatch(doc_id: str, file_path: Path, mime_type: str):
    try:
        # Extract structured data
        parsed_data = await DocumentExtractor.extract(file_path, mime_type)
        
        # Check if auto-dispatch is enabled and document is VERIFIED
        webhook_status = WebhookStatus.PENDING
        webhook_code = None

        if settings.default_webhook_url and settings.auto_dispatch_on_verified:
            if parsed_data.verification and parsed_data.verification.status == DocumentStatus.VERIFIED:
                success, code, body, duration = await WebhookDispatcher.dispatch(
                    settings.default_webhook_url,
                    {
                        "event": "document.verified",
                        "document_id": doc_id,
                        "data": parsed_data.model_dump()
                    }
                )
                webhook_status = WebhookStatus.DELIVERED if success else WebhookStatus.FAILED
                webhook_code = code
                WebhookLogRepository.log(doc_id, settings.default_webhook_url, code, body, duration, success)

        DocumentRepository.update_parsed_data(doc_id, parsed_data, webhook_status, webhook_code)
    except Exception as e:
        print(f"[Worker] Error processing document {doc_id}: {e}")
        # Update as failed
        empty_data = ParsedDocumentData()
        empty_data.notes = f"Extraction failed: {str(e)}"
        DocumentValidator.validate(empty_data)
        DocumentRepository.update_parsed_data(doc_id, empty_data, WebhookStatus.FAILED)

@router.post("/upload")
async def upload_documents(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...)
):
    """
    Accepts single or batch uploads of Purchase Orders, Invoices, Delivery Slips (PDF, PNG, JPG).
    Initiates asynchronous extraction and math integrity checks.
    """
    results = []
    for upload in files:
        doc_id = str(uuid.uuid4())
        ext = Path(upload.filename).suffix.lower()
        if ext not in [".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".webp"]:
            continue

        stored_filename = f"{doc_id}{ext}"
        saved_path = settings.uploads_dir / stored_filename
        
        content = await upload.read()
        file_size = len(content)
        with open(saved_path, "wb") as f:
            f.write(content)

        DocumentRepository.create(
            doc_id=doc_id,
            filename=stored_filename,
            original_filename=upload.filename,
            file_path=str(saved_path),
            file_size=file_size,
            file_type=ext.replace(".", "").upper(),
            source="manual_upload"
        )

        # Trigger processing in background
        background_tasks.add_task(process_and_dispatch, doc_id, saved_path, upload.content_type or "application/pdf")
        
        results.append({
            "id": doc_id,
            "filename": upload.filename,
            "status": "PROCESSING",
            "message": "File received and queued for parsing & arithmetic verification."
        })

    return {"uploaded": len(results), "documents": results}

@router.get("")
def list_documents(status: Optional[str] = Query(None), limit: int = 100):
    docs = DocumentRepository.list_all(limit=limit, status=status)
    stats = DocumentRepository.get_stats()
    return {"stats": stats, "documents": docs}

@router.get("/{doc_id}")
def get_document(doc_id: str):
    doc = DocumentRepository.get_by_id(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    logs = WebhookLogRepository.list_by_document(doc_id)
    return {"document": doc, "webhook_logs": logs}

@router.put("/{doc_id}")
def update_document_data(doc_id: str, updated_data: ParsedDocumentData):
    """
    Allows human-in-the-loop review and correction.
    Re-runs arithmetic validator on corrected values and updates record.
    """
    doc = DocumentRepository.get_by_id(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Re-validate math
    DocumentValidator.validate(updated_data)
    DocumentRepository.update_parsed_data(doc_id, updated_data)
    
    return {"message": "Document updated and re-verified", "verification": updated_data.verification}

@router.post("/{doc_id}/dispatch")
async def manual_dispatch_webhook(doc_id: str, target_url: Optional[str] = None):
    doc = DocumentRepository.get_by_id(doc_id)
    if not doc or not doc.get("data"):
        raise HTTPException(status_code=404, detail="Document data not ready")

    url = target_url or settings.default_webhook_url
    if not url:
        raise HTTPException(status_code=400, detail="No webhook destination URL provided.")

    success, code, body, duration = await WebhookDispatcher.dispatch(
        url,
        {
            "event": "document.manual_dispatch",
            "document_id": doc_id,
            "data": doc["data"]
        }
    )

    webhook_status = WebhookStatus.DELIVERED if success else WebhookStatus.FAILED
    DocumentRepository.update_webhook_status(doc_id, webhook_status, code)
    WebhookLogRepository.log(doc_id, url, code, body, duration, success)

    return {
        "success": success,
        "status_code": code,
        "duration_ms": duration,
        "response": body
    }

@router.get("/{doc_id}/export/csv")
def export_csv(doc_id: str):
    doc = DocumentRepository.get_by_id(doc_id)
    if not doc or not doc.get("data"):
        raise HTTPException(status_code=404, detail="Document data not found")

    data = doc["data"]
    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow([
        "PO/Doc Number", "Date", "Vendor", "Buyer", "Line No",
        "SKU", "Description", "Quantity", "Unit Price", "Line Total",
        "Subtotal", "Tax", "Shipping", "Grand Total"
    ])

    po_num = data.get("document_number", "")
    po_date = data.get("document_date", "")
    vendor = data.get("vendor", {}).get("name", "")
    buyer = data.get("buyer", {}).get("company", "")
    subtotal = data.get("summary", {}).get("subtotal", 0.0)
    tax = data.get("summary", {}).get("tax_amount", 0.0)
    shipping = data.get("summary", {}).get("shipping_amount", 0.0)
    total = data.get("summary", {}).get("grand_total", 0.0)

    for item in data.get("line_items", []):
        writer.writerow([
            po_num, po_date, vendor, buyer,
            item.get("line_no"), item.get("sku"), item.get("description"),
            item.get("quantity"), item.get("unit_price"), item.get("line_total"),
            subtotal, tax, shipping, total
        ])

    output.seek(0)
    filename = f"po_export_{po_num or doc_id[:8]}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@router.delete("/{doc_id}")
def delete_document(doc_id: str):
    success = DocumentRepository.delete(doc_id)
    if not success:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"message": "Document deleted successfully"}

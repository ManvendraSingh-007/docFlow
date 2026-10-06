import uuid
import csv
import io
from pathlib import Path
from typing import List, Optional
from fastapi import APIRouter, UploadFile, File, HTTPException, BackgroundTasks, Query, Depends
from fastapi.responses import StreamingResponse, FileResponse
from backend.config import settings
from backend.database import DocumentRepository, WebhookLogRepository, AccountSettingsRepository
from backend.auth import current_user
from backend.models.document import (
    ParsedDocumentData,
    DocumentStatus,
    WebhookStatus
)
from backend.services.extractor import DocumentExtractor
from backend.services.validator import DocumentValidator
from backend.services.webhook_dispatcher import WebhookDispatcher

router = APIRouter(prefix="/api/documents", tags=["documents"])

async def process_and_dispatch(doc_id: str, file_path: Path, mime_type: str, user_id: str):
    try:
        account_settings = AccountSettingsRepository.get(user_id)
        # Extract structured data
        parsed_data = await DocumentExtractor.extract(file_path, mime_type, account_settings)
        
        # Check if auto-dispatch is enabled and document is VERIFIED
        webhook_status = WebhookStatus.PENDING
        webhook_code = None

        webhook_url = account_settings.get("webhook_url", "")
        webhook_secret = account_settings.get("webhook_secret", "")
        if webhook_url and webhook_secret and account_settings.get("auto_dispatch_on_verified"):
            if parsed_data.verification and parsed_data.verification.status == DocumentStatus.VERIFIED:
                success, code, body, duration = await WebhookDispatcher.dispatch(
                    webhook_url,
                    {
                        "event": "document.verified",
                        "document_id": doc_id,
                        "data": parsed_data.model_dump()
                    },
                    webhook_secret,
                )
                webhook_status = WebhookStatus.DELIVERED if success else WebhookStatus.FAILED
                webhook_code = code
                WebhookLogRepository.log(doc_id, webhook_url, code, body, duration, success)

        DocumentRepository.update_parsed_data(doc_id, parsed_data, webhook_status, webhook_code, user_id)
    except Exception as e:
        print(f"[Worker] Error processing document {doc_id}: {e}")
        # Update as failed
        empty_data = ParsedDocumentData()
        empty_data.notes = f"Extraction failed: {str(e)}"
        DocumentValidator.validate(empty_data)
        DocumentRepository.update_parsed_data(doc_id, empty_data, WebhookStatus.FAILED, user_id=user_id)

@router.post("/upload")
async def upload_documents(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    user: dict = Depends(current_user),
):
    """
    Accepts single or batch uploads of Purchase Orders, Invoices, Delivery Slips (PDF, PNG, JPG).
    Initiates asynchronous extraction and math integrity checks.
    """
    results = []
    user_upload_dir = settings.uploads_dir / user["id"]
    user_upload_dir.mkdir(parents=True, exist_ok=True)
    for upload in files:
        doc_id = str(uuid.uuid4())
        ext = Path(upload.filename).suffix.lower()
        if ext not in [".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".webp"]:
            continue

        stored_filename = f"{doc_id}{ext}"
        saved_path = user_upload_dir / stored_filename
        
        content = await upload.read()
        file_size = len(content)
        if file_size > 25 * 1024 * 1024:
            continue
        with open(saved_path, "wb") as f:
            f.write(content)

        DocumentRepository.create(
            doc_id=doc_id,
            filename=stored_filename,
            original_filename=upload.filename,
            file_path=str(saved_path),
            file_size=file_size,
            file_type=ext.replace(".", "").upper(),
            source="manual_upload",
            user_id=user["id"],
        )

        # Trigger processing in background
        background_tasks.add_task(process_and_dispatch, doc_id, saved_path, upload.content_type or "application/pdf", user["id"])
        
        results.append({
            "id": doc_id,
            "filename": upload.filename,
            "status": "PROCESSING",
            "message": "File received and queued for parsing & arithmetic verification."
        })

    return {"uploaded": len(results), "documents": results}

@router.get("")
def list_documents(status: Optional[str] = Query(None), limit: int = 100, user: dict = Depends(current_user)):
    docs = DocumentRepository.list_all(limit=min(limit, 100), status=status, user_id=user["id"])
    stats = DocumentRepository.get_stats(user_id=user["id"])
    return {"stats": stats, "documents": docs}

@router.get("/{doc_id}")
def get_document(doc_id: str, user: dict = Depends(current_user)):
    doc = DocumentRepository.get_by_id(doc_id, user_id=user["id"])
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    logs = WebhookLogRepository.list_by_document(doc_id)
    return {"document": doc, "webhook_logs": logs}

@router.put("/{doc_id}")
def update_document_data(doc_id: str, updated_data: ParsedDocumentData, user: dict = Depends(current_user)):
    """
    Allows human-in-the-loop review and correction.
    Re-runs arithmetic validator on corrected values and updates record.
    """
    doc = DocumentRepository.get_by_id(doc_id, user_id=user["id"])
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Re-validate math
    DocumentValidator.validate(updated_data)
    DocumentRepository.update_parsed_data(doc_id, updated_data, user_id=user["id"])
    
    return {"message": "Document updated and re-verified", "verification": updated_data.verification}

@router.post("/{doc_id}/dispatch")
async def manual_dispatch_webhook(doc_id: str, target_url: Optional[str] = None, user: dict = Depends(current_user)):
    doc = DocumentRepository.get_by_id(doc_id, user_id=user["id"])
    if not doc or not doc.get("data"):
        raise HTTPException(status_code=404, detail="Document data not ready")

    account_settings = AccountSettingsRepository.get(user["id"])
    url = target_url or account_settings.get("webhook_url", "")
    if not url:
        raise HTTPException(status_code=400, detail="No webhook destination URL provided.")
    if not account_settings.get("webhook_secret"):
        raise HTTPException(status_code=400, detail="Set a webhook signing secret in account settings first.")

    success, code, body, duration = await WebhookDispatcher.dispatch(
        url,
        {
            "event": "document.manual_dispatch",
            "document_id": doc_id,
            "data": doc["data"]
        },
        account_settings["webhook_secret"],
    )

    webhook_status = WebhookStatus.DELIVERED if success else WebhookStatus.FAILED
    DocumentRepository.update_webhook_status(doc_id, webhook_status, code, user["id"])
    WebhookLogRepository.log(doc_id, url, code, body, duration, success)

    return {
        "success": success,
        "status_code": code,
        "duration_ms": duration,
        "response": body
    }

@router.get("/{doc_id}/export/csv")
def export_csv(doc_id: str, user: dict = Depends(current_user)):
    doc = DocumentRepository.get_by_id(doc_id, user_id=user["id"])
    if not doc or not doc.get("data"):
        raise HTTPException(status_code=404, detail="Document data not found")

    data = doc["data"]
    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow([
        "PO/Doc Number", "Date", "Vendor", "Buyer", "Line No",
        "SKU", "Description", "Quantity", "Unit Price", "Line Total",
        "Subtotal", "Tax", "Shipping", "Grand Total", "Currency"
    ])

    po_num = data.get("document_number", "")
    po_date = data.get("document_date", "")
    vendor = data.get("vendor", {}).get("name", "")
    buyer = data.get("buyer", {}).get("company", "")
    subtotal = data.get("summary", {}).get("subtotal", 0.0)
    tax = data.get("summary", {}).get("tax_amount", 0.0)
    shipping = data.get("summary", {}).get("shipping_amount", 0.0)
    total = data.get("summary", {}).get("grand_total", 0.0)
    currency = data.get("summary", {}).get("currency", "USD")

    for item in data.get("line_items", []):
        writer.writerow([
            po_num, po_date, vendor, buyer,
            item.get("line_no"), item.get("sku"), item.get("description"),
            item.get("quantity"), item.get("unit_price"), item.get("line_total"),
            subtotal, tax, shipping, total, currency
        ])

    output.seek(0)
    filename = f"po_export_{po_num or doc_id[:8]}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@router.delete("/{doc_id}")
def delete_document(doc_id: str, user: dict = Depends(current_user)):
    success = DocumentRepository.delete(doc_id, user["id"])
    if not success:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"message": "Document deleted successfully"}


@router.get("/{doc_id}/file")
def get_uploaded_file(doc_id: str, user: dict = Depends(current_user)):
    doc = DocumentRepository.get_by_id(doc_id, user_id=user["id"])
    if not doc:
        raise HTTPException(status_code=404, detail="File not found")
    file_path = Path(doc["file_path"])
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_path, media_type="application/pdf" if doc["file_type"] == "PDF" else None)

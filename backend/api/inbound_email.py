import uuid
from typing import List
from fastapi import APIRouter, Request, BackgroundTasks, UploadFile, File
from backend.config import settings
from backend.database import DocumentRepository
from backend.services.email_receiver import EmailReceiverService
from backend.api.documents import process_and_dispatch

router = APIRouter(prefix="/api/inbound", tags=["inbound_email"])

@router.post("/postmark")
async def postmark_inbound_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Receives inbound email webhook from Postmark Inbound.
    Extracts attachments (PDFs/images), saves and processes them.
    """
    payload = await request.json()
    message = await EmailReceiverService.parse_postmark(payload)
    
    processed = []
    for att in message.attachments:
        if not any(att.filename.lower().endswith(ext) for ext in [".pdf", ".png", ".jpg", ".jpeg", ".tiff"]):
            continue

        saved_path = EmailReceiverService.save_attachment(att)
        doc_id = str(uuid.uuid4())
        ext = saved_path.suffix.replace(".", "").upper()

        DocumentRepository.create(
            doc_id=doc_id,
            filename=saved_path.name,
            original_filename=att.filename,
            file_path=str(saved_path),
            file_size=len(att.data),
            file_type=ext,
            source="email_inbound",
            sender_email=message.sender
        )

        background_tasks.add_task(process_and_dispatch, doc_id, saved_path, att.content_type)
        processed.append({"id": doc_id, "filename": att.filename})

    return {"status": "success", "processed_attachments": len(processed), "documents": processed}

@router.post("/sendgrid")
async def sendgrid_inbound_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Receives inbound email webhook from SendGrid Inbound Parse (multipart/form-data).
    """
    form = await request.form()
    form_dict = dict(form)
    
    # Collect files
    files: List[UploadFile] = []
    for key, val in form.items():
        if isinstance(val, UploadFile):
            files.append(val)

    message = await EmailReceiverService.parse_sendgrid(form_dict, files)
    
    processed = []
    for att in message.attachments:
        if not any(att.filename.lower().endswith(ext) for ext in [".pdf", ".png", ".jpg", ".jpeg", ".tiff"]):
            continue

        saved_path = EmailReceiverService.save_attachment(att)
        doc_id = str(uuid.uuid4())
        ext = saved_path.suffix.replace(".", "").upper()

        DocumentRepository.create(
            doc_id=doc_id,
            filename=saved_path.name,
            original_filename=att.filename,
            file_path=str(saved_path),
            file_size=len(att.data),
            file_type=ext,
            source="email_inbound",
            sender_email=message.sender
        )

        background_tasks.add_task(process_and_dispatch, doc_id, saved_path, att.content_type)
        processed.append({"id": doc_id, "filename": att.filename})

    return {"status": "success", "processed_attachments": len(processed), "documents": processed}

@router.post("/test-mock")
async def test_mock_email(background_tasks: BackgroundTasks):
    """
    Simulates receiving an inbound email with a sample supplier purchase order.
    Useful for testing the email flow without setting up MX records.
    """
    sample_content = b"%PDF-1.4 Mock Supplier Purchase Order Content for Testing"
    from backend.services.email_receiver import InboundAttachment
    att = InboundAttachment(filename="PO_Vanguard_Testing.pdf", content_type="application/pdf", data=sample_content)
    
    saved_path = EmailReceiverService.save_attachment(att)
    doc_id = str(uuid.uuid4())

    DocumentRepository.create(
        doc_id=doc_id,
        filename=saved_path.name,
        original_filename=att.filename,
        file_path=str(saved_path),
        file_size=len(sample_content),
        file_type="PDF",
        source="email_inbound",
        sender_email="supplier-orders@acme-parts.com"
    )

    background_tasks.add_task(process_and_dispatch, doc_id, saved_path, "application/pdf")
    return {
        "status": "success",
        "message": "Mock inbound email received and queued for processing.",
        "document_id": doc_id
    }

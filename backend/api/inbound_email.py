import uuid
from typing import List
from fastapi import APIRouter, Request, UploadFile, HTTPException, Depends
from backend.config import settings
from backend.database import DocumentRepository, UserRepository, ProcessingRunRepository
from backend.auth import current_user
from backend.services.email_receiver import EmailReceiverService

router = APIRouter(prefix="/api/inbound", tags=["inbound_email"])


def _token_user(request: Request, token: str = ""):
    authorization = request.headers.get("authorization", "")
    bearer_token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    supplied = token or request.headers.get("x-docflow-inbound-token", "") or bearer_token
    user = UserRepository.get_by_inbound_token(supplied) if supplied else None
    if not user:
        raise HTTPException(status_code=401, detail="Invalid inbound token")
    return user

@router.post("/postmark")
async def postmark_inbound_webhook(request: Request):
    """
    Receives inbound email webhook from Postmark Inbound.
    Extracts attachments (PDFs/images), saves and processes them.
    """
    user = _token_user(request)
    payload = await request.json()
    message = await EmailReceiverService.parse_postmark(payload)
    
    processed = []
    for att in message.attachments:
        if not any(att.filename.lower().endswith(ext) for ext in [".pdf", ".png", ".jpg", ".jpeg", ".tiff"]):
            continue

        saved_path = EmailReceiverService.save_attachment(att)
        doc_id = str(uuid.uuid4())
        ext = saved_path.suffix.replace(".", "").upper()
        user_dir = settings.uploads_dir / user["id"]
        user_dir.mkdir(parents=True, exist_ok=True)
        private_path = user_dir / f"{doc_id}{saved_path.suffix.lower()}"
        saved_path.replace(private_path)
        saved_path = private_path

        DocumentRepository.create(
            doc_id=doc_id,
            filename=saved_path.name,
            original_filename=att.filename,
            file_path=str(saved_path),
            file_size=len(att.data),
            file_type=ext,
            source="email_inbound",
            sender_email=message.sender,
            user_id=user["id"],
        )

        ProcessingRunRepository.enqueue(doc_id, user["id"], str(saved_path), att.content_type or "application/octet-stream")
        processed.append({"id": doc_id, "filename": att.filename})

    return {"status": "success", "processed_attachments": len(processed), "documents": processed}

@router.post("/sendgrid")
async def sendgrid_inbound_webhook(request: Request):
    """
    Receives inbound email webhook from SendGrid Inbound Parse (multipart/form-data).
    """
    form = await request.form()
    form_dict = dict(form)
    user = _token_user(request, str(form_dict.get("docflow_token", "")))
    
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
        user_dir = settings.uploads_dir / user["id"]
        user_dir.mkdir(parents=True, exist_ok=True)
        private_path = user_dir / f"{doc_id}{saved_path.suffix.lower()}"
        saved_path.replace(private_path)
        saved_path = private_path

        DocumentRepository.create(
            doc_id=doc_id,
            filename=saved_path.name,
            original_filename=att.filename,
            file_path=str(saved_path),
            file_size=len(att.data),
            file_type=ext,
            source="email_inbound",
            sender_email=message.sender,
            user_id=user["id"],
        )

        ProcessingRunRepository.enqueue(doc_id, user["id"], str(saved_path), att.content_type or "application/octet-stream")
        processed.append({"id": doc_id, "filename": att.filename})

    return {"status": "success", "processed_attachments": len(processed), "documents": processed}

@router.post("/test-mock")
async def test_mock_email(user: dict = Depends(current_user)):
    """
    Simulates receiving an inbound email with a sample supplier purchase order.
    Useful for testing the email flow without setting up MX records.
    """
    sample_content = b"%PDF-1.4 Mock Supplier Purchase Order Content for Testing"
    from backend.services.email_receiver import InboundAttachment
    att = InboundAttachment(filename="PO_Vanguard_Testing.pdf", content_type="application/pdf", data=sample_content)
    
    doc_id = str(uuid.uuid4())
    user_dir = settings.uploads_dir / user["id"]
    user_dir.mkdir(parents=True, exist_ok=True)
    saved_path = user_dir / f"{doc_id}.pdf"
    saved_path.write_bytes(sample_content)

    DocumentRepository.create(
        doc_id=doc_id,
        filename=saved_path.name,
        original_filename=att.filename,
        file_path=str(saved_path),
        file_size=len(sample_content),
        file_type="PDF",
        source="email_inbound",
        sender_email="supplier-orders@acme-parts.com",
        user_id=user["id"],
    )

    ProcessingRunRepository.enqueue(doc_id, user["id"], str(saved_path), "application/pdf")
    return {
        "status": "success",
        "message": "Mock inbound email received and queued for processing.",
        "document_id": doc_id
    }

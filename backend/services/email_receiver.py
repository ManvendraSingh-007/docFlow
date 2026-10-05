import os
import uuid
import base64
from pathlib import Path
from typing import List, Dict, Any, Optional
from fastapi import UploadFile
from backend.config import settings

class InboundAttachment:
    def __init__(self, filename: str, content_type: str, data: bytes):
        self.filename = filename
        self.content_type = content_type
        self.data = data

class InboundEmailMessage:
    def __init__(
        self,
        sender: str,
        recipient: str,
        subject: str,
        body_text: str,
        attachments: List[InboundAttachment]
    ):
        self.sender = sender
        self.recipient = recipient
        self.subject = subject
        self.body_text = body_text
        self.attachments = attachments

class EmailReceiverService:
    """
    Parses and standardizes incoming webhook payloads from:
    - Postmark Inbound
    - SendGrid Inbound Parse
    - Mailgun / Generic multipart
    """

    @classmethod
    async def parse_postmark(cls, payload: Dict[str, Any]) -> InboundEmailMessage:
        sender = payload.get("From", "unknown@sender.com")
        recipient = payload.get("To", "")
        subject = payload.get("Subject", "Inbound Document")
        body_text = payload.get("TextBody", "")

        attachments = []
        for att in payload.get("Attachments", []):
            name = att.get("Name", "attachment.pdf")
            content_type = att.get("ContentType", "application/pdf")
            content_b64 = att.get("Content", "")
            try:
                data = base64.b64decode(content_b64)
                attachments.append(InboundAttachment(filename=name, content_type=content_type, data=data))
            except Exception as e:
                print(f"[EmailReceiver] Postmark attachment decode error: {e}")

        return InboundEmailMessage(
            sender=sender,
            recipient=recipient,
            subject=subject,
            body_text=body_text,
            attachments=attachments
        )

    @classmethod
    async def parse_sendgrid(cls, form_data: Dict[str, Any], files: List[UploadFile]) -> InboundEmailMessage:
        sender = form_data.get("from", "unknown@sender.com")
        recipient = form_data.get("to", "")
        subject = form_data.get("subject", "Inbound Document")
        body_text = form_data.get("text", "")

        attachments = []
        for f in files:
            content = await f.read()
            attachments.append(InboundAttachment(
                filename=f.filename or "attachment.pdf",
                content_type=f.content_type or "application/pdf",
                data=content
            ))

        return InboundEmailMessage(
            sender=sender,
            recipient=recipient,
            subject=subject,
            body_text=body_text,
            attachments=attachments
        )

    @classmethod
    def save_attachment(cls, attachment: InboundAttachment) -> Path:
        """Saves inbound attachment into data/uploads directory with unique ID"""
        unique_name = f"{uuid.uuid4().hex[:8]}_{attachment.filename}"
        target_path = settings.uploads_dir / unique_name
        with open(target_path, "wb") as f:
            f.write(attachment.data)
        return target_path

from typing import List, Optional
from pydantic import BaseModel, Field
from datetime import datetime
from enum import Enum

class DocumentStatus(str, Enum):
    PROCESSING = "PROCESSING"
    VERIFIED = "VERIFIED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    FAILED = "FAILED"

class WebhookStatus(str, Enum):
    PENDING = "PENDING"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"

class VendorInfo(BaseModel):
    name: Optional[str] = None
    tax_id: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None

class BuyerInfo(BaseModel):
    name: Optional[str] = None
    company: Optional[str] = None
    email: Optional[str] = None
    billing_address: Optional[str] = None
    shipping_address: Optional[str] = None

class LineItem(BaseModel):
    line_no: int = 1
    sku: Optional[str] = ""
    description: str
    quantity: float = 1.0
    unit_price: float = 0.0
    tax_rate: Optional[float] = 0.0
    line_total: float = 0.0
    # Math verification flags
    math_verified: bool = True
    discrepancy: float = 0.0

class FinancialSummary(BaseModel):
    currency: str = "USD"
    subtotal: float = 0.0
    tax_amount: float = 0.0
    shipping_amount: float = 0.0
    discount_amount: float = 0.0
    grand_total: float = 0.0

class AdditionalDetail(BaseModel):
    label: str
    value: str
    section: Optional[str] = None

class ExtractedTable(BaseModel):
    title: str = "Document table"
    columns: List[str] = Field(default_factory=list)
    rows: List[List[str]] = Field(default_factory=list)

class ValidationCheck(BaseModel):
    name: str
    passed: bool
    applicable: bool = True
    expected: float
    actual: float
    message: str

class VerificationResult(BaseModel):
    status: DocumentStatus
    confidence_score: float = 1.0 # 0.0 to 1.0
    checks: List[ValidationCheck] = Field(default_factory=list)
    flags: List[str] = Field(default_factory=list)

class ParsedDocumentData(BaseModel):
    document_type: str = "PURCHASE_ORDER" # PURCHASE_ORDER, INVOICE, PACKING_SLIP
    document_number: Optional[str] = None # PO # or Invoice #
    document_date: Optional[str] = None
    due_date: Optional[str] = None
    payment_terms: Optional[str] = None
    
    vendor: VendorInfo = Field(default_factory=VendorInfo)
    buyer: BuyerInfo = Field(default_factory=BuyerInfo)
    line_items: List[LineItem] = Field(default_factory=list)
    summary: FinancialSummary = Field(default_factory=FinancialSummary)
    verification: Optional[VerificationResult] = None
    extraction_provider: Optional[str] = None
    extraction_model: Optional[str] = None
    extraction_fallback: bool = False
    extraction_requested_provider: Optional[str] = None
    extraction_error: Optional[str] = None
    notes: Optional[str] = None
    additional_details: List[AdditionalDetail] = Field(default_factory=list)
    extracted_tables: List[ExtractedTable] = Field(default_factory=list)
    # Kept out of parsed_json/API list responses; persisted separately for inspection.
    source_text: Optional[str] = Field(default=None, exclude=True)

class DocumentResponse(BaseModel):
    id: str
    filename: str
    file_type: str
    file_size_bytes: int
    source: str # upload, email, api
    sender_email: Optional[str] = None
    created_at: str
    status: DocumentStatus
    confidence: float
    data: Optional[ParsedDocumentData] = None
    webhook_status: WebhookStatus = WebhookStatus.PENDING
    webhook_response_code: Optional[int] = None

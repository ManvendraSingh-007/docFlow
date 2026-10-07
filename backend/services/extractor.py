import json
import re
import os
import httpx
import pdfplumber
from pathlib import Path
from typing import Optional, Dict, Any, List
from pypdf import PdfReader
from backend.config import settings
from backend.models.document import (
    ParsedDocumentData,
    VendorInfo,
    BuyerInfo,
    LineItem,
    FinancialSummary
)
from backend.services.validator import DocumentValidator

SYSTEM_PROMPT = """You are a multimodal business-document extraction engine. Inspect every page of the supplied document visually and read its printed text and tables. Documents may use unfamiliar layouts, regional tax rules, abbreviations, or product-specific columns.

Capture every visible meaningful field and every table column and row. Preserve document-specific fields such as GST/VAT, HSN/SAC, rate, discount, MRP, batch/lot, manufacture or expiry date, unit, pack size, scheme, tax split, and product identifiers. Return table headings and cell values in extracted_tables, keeping each value on its original row. Return labeled values outside tables in additional_details, grouped by section when useful. Include standard normalized fields too when the document supports them. Do not force unfamiliar values into an incorrect standard field, and do not omit them just because they are absent from the standard schema.

Preserve labels, units, currencies, signs, and date text as printed. Never invent missing values or calculate a value the document does not state. Leave unknown standard scalar fields null and use empty arrays when there is no corresponding data. Capture notes, references, delivery instructions, contacts, addresses, and footers when meaningful. Return only valid JSON matching the requested structure; no markdown or commentary.
"""

EXTRACTION_SCHEMA = {
    "document_type": "INVOICE or PURCHASE_ORDER",
    "document_number": None,
    "document_date": None,
    "due_date": None,
    "payment_terms": None,
    "vendor": {
        "name": None,
        "tax_id": None,
        "email": None,
        "phone": None,
        "address": None
    },
    "buyer": {
        "name": None,
        "company": None,
        "email": None,
        "billing_address": None,
        "shipping_address": None
    },
    "line_items": [],
    "summary": {
        "currency": None,
        "subtotal": None,
        "tax_amount": None,
        "shipping_amount": None,
        "discount_amount": None,
        "grand_total": None
    },
    "notes": None,
    "additional_details": [],
    "extracted_tables": [
        {"title": "Table title", "columns": ["Printed heading"], "rows": [["Printed cell value"]]}
    ]
}

def clean_num(val) -> float:
    if val is None:
        return 0.0
    s = str(val).strip().replace("€", "").replace("$", "").replace("£", "").replace(",", "")
    m = re.search(r"[-+]?\d*\.?\d+", s)
    return float(m.group(0)) if m else 0.0

class DocumentExtractor:
    """
    Multimodal AI document extraction service for Google Gemini, OpenAI,
    Mistral, and OpenRouter.
    """

    @classmethod
    async def extract(cls, file_path: Path, mime_type: str = "application/pdf", account_settings: Optional[Dict[str, Any]] = None) -> ParsedDocumentData:
        file_path = Path(file_path)
        use_account_credentials = account_settings is not None
        account_settings = account_settings or {}
        credentials = account_settings.get("provider_credentials") or {}
        gemini_api_key = credentials.get("gemini", {}).get("api_key") or account_settings.get("gemini_api_key") or ("" if use_account_credentials else settings.gemini_api_key)
        openai_api_key = credentials.get("openai", {}).get("api_key") or account_settings.get("openai_api_key") or ("" if use_account_credentials else settings.openai_api_key)
        mistral_api_key = credentials.get("mistral", {}).get("api_key") or account_settings.get("mistral_api_key", "")
        openrouter_api_key = credentials.get("openrouter", {}).get("api_key") or account_settings.get("openrouter_api_key", "")
        gemini_model = credentials.get("gemini", {}).get("model") or account_settings.get("gemini_model") or "gemini-3.1-flash-lite"
        openai_model = credentials.get("openai", {}).get("model") or account_settings.get("openai_model") or "gpt-4o-mini"
        mistral_model = credentials.get("mistral", {}).get("model") or account_settings.get("mistral_model") or "mistral-small-2506"
        provider = (account_settings.get("default_provider") or settings.default_provider).lower()

        # Decide provider
        if provider == "heuristic":
            provider = "auto"  # Upgrade accounts saved before AI-only extraction.
        if provider == "auto":
            if gemini_api_key:
                provider = "gemini"
            elif openai_api_key:
                provider = "openai"
            elif mistral_api_key:
                provider = "mistral"
            elif openrouter_api_key:
                provider = "openrouter"
            else:
                raise RuntimeError("No AI provider key is configured. Add a provider key in Extraction settings before processing documents.")

        requested_provider = provider
        model_by_provider = {
            "gemini": gemini_model,
            "openai": openai_model,
            "mistral": mistral_model,
            "openrouter": credentials.get("openrouter", {}).get("model") or account_settings.get("openrouter_model") or "google/gemini-2.5-flash",
        }
        used_provider = {"gemini": "Google Gemini", "openai": "OpenAI", "mistral": "Mistral", "openrouter": "OpenRouter"}.get(provider, provider)
        used_model = model_by_provider.get(provider, "")
        if provider == "gemini":
            if not gemini_api_key:
                raise RuntimeError("No Google Gemini key is selected. Add or select a key in Extraction settings.")
            parsed_data = await cls._extract_with_gemini(file_path, mime_type, gemini_api_key, gemini_model)
        elif provider == "openai":
            if not openai_api_key:
                raise RuntimeError("No OpenAI key is selected. Add or select a key in Extraction settings.")
            parsed_data = await cls._extract_with_openai(file_path, mime_type, openai_api_key, openai_model)
        elif provider == "mistral":
            if not mistral_api_key:
                raise RuntimeError("No Mistral key is selected. Add or select a key in Extraction settings.")
            parsed_data = await cls._extract_with_compatible_vision(file_path, mime_type, mistral_api_key, "https://api.mistral.ai/v1/chat/completions", mistral_model)
        elif provider == "openrouter":
            if not openrouter_api_key:
                raise RuntimeError("No OpenRouter key is selected. Add or select a key in Extraction settings.")
            model = credentials.get("openrouter", {}).get("model") or account_settings.get("openrouter_model") or "google/gemini-2.5-flash"
            parsed_data = await cls._extract_with_compatible_vision(file_path, mime_type, openrouter_api_key, "https://openrouter.ai/api/v1/chat/completions", model)
        else:
            raise ValueError(f"Unsupported AI extraction provider: {provider}")

        # Keep the readable PDF text alongside vision extraction too. It provides
        # an audit trail for details that do not fit the structured invoice fields.
        if provider in {"gemini", "openai", "mistral", "openrouter"}:
            try:
                parsed_data.source_text = await cls._extract_pdf_text(file_path)
            except Exception as exc:
                print(f"[Extractor] Could not retain document text layer: {exc}")

        parsed_data.extraction_provider = used_provider
        if not parsed_data.extraction_model:
            parsed_data.extraction_model = used_model
        parsed_data.extraction_fallback = False
        parsed_data.extraction_requested_provider = requested_provider
        parsed_data.extraction_error = None
        # Always run math validation check on the extracted data
        DocumentValidator.validate(parsed_data)
        return parsed_data

    @classmethod
    async def _extract_with_gemini(cls, file_path: Path, mime_type: str, api_key: Optional[str] = None, model_name: str = "gemini-3.1-flash-lite") -> ParsedDocumentData:
        import base64
        api_key = api_key or settings.gemini_api_key
        with open(file_path, "rb") as f:
            encoded_bytes = base64.b64encode(f.read()).decode("utf-8")

        prompt = (
            f"{SYSTEM_PROMPT}\n"
            f"Here is the expected JSON structure:\n{json.dumps(EXTRACTION_SCHEMA, indent=2)}\n"
            f"Extract all information accurately from the document and return only valid JSON."
        )

        models_to_try = [model_name]
        last_error = None

        for model_name in models_to_try:
            from urllib.parse import quote
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{quote(model_name, safe='')}:generateContent"
            headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
            payload = {
                "contents": [
                    {
                        "parts": [
                            {"text": prompt},
                            {
                                "inline_data": {
                                    "mime_type": mime_type if mime_type != "application/pdf" else "application/pdf",
                                    "data": encoded_bytes
                                }
                            }
                        ]
                    }
                ],
                "generationConfig": {
                    "response_mime_type": "application/json"
                }
            }

            try:
                async with httpx.AsyncClient(timeout=45.0) as client:
                    resp = await client.post(url, headers=headers, json=payload)
                    try:
                        data = resp.json()
                    except ValueError as exc:
                        raise RuntimeError(f"Gemini returned a non-JSON response (HTTP {resp.status_code}).") from exc
                    if resp.is_error:
                        api_error = data.get("error") if isinstance(data, dict) else None
                        message = api_error.get("message") if isinstance(api_error, dict) else None
                        raise RuntimeError(f"Gemini request failed (HTTP {resp.status_code}): {message or 'no error details returned'}")
                    candidates = data.get("candidates") or []
                    if not candidates:
                        reason = (data.get("promptFeedback") or {}).get("blockReason") or "no candidates returned"
                        raise RuntimeError(f"Gemini returned no extraction candidate: {reason}.")
                    content = candidates[0].get("content") or {}
                    text_content = "\n".join(part.get("text", "") for part in content.get("parts", []) if isinstance(part, dict) and isinstance(part.get("text"), str))
                    if not text_content.strip():
                        reason = candidates[0].get("finishReason") or "empty response"
                        raise RuntimeError(f"Gemini returned no extraction text (finish reason: {reason}).")
                    parsed_dict = cls._decode_model_json(text_content)
                    parsed_data = cls._parse_ai_result(parsed_dict)
                    parsed_data.extraction_model = data.get("modelVersion") or model_name
                    return parsed_data
            except Exception as e:
                last_error = e
                continue

        raise last_error or Exception("Gemini extraction failed on all model endpoints")

    @classmethod
    async def _extract_with_openai(cls, file_path: Path, mime_type: str, api_key: Optional[str] = None, model: str = "gpt-4o-mini") -> ParsedDocumentData:
        return await cls._extract_with_compatible_vision(file_path, mime_type, api_key or settings.openai_api_key, "https://api.openai.com/v1/chat/completions", model)

    @classmethod
    async def _extract_with_compatible_vision(cls, file_path: Path, mime_type: str, api_key: str, endpoint: str, model: str) -> ParsedDocumentData:
        import base64

        prompt = (
            f"{SYSTEM_PROMPT}\n"
            f"Extract all information accurately into this JSON schema:\n{json.dumps(EXTRACTION_SCHEMA, indent=2)}\n"
        )

        content = [{"type": "text", "text": prompt}]
        if file_path.suffix.lower() == ".pdf":
            content.extend({"type": "image_url", "image_url": {"url": url, "detail": "high"}} for url in cls._pdf_image_data_urls(file_path))
        else:
            with open(file_path, "rb") as f:
                encoded_bytes = base64.b64encode(f.read()).decode("utf-8")
            content.append({"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded_bytes}", "detail": "high"}})

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": content
                }
            ],
            "response_format": {"type": "json_object"}
        }

        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(endpoint, headers=headers, json=payload)
            try:
                data = resp.json()
            except ValueError as exc:
                raise RuntimeError(
                    f"Provider returned a non-JSON response (HTTP {resp.status_code}, "
                    f"content type {resp.headers.get('content-type', 'unknown')})."
                ) from exc
            if resp.is_error:
                error = data.get("error") if isinstance(data, dict) else None
                message = error.get("message") if isinstance(error, dict) else None
                raise RuntimeError(f"Provider request failed (HTTP {resp.status_code}): {message or 'no error details returned'}")

        choices = data.get("choices") if isinstance(data, dict) else None
        if not choices or not isinstance(choices[0], dict):
            raise RuntimeError("Provider returned no completion choices.")
        choice = choices[0]
        message = choice.get("message") or {}
        text_content = message.get("content") if isinstance(message, dict) else None
        if isinstance(text_content, list):
            text_content = "\n".join(
                block.get("text", "") for block in text_content
                if isinstance(block, dict) and isinstance(block.get("text"), str)
            )
        if not isinstance(text_content, str) or not text_content.strip():
            finish_reason = choice.get("finish_reason") or "unknown"
            refusal = message.get("refusal") if isinstance(message, dict) else None
            detail = f" Refusal: {refusal[:240]}" if isinstance(refusal, str) and refusal else ""
            raise RuntimeError(
                f"Provider returned an empty extraction response (finish reason: {finish_reason}).{detail} "
                "Check that the selected model supports image input, or choose a vision-capable model."
            )

        parsed_dict = cls._decode_model_json(text_content)
        parsed_data = cls._parse_ai_result(parsed_dict)
        parsed_data.extraction_model = data.get("model") or model
        return parsed_data

    @staticmethod
    def _decode_model_json(text_content: str) -> Dict[str, Any]:
        candidate = text_content.strip()
        candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", candidate, flags=re.IGNORECASE).strip()
        candidate = re.sub(r"<think>.*?</think>", "", candidate, flags=re.IGNORECASE | re.DOTALL).strip()
        try:
            decoded = json.loads(candidate)
        except json.JSONDecodeError:
            decoded = None
            # Some compatible models add a short preamble despite being asked
            # for JSON. Decode the first valid JSON object without logging its text.
            decoder = json.JSONDecoder()
            for index, char in enumerate(candidate):
                if char != "{":
                    continue
                try:
                    decoded, _ = decoder.raw_decode(candidate[index:])
                    break
                except json.JSONDecodeError:
                    continue
        if not isinstance(decoded, dict):
            raise ValueError("Provider response did not contain a valid JSON object. Check the selected model's JSON and vision support.")
        return decoded

    @staticmethod
    def _parse_ai_result(parsed_dict: Dict[str, Any]) -> ParsedDocumentData:
        normalized = dict(parsed_dict)

        def text_value(value, default=None):
            if value is None:
                return default
            if isinstance(value, str):
                return value.strip() or default
            if isinstance(value, (dict, list)):
                return json.dumps(value, ensure_ascii=False)
            return str(value)

        def number_value(value, default=0.0):
            if value is None or isinstance(value, str) and not value.strip():
                return default
            if isinstance(value, bool):
                return float(value)
            try:
                return float(value)
            except (TypeError, ValueError):
                # Gemini may return formatted values such as "$1,234.50".
                return clean_num(value)

        normalized["verification"] = None  # Always calculate checks from normalized values below.
        normalized["document_type"] = text_value(normalized.get("document_type"), "PURCHASE_ORDER")
        for field in ("document_number", "document_date", "due_date", "payment_terms", "notes"):
            normalized[field] = text_value(normalized.get(field))

        for party_field in ("vendor", "buyer"):
            party = normalized.get(party_field)
            if not isinstance(party, dict):
                party = {}
            else:
                party = dict(party)
            for field, value in party.items():
                party[field] = text_value(value)
            normalized[party_field] = party

        summary = normalized.get("summary")
        if not isinstance(summary, dict):
            summary = {}
        else:
            summary = dict(summary)
        summary["currency"] = text_value(summary.get("currency"), "UNKNOWN")
        for field in ("subtotal", "tax_amount", "shipping_amount", "discount_amount", "grand_total"):
            summary[field] = number_value(summary.get(field))
        normalized["summary"] = summary

        line_items = normalized.get("line_items")
        if isinstance(line_items, dict):
            line_items = [line_items]
        if not isinstance(line_items, list):
            line_items = []
        normalized_items = []
        for index, raw_item in enumerate(line_items):
            if isinstance(raw_item, dict):
                item = dict(raw_item)
            elif isinstance(raw_item, str):
                item = {"description": raw_item}
            else:
                continue
            line_no = number_value(item.get("line_no"), index + 1)
            item["line_no"] = max(1, int(line_no))
            item["sku"] = text_value(item.get("sku"), "") or ""
            item["description"] = text_value(item.get("description"), "") or ""
            for field in ("quantity", "unit_price", "tax_rate", "line_total", "discrepancy"):
                item[field] = number_value(item.get(field))
            # The arithmetic validator recalculates this flag; do not trust model output.
            item["math_verified"] = True
            normalized_items.append(item)
        normalized["line_items"] = normalized_items

        details = normalized.get("additional_details")
        if isinstance(details, dict):
            details = [{"label": label, "value": value} for label, value in details.items()]
        if not isinstance(details, list):
            details = []
        normalized_details = []
        for item in details:
            if isinstance(item, dict):
                label = text_value(item.get("label") or item.get("name"), "Additional detail")
                raw_value = item.get("value") if item.get("value") is not None else item.get("text")
                value = text_value(raw_value, "")
            elif isinstance(item, str):
                label, value = "Additional detail", item.strip()
            else:
                continue
            if value:
                normalized_details.append({
                    "label": label,
                    "value": value,
                    "section": text_value(item.get("section") or item.get("group")) if isinstance(item, dict) else None,
                })
        normalized["additional_details"] = normalized_details

        raw_tables = normalized.get("extracted_tables") or normalized.get("tables") or []
        if isinstance(raw_tables, dict):
            raw_tables = [raw_tables]
        normalized_tables = []
        for table_index, raw_table in enumerate(raw_tables if isinstance(raw_tables, list) else []):
            if not isinstance(raw_table, dict):
                continue
            raw_columns = raw_table.get("columns") or raw_table.get("headers") or []
            raw_rows = raw_table.get("rows") or raw_table.get("data") or []
            columns = [text_value(column, "") or "" for column in raw_columns] if isinstance(raw_columns, list) else []
            if not columns and isinstance(raw_rows, list):
                for raw_row in raw_rows:
                    if isinstance(raw_row, dict):
                        for key in raw_row:
                            label = text_value(key, "") or ""
                            if label not in columns:
                                columns.append(label)
            rows = []
            for raw_row in raw_rows if isinstance(raw_rows, list) else []:
                if isinstance(raw_row, dict):
                    if not columns:
                        columns = [text_value(key, "") or "" for key in raw_row.keys()]
                    rows.append([text_value(raw_row.get(column), "") or "" for column in columns])
                elif isinstance(raw_row, (list, tuple)):
                    rows.append([text_value(cell, "") or "" for cell in raw_row])
            if columns and rows:
                # Keep each row rectangular without discarding unexpected cells.
                width = max(len(columns), max(len(row) for row in rows))
                columns.extend(f"Column {index + 1}" for index in range(len(columns), width))
                rows = [row + [""] * (width - len(row)) for row in rows]
                normalized_tables.append({
                    "title": text_value(raw_table.get("title") or raw_table.get("name"), f"Document table {table_index + 1}"),
                    "columns": columns,
                    "rows": rows,
                })
        normalized["extracted_tables"] = normalized_tables

        try:
            return ParsedDocumentData(**normalized)
        except Exception as exc:
            errors = getattr(exc, "errors", None)
            if callable(errors):
                paths = [".".join(str(part) for part in error.get("loc", ())) for error in errors()]
                if paths:
                    raise ValueError("Model response has invalid fields: " + ", ".join(paths)) from exc
            raise

    @staticmethod
    def _pdf_image_data_urls(file_path: Path) -> List[str]:
        import base64
        from io import BytesIO
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument(str(file_path))
        if len(document) > 20:
            document.close()
            raise ValueError("OpenAI vision extraction is limited to 20-page PDFs")
        image_data_urls = []
        try:
            for page in document:
                image = page.render(scale=1.5).to_pil()
                image_buffer = BytesIO()
                image.save(image_buffer, format="JPEG", quality=85)
                encoded = base64.b64encode(image_buffer.getvalue()).decode("ascii")
                image_data_urls.append(f"data:image/jpeg;base64,{encoded}")
                page.close()
        finally:
            document.close()
        return image_data_urls

    @staticmethod
    async def _extract_pdf_text(file_path: Path) -> str:
        """Retain a text-layer copy for reference; it does not extract fields."""
        file_path = Path(file_path)
        chunks = []
        if file_path.suffix.lower() == ".pdf":
            try:
                with pdfplumber.open(file_path) as pdf:
                    chunks = [page.extract_text(layout=False) or "" for page in pdf.pages]
            except Exception:
                reader = PdfReader(str(file_path))
                chunks = [page.extract_text() or "" for page in reader.pages]
        else:
            reader = PdfReader(str(file_path))
            chunks = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(chunk for chunk in chunks if chunk.strip())[:200_000]

__all__ = ["DocumentExtractor", "clean_num"]

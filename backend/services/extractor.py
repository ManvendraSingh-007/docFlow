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

SYSTEM_PROMPT = """You are a multimodal document analyst. Inspect every page of the supplied document visually, including layout, printed text, stamps, and tables. The document may be an invoice, purchase order, receipt, medical bill, customs form, or another kind of business document.

Design the data structure from the document itself. Group its fields into meaningful sections using the document's own concepts. Create a separate table for each distinct table in the document. Each table must have columns based on the printed headings and one row per source row. Include every visible meaningful field and cell, including unfamiliar regional or product fields such as GST, HSN/SAC, rate, discount, MRP, batch/lot, manufacture/expiry, pack size, and split taxes. Do not force the document into a predefined invoice layout or omit a field because it has no standard counterpart.

For each field and table column, include an optional role when confidently recognized. Roles are metadata used only for arithmetic checks: document_number, document_date, due_date, payment_terms, vendor_name, vendor_tax_id, buyer_name, buyer_company, currency, sku, description, quantity, unit_price, net_unit_price, tax_rate, line_total, line_discount_amount, line_discount_rate, line_tax_amount, line_charge_amount, subtotal, tax_amount, shipping_amount, discount_amount, grand_total. Use role `other` or omit role for anything else. Keep all original labels and values regardless of role.

In `arithmetic_rules`, identify a rule only when the source layout and extracted columns clearly support it. Supported line_total_basis values: `quantity_times_unit_price`, `quantity_times_unit_price_less_discount_amount`, `quantity_times_unit_price_less_discount_rate`, `quantity_times_net_unit_price`, `quantity_times_unit_price_plus_line_tax`, `quantity_times_unit_price_less_discount_amount_plus_line_tax`, `quantity_times_unit_price_less_discount_rate_plus_line_tax`. Supported subtotal_basis: `sum_of_line_totals`. Supported grand_total_basis values: `subtotal_plus_tax_shipping_less_discount`, `subtotal_plus_tax_shipping`, `subtotal_plus_tax_less_discount`, `subtotal_plus_tax`, `subtotal_plus_shipping`, `subtotal_less_discount`. Otherwise use null. Never choose a rule just because it is common on invoices.

Preserve printed labels, units, currencies, signs, and date text. Never invent a value or calculate a value the source does not state. Put values into scalar JSON strings or numbers. Keep table cells aligned to their declared columns. Capture useful notes, references, delivery instructions, contacts, addresses, and footers in sections or notes. Return only valid JSON matching the requested structure; no markdown or commentary.
"""

EXTRACTION_SCHEMA = {
    "document_type": "Use the source document's type",
    "document_data": {
        "title": "A concise title based on the source",
        "sections": [
            {"title": "Section title from the document", "fields": [{"label": "Printed field label", "value": "Printed value", "type": "text", "role": "other"}]}
        ],
        "tables": [
            {"title": "Table title", "columns": [{"key": "column_key", "label": "Printed column heading", "type": "text", "role": "other"}], "rows": [["Cell value"]]}
        ],
        "arithmetic_rules": {"line_total_basis": None, "subtotal_basis": None, "grand_total_basis": None},
        "notes": []
    }
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
        source_text = ""
        try:
            source_text = await cls._extract_pdf_text(file_path)
        except Exception as exc:
            print(f"[Extractor] Could not read document text layer; continuing with page visuals: {exc}")
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
            parsed_data = await cls._extract_with_gemini(file_path, mime_type, gemini_api_key, gemini_model, source_text)
        elif provider == "openai":
            if not openai_api_key:
                raise RuntimeError("No OpenAI key is selected. Add or select a key in Extraction settings.")
            parsed_data = await cls._extract_with_openai(file_path, mime_type, openai_api_key, openai_model, source_text)
        elif provider == "mistral":
            if not mistral_api_key:
                raise RuntimeError("No Mistral key is selected. Add or select a key in Extraction settings.")
            parsed_data = await cls._extract_with_compatible_vision(file_path, mime_type, mistral_api_key, "https://api.mistral.ai/v1/chat/completions", mistral_model, source_text)
        elif provider == "openrouter":
            if not openrouter_api_key:
                raise RuntimeError("No OpenRouter key is selected. Add or select a key in Extraction settings.")
            model = credentials.get("openrouter", {}).get("model") or account_settings.get("openrouter_model") or "google/gemini-2.5-flash"
            parsed_data = await cls._extract_with_compatible_vision(file_path, mime_type, openrouter_api_key, "https://openrouter.ai/api/v1/chat/completions", model, source_text)
        else:
            raise ValueError(f"Unsupported AI extraction provider: {provider}")

        # Keep the readable PDF text alongside vision extraction too. It provides
        # an audit trail for details that do not fit the structured invoice fields.
        parsed_data.source_text = source_text or None

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
    async def _extract_with_gemini(cls, file_path: Path, mime_type: str, api_key: Optional[str] = None, model_name: str = "gemini-3.1-flash-lite", source_text: str = "") -> ParsedDocumentData:
        import base64
        api_key = api_key or settings.gemini_api_key
        with open(file_path, "rb") as f:
            encoded_bytes = base64.b64encode(f.read()).decode("utf-8")

        prompt = (
            f"{SYSTEM_PROMPT}\n"
            + (f"Readable text layer from this same document (visual layout remains the reference for table grouping):\n{source_text}\n" if source_text else "")
            + f"Response structure example (placeholder text is not document data):\n{json.dumps(EXTRACTION_SCHEMA, indent=2)}\n"
            + "Return empty sections or tables only when the source contains no corresponding information. Extract all visible information and return only valid JSON."
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
    async def _extract_with_openai(cls, file_path: Path, mime_type: str, api_key: Optional[str] = None, model: str = "gpt-4o-mini", source_text: str = "") -> ParsedDocumentData:
        return await cls._extract_with_compatible_vision(file_path, mime_type, api_key or settings.openai_api_key, "https://api.openai.com/v1/chat/completions", model, source_text)

    @classmethod
    async def _extract_with_compatible_vision(cls, file_path: Path, mime_type: str, api_key: str, endpoint: str, model: str, source_text: str = "") -> ParsedDocumentData:
        import base64

        prompt = (
            f"{SYSTEM_PROMPT}\n"
            + (f"Readable text layer from this same document (visual layout remains the reference for table grouping):\n{source_text}\n" if source_text else "")
            + f"Response structure example (placeholder text is not document data):\n{json.dumps(EXTRACTION_SCHEMA, indent=2)}\n"
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
        flexible_data = parsed_dict.get("document_data") or parsed_dict.get("data")
        if not isinstance(flexible_data, dict) and any(key in parsed_dict for key in ("sections", "tables", "fields")):
            flexible_data = {key: value for key, value in parsed_dict.items() if key not in {"document_type", "verification"}}
        if isinstance(flexible_data, dict):
            flexible_response = dict(parsed_dict)
            flexible_response["document_data"] = flexible_data
            return DocumentExtractor._parse_flexible_ai_result(flexible_response)

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
            item["math_verified"] = None
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
    def _parse_flexible_ai_result(response: Dict[str, Any]) -> ParsedDocumentData:
        """Normalize the AI-designed sections and tables without imposing invoice columns."""
        def label_text(value, fallback=""):
            if value is None:
                return fallback
            if isinstance(value, (dict, list)):
                return json.dumps(value, ensure_ascii=False)
            return str(value).strip()

        source = dict(response.get("document_data") or {})
        sections = source.get("sections") or []
        if not sections and source.get("fields"):
            sections = [{"title": source.get("title") or "Document fields", "fields": source["fields"]}]
        if isinstance(sections, dict):
            sections = [{"title": title, "fields": fields} for title, fields in sections.items()]
        normalized_sections = []
        role_fields = {}
        for section_index, section in enumerate(sections if isinstance(sections, list) else []):
            if not isinstance(section, dict):
                continue
            raw_fields = section.get("fields") or []
            if isinstance(raw_fields, dict):
                raw_fields = [{"label": label, "value": value} for label, value in raw_fields.items()]
            fields = []
            for raw_field in raw_fields if isinstance(raw_fields, list) else []:
                if not isinstance(raw_field, dict):
                    continue
                label = label_text(raw_field.get("label") or raw_field.get("name"), "Field")
                value = raw_field.get("value", raw_field.get("text"))
                if isinstance(value, (dict, list)):
                    value = json.dumps(value, ensure_ascii=False)
                role = label_text(raw_field.get("role")).lower()
                field = {
                    "label": label,
                    "value": value,
                    "type": label_text(raw_field.get("type"), "text").lower(),
                }
                if role:
                    field["role"] = role
                    role_fields.setdefault(role, value)
                fields.append(field)
            if fields:
                normalized_sections.append({
                    "title": label_text(section.get("title") or section.get("name"), f"Section {section_index + 1}"),
                    "fields": fields,
                })

        extra_fields = []
        known_keys = {"title", "document_title", "sections", "fields", "tables", "notes", "arithmetic_rules"}
        def flatten_extra(label, value):
            if isinstance(value, dict) and value:
                for child_label, child_value in value.items():
                    flatten_extra(f"{label} · {child_label}", child_value)
            elif value is not None:
                rendered = json.dumps(value, ensure_ascii=False) if isinstance(value, list) else value
                value_type = "number" if isinstance(value, (int, float)) and not isinstance(value, bool) else "text"
                extra_fields.append({"label": label, "value": rendered, "type": value_type, "role": "other"})
        for key, value in source.items():
            if key not in known_keys:
                flatten_extra(label_text(key), value)
        if extra_fields:
            normalized_sections.append({"title": "Additional document data", "fields": extra_fields})

        raw_tables = source.get("tables") or []
        if isinstance(raw_tables, dict):
            raw_tables = [raw_tables]
        normalized_tables = []
        for table_index, table in enumerate(raw_tables if isinstance(raw_tables, list) else []):
            if not isinstance(table, dict):
                continue
            raw_columns = table.get("columns") or table.get("headers") or []
            columns = []
            for column_index, raw_column in enumerate(raw_columns if isinstance(raw_columns, list) else []):
                if isinstance(raw_column, dict):
                    column = {
                        "key": label_text(raw_column.get("key") or raw_column.get("label") or raw_column.get("name"), f"column_{column_index + 1}"),
                        "label": label_text(raw_column.get("label") or raw_column.get("name"), f"Column {column_index + 1}"),
                        "type": label_text(raw_column.get("type"), "text").lower(),
                    }
                    role = label_text(raw_column.get("role")).lower()
                    if role:
                        column["role"] = role
                else:
                    label = label_text(raw_column, f"Column {column_index + 1}")
                    column = {"key": label, "label": label, "type": "text"}
                columns.append(column)
            raw_rows = table.get("rows") or table.get("data") or []
            rows = []
            for raw_row in raw_rows if isinstance(raw_rows, list) else []:
                if isinstance(raw_row, dict):
                    rows.append([raw_row.get(column.get("key", ""), raw_row.get(column["label"])) for column in columns])
                elif isinstance(raw_row, (list, tuple)):
                    rows.append(list(raw_row))
            width = max([len(columns), *(len(row) for row in rows)], default=len(columns))
            while len(columns) < width:
                columns.append({"label": f"Column {len(columns) + 1}", "type": "text"})
            rows = [row + [None] * (width - len(row)) for row in rows]
            rows = [[json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value for value in row] for row in rows]
            if columns and rows:
                normalized_tables.append({
                    "title": label_text(table.get("title") or table.get("name"), f"Table {table_index + 1}"),
                    "columns": columns,
                    "rows": rows,
                })

        notes = source.get("notes") or []
        if isinstance(notes, str):
            notes = [notes] if notes.strip() else []
        data = {
            **source,
            "title": label_text(source.get("title") or source.get("document_title"), "Document details"),
            "sections": normalized_sections,
            "tables": normalized_tables,
            "notes": [label_text(note) for note in notes if note is not None],
        }
        document_type = label_text(response.get("document_type"), "DOCUMENT").upper()

        def numeric_role(role):
            value = role_fields.get(role)
            return clean_num(value) if value not in (None, "") else 0.0

        currency = label_text(role_fields.get("currency"), "UNKNOWN")
        compatibility = {
            "document_type": document_type,
            "document_number": label_text(role_fields.get("document_number")) or None,
            "document_date": label_text(role_fields.get("document_date")) or None,
            "due_date": label_text(role_fields.get("due_date")) or None,
            "payment_terms": label_text(role_fields.get("payment_terms")) or None,
            "vendor": {
                "name": label_text(role_fields.get("vendor_name")) or None,
                "tax_id": label_text(role_fields.get("vendor_tax_id")) or None,
            },
            "buyer": {
                "name": label_text(role_fields.get("buyer_name")) or None,
                "company": label_text(role_fields.get("buyer_company")) or None,
            },
            "line_items": [],
            "summary": {
                "currency": currency,
                "subtotal": numeric_role("subtotal"),
                "tax_amount": numeric_role("tax_amount"),
                "shipping_amount": numeric_role("shipping_amount"),
                "discount_amount": numeric_role("discount_amount"),
                "grand_total": numeric_role("grand_total"),
            },
            "notes": "\n".join(data["notes"]) or None,
            "document_data": data,
        }
        parsed = ParsedDocumentData(**compatibility)
        DocumentExtractor._apply_document_roles(parsed)
        return parsed

    @staticmethod
    def _apply_document_roles(parsed: ParsedDocumentData) -> None:
        """Refresh legacy arithmetic fields from the editable AI-designed JSON roles."""
        parsed.document_number = None
        parsed.document_date = None
        parsed.due_date = None
        parsed.payment_terms = None
        parsed.vendor = VendorInfo()
        parsed.buyer = BuyerInfo()
        parsed.line_items = []
        parsed.summary = FinancialSummary(currency="UNKNOWN")
        data = parsed.document_data or {}
        raw_notes = data.get("notes") or []
        if isinstance(raw_notes, str):
            raw_notes = [raw_notes]
        parsed.notes = "\n".join(str(note) for note in raw_notes if note not in (None, "")) or None
        fields = {}
        for section in data.get("sections", []):
            if not isinstance(section, dict):
                continue
            for field in section.get("fields", []):
                if not isinstance(field, dict):
                    continue
                role = field.get("role")
                if role:
                    fields[role] = field.get("value")
        role_values = {}
        for table in data.get("tables", []):
            if not isinstance(table, dict):
                continue
            columns = table.get("columns", [])
            role_indexes = {column.get("role"): index for index, column in enumerate(columns) if isinstance(column, dict) and column.get("role")}
            for row_index, row in enumerate(table.get("rows", [])):
                if not isinstance(row, list):
                    continue
                values = {role: row[index] for role, index in role_indexes.items() if index < len(row)}
                if "description" in role_indexes or ("quantity" in role_indexes and "line_total" in role_indexes):
                    parsed.line_items.append(LineItem(
                        line_no=row_index + 1,
                        sku=str(values.get("sku") or ""),
                        description=str(values.get("description") or ""),
                        quantity=clean_num(values.get("quantity")),
                        unit_price=clean_num(values.get("unit_price")),
                        tax_rate=clean_num(values.get("tax_rate")),
                        line_total=clean_num(values.get("line_total")),
                        pricing_adjustments={role: value for role, value in values.items() if value not in (None, "")},
                    ))
                for role in ("currency", "subtotal", "tax_amount", "shipping_amount", "discount_amount", "grand_total"):
                    if role in values and values[role] not in (None, ""):
                        role_values[role] = values[role]
        fields.update(role_values)
        for role, attr in (("document_number", "document_number"), ("document_date", "document_date"), ("due_date", "due_date"), ("payment_terms", "payment_terms")):
            if fields.get(role) is not None:
                setattr(parsed, attr, str(fields[role]))
        for role, attr in (("vendor_name", "name"), ("vendor_tax_id", "tax_id")):
            if fields.get(role) is not None:
                setattr(parsed.vendor, attr, str(fields[role]))
        for role, attr in (("buyer_name", "name"), ("buyer_company", "company")):
            if fields.get(role) is not None:
                setattr(parsed.buyer, attr, str(fields[role]))
        if fields.get("currency"):
            parsed.summary.currency = str(fields["currency"])
        for role in ("subtotal", "tax_amount", "shipping_amount", "discount_amount", "grand_total"):
            if role in fields and fields[role] not in (None, ""):
                setattr(parsed.summary, role, clean_num(fields[role]))

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

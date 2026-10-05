import sqlite3
import json
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from backend.config import DB_PATH
from backend.models.document import (
    DocumentStatus,
    WebhookStatus,
    ParsedDocumentData,
    DocumentResponse
)

def get_connection():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                original_filename TEXT NOT NULL,
                file_path TEXT NOT NULL,
                file_size_bytes INTEGER DEFAULT 0,
                file_type TEXT NOT NULL,
                source TEXT DEFAULT 'manual_upload',
                sender_email TEXT,
                status TEXT NOT NULL,
                confidence REAL DEFAULT 1.0,
                parsed_json TEXT,
                webhook_status TEXT DEFAULT 'PENDING',
                webhook_response_code INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS webhook_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id TEXT NOT NULL,
                webhook_url TEXT NOT NULL,
                status_code INTEGER,
                response_body TEXT,
                duration_ms REAL,
                success INTEGER,
                timestamp TEXT NOT NULL,
                FOREIGN KEY (document_id) REFERENCES documents (id)
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)
    conn.close()

class DocumentRepository:

    @classmethod
    def create(cls, doc_id: str, filename: str, original_filename: str, file_path: str,
               file_size: int, file_type: str, source: str = "manual_upload",
               sender_email: Optional[str] = None) -> str:
        now = datetime.now(timezone.utc).isoformat()
        conn = get_connection()
        with conn:
            conn.execute("""
                INSERT INTO documents (
                    id, filename, original_filename, file_path, file_size_bytes,
                    file_type, source, sender_email, status, confidence,
                    parsed_json, webhook_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                doc_id, filename, original_filename, file_path, file_size,
                file_type, source, sender_email, DocumentStatus.PROCESSING.value, 0.0,
                None, WebhookStatus.PENDING.value, now, now
            ))
        conn.close()
        return doc_id

    @classmethod
    def update_parsed_data(cls, doc_id: str, data: ParsedDocumentData,
                           webhook_status: Optional[WebhookStatus] = None,
                           webhook_code: Optional[int] = None):
        now = datetime.now(timezone.utc).isoformat()
        status_val = data.verification.status.value if data.verification else DocumentStatus.REVIEW_REQUIRED.value
        confidence_val = data.verification.confidence_score if data.verification else 0.5
        json_str = data.model_dump_json()

        conn = get_connection()
        with conn:
            if webhook_status:
                conn.execute("""
                    UPDATE documents
                    SET status = ?, confidence = ?, parsed_json = ?,
                        webhook_status = ?, webhook_response_code = ?, updated_at = ?
                    WHERE id = ?
                """, (status_val, confidence_val, json_str, webhook_status.value, webhook_code, now, doc_id))
            else:
                conn.execute("""
                    UPDATE documents
                    SET status = ?, confidence = ?, parsed_json = ?, updated_at = ?
                    WHERE id = ?
                """, (status_val, confidence_val, json_str, now, doc_id))
        conn.close()

    @classmethod
    def update_webhook_status(cls, doc_id: str, status: WebhookStatus, response_code: Optional[int] = None):
        now = datetime.now(timezone.utc).isoformat()
        conn = get_connection()
        with conn:
            conn.execute("""
                UPDATE documents
                SET webhook_status = ?, webhook_response_code = ?, updated_at = ?
                WHERE id = ?
            """, (status.value, response_code, now, doc_id))
        conn.close()

    @classmethod
    def get_by_id(cls, doc_id: str) -> Optional[Dict[str, Any]]:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM documents WHERE id = ?", (doc_id,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        return cls._row_to_dict(row)

    @classmethod
    def list_all(cls, limit: int = 100, status: Optional[str] = None) -> List[Dict[str, Any]]:
        conn = get_connection()
        cursor = conn.cursor()
        if status:
            cursor.execute("SELECT * FROM documents WHERE status = ? ORDER BY created_at DESC LIMIT ?", (status, limit))
        else:
            cursor.execute("SELECT * FROM documents ORDER BY created_at DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        conn.close()
        return [cls._row_to_dict(r) for r in rows]

    @classmethod
    def delete(cls, doc_id: str) -> bool:
        conn = get_connection()
        with conn:
            conn.execute("DELETE FROM webhook_logs WHERE document_id = ?", (doc_id,))
            cursor = conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            deleted = cursor.rowcount > 0
        conn.close()
        return deleted

    @classmethod
    def get_stats(cls) -> Dict[str, Any]:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                COUNT(*) as total_documents,
                SUM(CASE WHEN status = 'VERIFIED' THEN 1 ELSE 0 END) as verified_count,
                SUM(CASE WHEN status = 'REVIEW_REQUIRED' THEN 1 ELSE 0 END) as review_count,
                SUM(CASE WHEN webhook_status = 'DELIVERED' THEN 1 ELSE 0 END) as delivered_webhooks
            FROM documents
        """)
        row = cursor.fetchone()
        conn.close()
        return {
            "total_documents": row["total_documents"] or 0,
            "verified_count": row["verified_count"] or 0,
            "review_count": row["review_count"] or 0,
            "delivered_webhooks": row["delivered_webhooks"] or 0
        }

    @classmethod
    def _row_to_dict(cls, row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        if d.get("parsed_json"):
            try:
                d["data"] = json.loads(d["parsed_json"])
            except Exception:
                d["data"] = None
        else:
            d["data"] = None
        return d

class WebhookLogRepository:
    @classmethod
    def log(cls, doc_id: str, url: str, status_code: int, response_body: str, duration_ms: float, success: bool):
        now = datetime.now(timezone.utc).isoformat()
        conn = get_connection()
        with conn:
            conn.execute("""
                INSERT INTO webhook_logs (
                    document_id, webhook_url, status_code, response_body, duration_ms, success, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (doc_id, url, status_code, response_body, duration_ms, 1 if success else 0, now))
        conn.close()

    @classmethod
    def list_by_document(cls, doc_id: str) -> List[Dict[str, Any]]:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM webhook_logs WHERE document_id = ? ORDER BY timestamp DESC", (doc_id,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

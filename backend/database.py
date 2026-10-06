import sqlite3
import json
import os
import secrets
import uuid
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any
from urllib.parse import urlsplit, unquote
from backend.config import DB_PATH, DATA_DIR, settings
from backend.security import decrypt_secret, encrypt_secret, hash_token
from backend.models.document import (
    DocumentStatus,
    WebhookStatus,
    ParsedDocumentData,
    DocumentResponse
)

class PostgresConnection:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        self.connection.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return self.connection.__exit__(exc_type, exc_value, traceback)

    def execute(self, query: str, params=()):
        return self.connection.execute(query.replace("?", "%s"), params)

    def close(self):
        self.connection.close()


def get_connection():
    if os.getenv("DOCFLOW_TESTING") == "1":
        test_db_path = os.getenv("DOCFLOW_TEST_DB_PATH", str(DATA_DIR / "docflow-test.db"))
        conn = sqlite3.connect(test_db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    parsed_url = urlsplit(settings.database_url)
    if parsed_url.scheme.startswith("sqlite"):
        path = unquote(parsed_url.path)
        if parsed_url.netloc:
            path = f"//{parsed_url.netloc}{path}"
        conn = sqlite3.connect(path or str(DB_PATH))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    import psycopg
    from psycopg.rows import dict_row

    return PostgresConnection(psycopg.connect(settings.database_url, row_factory=dict_row))

def init_db():
    conn = get_connection()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                full_name TEXT NOT NULL,
                company_name TEXT NOT NULL DEFAULT '',
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS account_settings (
                user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                webhook_url TEXT NOT NULL DEFAULT '',
                webhook_secret_enc TEXT NOT NULL DEFAULT '',
                default_provider TEXT NOT NULL DEFAULT 'auto',
                gemini_api_key_enc TEXT NOT NULL DEFAULT '',
                openai_api_key_enc TEXT NOT NULL DEFAULT '',
                mistral_api_key_enc TEXT NOT NULL DEFAULT '',
                openrouter_api_key_enc TEXT NOT NULL DEFAULT '',
                openrouter_model TEXT NOT NULL DEFAULT 'google/gemini-2.5-flash',
                gemini_model TEXT NOT NULL DEFAULT 'gemini-3.1-flash-lite',
                openai_model TEXT NOT NULL DEFAULT 'gpt-4o-mini',
                mistral_model TEXT NOT NULL DEFAULT 'mistral-small-2506',
                auto_dispatch_on_verified INTEGER NOT NULL DEFAULT 0,
                inbound_token_hash TEXT NOT NULL,
                inbound_token_enc TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS login_attempts (
                email TEXT NOT NULL,
                client_ip TEXT NOT NULL,
                attempted_at TEXT NOT NULL,
                succeeded INTEGER NOT NULL
        );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS provider_credentials (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                provider TEXT NOT NULL,
                label TEXT NOT NULL,
                api_key_enc TEXT NOT NULL,
                model TEXT NOT NULL,
                is_selected INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_provider_credentials_user ON provider_credentials(user_id, provider)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
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
            CREATE TABLE IF NOT EXISTS document_source_text (
                document_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
                text_content TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)
        # Older versions allowed one selected key per provider. Keep only the
        # selected key for the saved default provider before enforcing one key.
        conn.execute("""
            WITH ranked AS (
                SELECT pc.id,
                    ROW_NUMBER() OVER (
                        PARTITION BY pc.user_id
                        ORDER BY CASE WHEN pc.provider = COALESCE((
                            SELECT default_provider FROM account_settings WHERE user_id = pc.user_id
                        ), '') THEN 0 ELSE 1 END, pc.updated_at DESC
                    ) AS position
                FROM provider_credentials pc
                WHERE pc.is_selected = 1
            )
            UPDATE provider_credentials SET is_selected = 0
            WHERE id IN (SELECT id FROM ranked WHERE position > 1)
        """)
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_provider_credentials_one_selected ON provider_credentials(user_id) WHERE is_selected = 1")
        conn.execute("DROP TABLE IF EXISTS credential_security")
        log_id_type = "INTEGER PRIMARY KEY AUTOINCREMENT" if isinstance(conn, sqlite3.Connection) else "BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY"
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS webhook_logs (
                id {log_id_type},
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
            CREATE TABLE IF NOT EXISTS processing_runs (
                id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                run_number INTEGER NOT NULL,
                file_path TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'QUEUED',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                locked_until TEXT,
                updated_at TEXT NOT NULL,
                error TEXT,
                provider TEXT,
                model TEXT,
                document_status TEXT,
                outcome TEXT,
                UNIQUE(document_id, run_number)
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_processing_runs_queue ON processing_runs(status, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_processing_runs_document ON processing_runs(document_id, run_number DESC)")
        if isinstance(conn, sqlite3.Connection):
            run_columns = {row["name"] for row in conn.execute("PRAGMA table_info(processing_runs)").fetchall()}
            for name in ("document_status", "outcome"):
                if name not in run_columns:
                    conn.execute(f"ALTER TABLE processing_runs ADD COLUMN {name} TEXT")
        else:
            conn.execute("ALTER TABLE processing_runs ADD COLUMN IF NOT EXISTS document_status TEXT")
            conn.execute("ALTER TABLE processing_runs ADD COLUMN IF NOT EXISTS outcome TEXT")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)
        if isinstance(conn, sqlite3.Connection):
            settings_columns = {row["name"] for row in conn.execute("PRAGMA table_info(account_settings)").fetchall()}
            for name, definition in (("mistral_api_key_enc", "TEXT NOT NULL DEFAULT ''"), ("openrouter_api_key_enc", "TEXT NOT NULL DEFAULT ''"), ("openrouter_model", "TEXT NOT NULL DEFAULT 'google/gemini-2.5-flash'"), ("gemini_model", "TEXT NOT NULL DEFAULT 'gemini-3.1-flash-lite'"), ("openai_model", "TEXT NOT NULL DEFAULT 'gpt-4o-mini'"), ("mistral_model", "TEXT NOT NULL DEFAULT 'mistral-small-2506'")):
                if name not in settings_columns:
                    conn.execute(f"ALTER TABLE account_settings ADD COLUMN {name} {definition}")
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(documents)").fetchall()}
            if "user_id" not in columns:
                conn.execute("ALTER TABLE documents ADD COLUMN user_id TEXT REFERENCES users(id) ON DELETE CASCADE")
        else:
            conn.execute("ALTER TABLE account_settings ADD COLUMN IF NOT EXISTS mistral_api_key_enc TEXT NOT NULL DEFAULT ''")
            conn.execute("ALTER TABLE account_settings ADD COLUMN IF NOT EXISTS openrouter_api_key_enc TEXT NOT NULL DEFAULT ''")
            conn.execute("ALTER TABLE account_settings ADD COLUMN IF NOT EXISTS openrouter_model TEXT NOT NULL DEFAULT 'google/gemini-2.5-flash'")
            conn.execute("ALTER TABLE account_settings ADD COLUMN IF NOT EXISTS gemini_model TEXT NOT NULL DEFAULT 'gemini-3.1-flash-lite'")
            conn.execute("ALTER TABLE account_settings ADD COLUMN IF NOT EXISTS openai_model TEXT NOT NULL DEFAULT 'gpt-4o-mini'")
            conn.execute("ALTER TABLE account_settings ADD COLUMN IF NOT EXISTS mistral_model TEXT NOT NULL DEFAULT 'mistral-small-2506'")
            conn.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS user_id TEXT REFERENCES users(id) ON DELETE CASCADE")
        # Move existing single-key settings into the credential vault once.
        for legacy in conn.execute("SELECT * FROM account_settings").fetchall():
            legacy = dict(legacy)
            for provider, key_field, model_field in (
                ("gemini", "gemini_api_key_enc", "gemini_model"),
                ("openai", "openai_api_key_enc", "openai_model"),
                ("mistral", "mistral_api_key_enc", "mistral_model"),
                ("openrouter", "openrouter_api_key_enc", "openrouter_model"),
            ):
                encrypted = legacy.get(key_field) or ""
                if not encrypted or conn.execute("SELECT 1 FROM provider_credentials WHERE user_id = ? AND provider = ?", (legacy["user_id"], provider)).fetchone():
                    continue
                api_key = decrypt_secret(encrypted)
                if not api_key:
                    continue
                now = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    "INSERT INTO provider_credentials (id, user_id, provider, label, api_key_enc, model, is_selected, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
                    (str(uuid.uuid4()), legacy["user_id"], provider, f"{provider.title()} key", encrypt_secret(api_key), legacy.get(model_field) or "", now, now),
                )
                conn.execute(f"UPDATE account_settings SET {key_field} = '' WHERE user_id = ?", (legacy["user_id"],))
    conn.close()


class UserRepository:
    @staticmethod
    def _migrate_legacy_data(conn, user_id: str) -> None:
        if os.getenv("DOCFLOW_TESTING") == "1" or urlsplit(settings.database_url).scheme.startswith("sqlite") or not DB_PATH.is_file():
            return
        try:
            legacy = sqlite3.connect(str(DB_PATH))
            legacy.row_factory = sqlite3.Row
            documents = [dict(row) for row in legacy.execute("SELECT * FROM documents").fetchall()]
            legacy_logs = []
            try:
                legacy_logs = [dict(row) for row in legacy.execute("SELECT * FROM webhook_logs").fetchall()]
            except sqlite3.OperationalError:
                pass
        except sqlite3.Error:
            return
        finally:
            if "legacy" in locals():
                legacy.close()

        conn.execute("UPDATE documents SET user_id = ? WHERE user_id IS NULL", (user_id,))
        fields = (
            "id", "filename", "original_filename", "file_path", "file_size_bytes", "file_type",
            "source", "sender_email", "status", "confidence", "parsed_json", "webhook_status",
            "webhook_response_code", "created_at", "updated_at",
        )
        for document in documents:
            if conn.execute("SELECT 1 FROM documents WHERE id = ?", (document["id"],)).fetchone():
                continue
            conn.execute(
                "INSERT INTO documents (id, user_id, filename, original_filename, file_path, file_size_bytes, file_type, source, sender_email, status, confidence, parsed_json, webhook_status, webhook_response_code, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (document["id"], user_id, *(document.get(field) for field in fields[1:])),
            )
        for log in legacy_logs:
            if conn.execute("SELECT 1 FROM documents WHERE id = ? AND user_id = ?", (log["document_id"], user_id)).fetchone():
                conn.execute(
                    "INSERT INTO webhook_logs (document_id, webhook_url, status_code, response_body, duration_ms, success, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    tuple(log.get(field) for field in ("document_id", "webhook_url", "status_code", "response_body", "duration_ms", "success", "timestamp")),
                )

    @classmethod
    def create(cls, email: str, full_name: str, company_name: str, password_hash: str) -> Dict[str, Any]:
        user_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        conn = get_connection()
        with conn:
            first_account = conn.execute("SELECT COUNT(*) AS total FROM users").fetchone()["total"] == 0
            conn.execute(
                "INSERT INTO users (id, email, full_name, company_name, password_hash, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, email, full_name, company_name, password_hash, now, now),
            )
            token = secrets.token_urlsafe(32)
            conn.execute(
                "INSERT INTO account_settings (user_id, default_provider, inbound_token_hash, inbound_token_enc, updated_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, settings.default_provider, hash_token(token), encrypt_secret(token), now),
            )
            if first_account:
                cls._migrate_legacy_data(conn, user_id)
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        conn.close()
        return dict(row)

    @classmethod
    def get_by_email(cls, email: str) -> Optional[Dict[str, Any]]:
        conn = get_connection()
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        conn.close()
        return dict(row) if row else None

    @classmethod
    def get_by_id(cls, user_id: str) -> Optional[Dict[str, Any]]:
        conn = get_connection()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    @classmethod
    def get_by_inbound_token(cls, token: str) -> Optional[Dict[str, Any]]:
        conn = get_connection()
        row = conn.execute(
            "SELECT users.* FROM users JOIN account_settings ON account_settings.user_id = users.id WHERE account_settings.inbound_token_hash = ?",
            (hash_token(token),),
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    @classmethod
    def update_profile(cls, user_id: str, email: str, full_name: str, company_name: str) -> None:
        conn = get_connection()
        with conn:
            conn.execute(
                "UPDATE users SET email = ?, full_name = ?, company_name = ?, updated_at = ? WHERE id = ?",
                (email, full_name, company_name, datetime.now(timezone.utc).isoformat(), user_id),
            )
        conn.close()

    @classmethod
    def update_password(cls, user_id: str, password_hash: str) -> None:
        conn = get_connection()
        with conn:
            conn.execute("UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?", (password_hash, datetime.now(timezone.utc).isoformat(), user_id))
        conn.close()

    @classmethod
    def failed_login_count(cls, client_ip: str, since: str) -> int:
        conn = get_connection()
        row = conn.execute(
            "SELECT COUNT(*) AS total FROM login_attempts WHERE client_ip = ? AND succeeded = 0 AND attempted_at >= ?",
            (client_ip, since),
        ).fetchone()
        conn.close()
        return row["total"]

    @classmethod
    def record_login_attempt(cls, email: str, client_ip: str, succeeded: bool, since: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn = get_connection()
        with conn:
            conn.execute("DELETE FROM login_attempts WHERE attempted_at < ?", (since,))
            if succeeded:
                conn.execute("DELETE FROM login_attempts WHERE client_ip = ? AND email = ?", (client_ip, email))
            else:
                conn.execute(
                    "INSERT INTO login_attempts (email, client_ip, attempted_at, succeeded) VALUES (?, ?, ?, 0)",
                    (email, client_ip, now),
                )
        conn.close()


class AccountSettingsRepository:
    @classmethod
    def get(cls, user_id: str) -> Dict[str, Any]:
        conn = get_connection()
        row = conn.execute("SELECT * FROM account_settings WHERE user_id = ?", (user_id,)).fetchone()
        conn.close()
        if not row:
            return {}
        result = dict(row)
        for field in ("webhook_secret_enc", "gemini_api_key_enc", "openai_api_key_enc", "mistral_api_key_enc", "openrouter_api_key_enc", "inbound_token_enc"):
            result[field.removesuffix("_enc")] = decrypt_secret(result.get(field))
        result["provider_credentials"] = ProviderCredentialRepository.selected_for_user(user_id)
        return result

    @classmethod
    def update(cls, user_id: str, values: Dict[str, Any]) -> None:
        current = cls.get(user_id)
        fields = {
            "webhook_url": values.get("webhook_url", current.get("webhook_url", "")),
            "webhook_secret_enc": encrypt_secret(values.get("webhook_secret", current.get("webhook_secret", ""))),
            "default_provider": values.get("default_provider", current.get("default_provider", "auto")),
            "gemini_api_key_enc": "",
            "openai_api_key_enc": "",
            "mistral_api_key_enc": "",
            "openrouter_api_key_enc": "",
            "openrouter_model": values.get("openrouter_model", current.get("openrouter_model", "google/gemini-2.5-flash")),
            "gemini_model": values.get("gemini_model", current.get("gemini_model", "gemini-3.1-flash-lite")),
            "openai_model": values.get("openai_model", current.get("openai_model", "gpt-4o-mini")),
            "mistral_model": values.get("mistral_model", current.get("mistral_model", "mistral-small-2506")),
            "auto_dispatch_on_verified": int(values.get("auto_dispatch_on_verified", current.get("auto_dispatch_on_verified", 0))),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "user_id": user_id,
        }
        conn = get_connection()
        with conn:
            conn.execute("""
                UPDATE account_settings SET webhook_url = ?, webhook_secret_enc = ?, default_provider = ?,
                    gemini_api_key_enc = ?, openai_api_key_enc = ?, mistral_api_key_enc = ?, openrouter_api_key_enc = ?, openrouter_model = ?, gemini_model = ?, openai_model = ?, mistral_model = ?, auto_dispatch_on_verified = ?, updated_at = ?
                WHERE user_id = ?
            """, tuple(fields[key] for key in (
                "webhook_url", "webhook_secret_enc", "default_provider", "gemini_api_key_enc",
                "openai_api_key_enc", "mistral_api_key_enc", "openrouter_api_key_enc", "openrouter_model", "gemini_model", "openai_model", "mistral_model", "auto_dispatch_on_verified", "updated_at", "user_id",
            )))
        conn.close()

    @classmethod
    def rotate_inbound_token(cls, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        conn = get_connection()
        with conn:
            conn.execute(
                "UPDATE account_settings SET inbound_token_hash = ?, inbound_token_enc = ?, updated_at = ? WHERE user_id = ?",
                (hash_token(token), encrypt_secret(token), datetime.now(timezone.utc).isoformat(), user_id),
            )
        conn.close()
        return token

class ProviderCredentialRepository:
    @classmethod
    def list_for_user(cls, user_id: str) -> list[Dict[str, Any]]:
        conn = get_connection()
        rows = conn.execute("SELECT * FROM provider_credentials WHERE user_id = ? ORDER BY provider, created_at", (user_id,)).fetchall()
        conn.close()
        result = []
        for row in rows:
            item = dict(row)
            item.pop("api_key_enc", None)
            item["masked"] = True
            result.append(item)
        return result

    @classmethod
    def selected_for_user(cls, user_id: str) -> Dict[str, Dict[str, str]]:
        conn = get_connection()
        rows = conn.execute("SELECT provider, api_key_enc, model FROM provider_credentials WHERE user_id = ? AND is_selected = 1", (user_id,)).fetchall()
        conn.close()
        return {row["provider"]: {"api_key": decrypt_secret(row["api_key_enc"]), "model": row["model"]} for row in rows}

    @classmethod
    def create(cls, user_id: str, provider: str, label: str, api_key: str, model: str) -> str:
        credential_id, now = str(uuid.uuid4()), datetime.now(timezone.utc).isoformat()
        conn = get_connection()
        with conn:
            selected = conn.execute("SELECT COUNT(*) AS total FROM provider_credentials WHERE user_id = ? AND is_selected = 1", (user_id,)).fetchone()["total"] == 0
            conn.execute("INSERT INTO provider_credentials (id, user_id, provider, label, api_key_enc, model, is_selected, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (credential_id, user_id, provider, label, encrypt_secret(api_key), model, int(selected), now, now))
            if selected:
                conn.execute("UPDATE account_settings SET default_provider = ?, updated_at = ? WHERE user_id = ?", (provider, now, user_id))
        conn.close()
        return credential_id

    @classmethod
    def update(cls, user_id: str, credential_id: str, label: str, model: str, api_key: Optional[str] = None) -> bool:
        conn = get_connection()
        with conn:
            if api_key:
                result = conn.execute("UPDATE provider_credentials SET label = ?, model = ?, api_key_enc = ?, updated_at = ? WHERE id = ? AND user_id = ?", (label, model, encrypt_secret(api_key), datetime.now(timezone.utc).isoformat(), credential_id, user_id))
            else:
                result = conn.execute("UPDATE provider_credentials SET label = ?, model = ?, updated_at = ? WHERE id = ? AND user_id = ?", (label, model, datetime.now(timezone.utc).isoformat(), credential_id, user_id))
        conn.close()
        return result.rowcount > 0

    @classmethod
    def select(cls, user_id: str, credential_id: str) -> bool:
        conn = get_connection()
        with conn:
            row = conn.execute("SELECT provider FROM provider_credentials WHERE id = ? AND user_id = ?", (credential_id, user_id)).fetchone()
            if row:
                conn.execute("UPDATE provider_credentials SET is_selected = 0 WHERE user_id = ?", (user_id,))
                conn.execute("UPDATE provider_credentials SET is_selected = 1, updated_at = ? WHERE id = ? AND user_id = ?", (datetime.now(timezone.utc).isoformat(), credential_id, user_id))
                conn.execute("UPDATE account_settings SET default_provider = ?, updated_at = ? WHERE user_id = ?", (row["provider"], datetime.now(timezone.utc).isoformat(), user_id))
        conn.close()
        return bool(row)

    @classmethod
    def activate_provider(cls, user_id: str, provider: str) -> bool:
        conn = get_connection()
        now = datetime.now(timezone.utc).isoformat()
        with conn:
            active = conn.execute("SELECT id, provider FROM provider_credentials WHERE user_id = ? AND is_selected = 1", (user_id,)).fetchone()
            choices = conn.execute("SELECT id FROM provider_credentials WHERE user_id = ? AND provider = ? ORDER BY created_at", (user_id, provider)).fetchall()
            credential = active if active and active["provider"] == provider else (choices[0] if len(choices) == 1 else None)
            conn.execute("UPDATE provider_credentials SET is_selected = 0 WHERE user_id = ?", (user_id,))
            if credential:
                conn.execute("UPDATE provider_credentials SET is_selected = 1, updated_at = ? WHERE id = ?", (now, credential["id"]))
            conn.execute("UPDATE account_settings SET default_provider = ?, updated_at = ? WHERE user_id = ?", (provider, now, user_id))
        conn.close()
        return bool(credential)

    @classmethod
    def delete(cls, user_id: str, credential_id: str) -> bool:
        conn = get_connection()
        with conn:
            row = conn.execute("SELECT provider, is_selected FROM provider_credentials WHERE id = ? AND user_id = ?", (credential_id, user_id)).fetchone()
            result = conn.execute("DELETE FROM provider_credentials WHERE id = ? AND user_id = ?", (credential_id, user_id))
            if row and row["is_selected"]:
                replacement = conn.execute("SELECT id, provider FROM provider_credentials WHERE user_id = ? ORDER BY updated_at DESC LIMIT 1", (user_id,)).fetchone()
                if replacement:
                    conn.execute("UPDATE provider_credentials SET is_selected = 1 WHERE id = ?", (replacement["id"],))
                    next_provider = replacement["provider"]
                else:
                    next_provider = "heuristic"
                conn.execute("UPDATE account_settings SET default_provider = ?, updated_at = ? WHERE user_id = ?", (next_provider, datetime.now(timezone.utc).isoformat(), user_id))
        conn.close()
        return result.rowcount > 0


class DocumentRepository:

    @classmethod
    def create(cls, doc_id: str, filename: str, original_filename: str, file_path: str,
               file_size: int, file_type: str, source: str = "manual_upload",
               sender_email: Optional[str] = None, user_id: Optional[str] = None) -> str:
        now = datetime.now(timezone.utc).isoformat()
        conn = get_connection()
        with conn:
            conn.execute("""
                INSERT INTO documents (
                    id, user_id, filename, original_filename, file_path, file_size_bytes,
                    file_type, source, sender_email, status, confidence,
                    parsed_json, webhook_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                doc_id, user_id, filename, original_filename, file_path, file_size,
                file_type, source, sender_email, DocumentStatus.PROCESSING.value, 0.0,
                None, WebhookStatus.PENDING.value, now, now
            ))
        conn.close()
        return doc_id

    @classmethod
    def update_parsed_data(cls, doc_id: str, data: ParsedDocumentData,
                           webhook_status: Optional[WebhookStatus] = None,
                           webhook_code: Optional[int] = None, user_id: Optional[str] = None):
        now = datetime.now(timezone.utc).isoformat()
        status_val = data.verification.status.value if data.verification else DocumentStatus.REVIEW_REQUIRED.value
        confidence_val = data.verification.confidence_score if data.verification else 0.5
        json_str = data.model_dump_json()

        conn = get_connection()
        with conn:
            if data.source_text is not None:
                conn.execute("""
                    INSERT INTO document_source_text (document_id, text_content, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(document_id) DO UPDATE SET
                        text_content = excluded.text_content,
                        updated_at = excluded.updated_at
                """, (doc_id, data.source_text[:200000], now))
            if webhook_status:
                conn.execute("""
                    UPDATE documents
                    SET status = ?, confidence = ?, parsed_json = ?,
                        webhook_status = ?, webhook_response_code = ?, updated_at = ?
                    WHERE id = ? AND user_id = ?
                """, (status_val, confidence_val, json_str, webhook_status.value, webhook_code, now, doc_id, user_id))
            else:
                query = """
                    UPDATE documents
                    SET status = ?, confidence = ?, parsed_json = ?, updated_at = ?
                    WHERE id = ?
                """
                params = (status_val, confidence_val, json_str, now, doc_id)
                if user_id is not None:
                    query += " AND user_id = ?"
                    params += (user_id,)
                conn.execute(query, params)
        conn.close()

    @classmethod
    def get_source_text(cls, doc_id: str) -> Optional[str]:
        conn = get_connection()
        row = conn.execute("SELECT text_content FROM document_source_text WHERE document_id = ?", (doc_id,)).fetchone()
        conn.close()
        return row["text_content"] if row else None

    @classmethod
    def update_webhook_status(cls, doc_id: str, status: WebhookStatus, response_code: Optional[int] = None, user_id: Optional[str] = None):
        now = datetime.now(timezone.utc).isoformat()
        conn = get_connection()
        with conn:
            conn.execute("""
                UPDATE documents
                SET webhook_status = ?, webhook_response_code = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
            """, (status.value, response_code, now, doc_id, user_id))
        conn.close()

    @classmethod
    def get_by_id(cls, doc_id: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        conn = get_connection()
        query = "SELECT * FROM documents WHERE id = ?"
        params = (doc_id,)
        if user_id is not None:
            query += " AND user_id = ?"
            params += (user_id,)
        row = conn.execute(query, params).fetchone()
        conn.close()
        if not row:
            return None
        return cls._row_to_dict(row)

    @classmethod
    def list_all(cls, limit: int = 100, status: Optional[str] = None, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        conn = get_connection()
        query = "SELECT * FROM documents WHERE user_id = ?"
        params: tuple = (user_id,)
        if status:
            query += " AND status = ?"
            params += (status,)
        query += " ORDER BY created_at DESC LIMIT ?"
        params += (limit,)
        rows = conn.execute(query, params).fetchall()
        conn.close()
        return [cls._row_to_dict(r) for r in rows]

    @classmethod
    def delete(cls, doc_id: str, user_id: str) -> bool:
        conn = get_connection()
        with conn:
            owned = conn.execute("SELECT 1 FROM documents WHERE id = ? AND user_id = ?", (doc_id, user_id)).fetchone()
            if not owned:
                deleted = False
            else:
                conn.execute("DELETE FROM webhook_logs WHERE document_id = ?", (doc_id,))
                cursor = conn.execute("DELETE FROM documents WHERE id = ? AND user_id = ?", (doc_id, user_id))
                deleted = cursor.rowcount > 0
        conn.close()
        return deleted

    @classmethod
    def get_stats(cls, user_id: str) -> Dict[str, Any]:
        conn = get_connection()
        row = conn.execute("""
            SELECT 
                COUNT(*) as total_documents,
                SUM(CASE WHEN status = 'VERIFIED' THEN 1 ELSE 0 END) as verified_count,
                SUM(CASE WHEN status = 'REVIEW_REQUIRED' THEN 1 ELSE 0 END) as review_count,
                SUM(CASE WHEN status = 'PROCESSING' THEN 1 ELSE 0 END) as processing_count,
                SUM(CASE WHEN webhook_status = 'DELIVERED' THEN 1 ELSE 0 END) as delivered_webhooks
            FROM documents WHERE user_id = ?
        """, (user_id,)).fetchone()
        conn.close()
        return {
            "total_documents": row["total_documents"] or 0,
            "verified_count": row["verified_count"] or 0,
            "review_count": row["review_count"] or 0,
            "processing_count": row["processing_count"] or 0,
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

class ProcessingRunRepository:
    LOCK_MINUTES = 2

    @classmethod
    def enqueue(cls, doc_id: str, user_id: str, file_path: str, mime_type: str) -> str:
        run_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        conn = get_connection()
        try:
            with conn:
                if isinstance(conn, sqlite3.Connection):
                    conn.execute("BEGIN IMMEDIATE")
                else:
                    conn.execute("SELECT id FROM documents WHERE id = ? AND user_id = ? FOR UPDATE", (doc_id, user_id)).fetchone()
                active = conn.execute("SELECT id FROM processing_runs WHERE document_id = ? AND status IN ('QUEUED', 'RUNNING') LIMIT 1", (doc_id,)).fetchone()
                if active:
                    raise ValueError("An extraction run is already queued or running for this document")
                row = conn.execute("SELECT COALESCE(MAX(run_number), 0) + 1 AS next_run FROM processing_runs WHERE document_id = ?", (doc_id,)).fetchone()
                run_number = row["next_run"]
                conn.execute("""
                    INSERT INTO processing_runs (id, document_id, user_id, run_number, file_path, mime_type, status, attempt_count, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'QUEUED', 0, ?, ?)
                """, (run_id, doc_id, user_id, run_number, str(file_path), mime_type, now, now))
                conn.execute("UPDATE documents SET status = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                             (DocumentStatus.PROCESSING.value, now, doc_id, user_id))
        finally:
            conn.close()
        return run_id

    @classmethod
    def claim_next(cls) -> Optional[Dict[str, Any]]:
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        locked_until = (now_dt + timedelta(minutes=cls.LOCK_MINUTES)).isoformat()
        conn = get_connection()
        claimed = None
        try:
            with conn:
                if isinstance(conn, sqlite3.Connection):
                    conn.execute("BEGIN IMMEDIATE")
                    row = conn.execute("""
                        SELECT * FROM processing_runs
                        WHERE status = 'QUEUED' OR (status = 'RUNNING' AND locked_until <= ?)
                        ORDER BY created_at ASC LIMIT 1
                    """, (now,)).fetchone()
                else:
                    row = conn.execute("""
                        SELECT * FROM processing_runs
                        WHERE status = 'QUEUED' OR (status = 'RUNNING' AND locked_until <= ?)
                        ORDER BY created_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED
                    """, (now,)).fetchone()
                if row:
                    run = dict(row)
                    conn.execute("""
                        UPDATE processing_runs SET status = 'RUNNING', attempt_count = attempt_count + 1,
                            started_at = COALESCE(started_at, ?), locked_until = ?, updated_at = ?, error = NULL
                        WHERE id = ?
                    """, (now, locked_until, now, run["id"]))
                    run["status"] = "RUNNING"
                    run["attempt_count"] += 1
                    run["locked_until"] = locked_until
                    claimed = run
        finally:
            conn.close()
        return claimed

    @classmethod
    def heartbeat(cls, run_id: str) -> None:
        now_dt = datetime.now(timezone.utc)
        conn = get_connection()
        with conn:
            conn.execute("UPDATE processing_runs SET locked_until = ?, updated_at = ? WHERE id = ? AND status = 'RUNNING'",
                         ((now_dt + timedelta(minutes=cls.LOCK_MINUTES)).isoformat(), now_dt.isoformat(), run_id))
        conn.close()

    @classmethod
    def finish(cls, run_id: str, status: str, error: Optional[str] = None,
               provider: Optional[str] = None, model: Optional[str] = None,
               document_status: Optional[str] = None, outcome: Optional[str] = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn = get_connection()
        with conn:
            conn.execute("""
                UPDATE processing_runs SET status = ?, error = ?, provider = ?, model = ?, document_status = ?, outcome = ?,
                    finished_at = ?, locked_until = NULL, updated_at = ? WHERE id = ?
            """, (status, error[:1000] if error else None, provider, model, document_status, outcome[:1000] if outcome else None, now, now, run_id))
        conn.close()

    @classmethod
    def requeue(cls, run_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn = get_connection()
        with conn:
            conn.execute("UPDATE processing_runs SET status = 'QUEUED', locked_until = NULL, updated_at = ?, error = 'Worker stopped; returned to queue' WHERE id = ? AND status = 'RUNNING'", (now, run_id))
        conn.close()

    @classmethod
    def is_active(cls, doc_id: str) -> bool:
        conn = get_connection()
        row = conn.execute("SELECT 1 FROM processing_runs WHERE document_id = ? AND status IN ('QUEUED', 'RUNNING') LIMIT 1", (doc_id,)).fetchone()
        conn.close()
        return bool(row)

    @classmethod
    def list_for_document(cls, doc_id: str, user_id: str) -> List[Dict[str, Any]]:
        conn = get_connection()
        rows = conn.execute("""
            SELECT id, document_id, user_id, run_number, status, attempt_count, created_at,
                started_at, finished_at, updated_at, error, provider, model, document_status, outcome
            FROM processing_runs WHERE document_id = ? AND user_id = ? ORDER BY run_number DESC
        """, (doc_id, user_id)).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    @classmethod
    def recover_orphans(cls) -> None:
        conn = get_connection()
        rows = conn.execute("""
            SELECT d.id, d.user_id, d.file_path, d.file_type FROM documents d
            LEFT JOIN processing_runs r ON r.document_id = d.id
            WHERE d.status = 'PROCESSING' AND r.id IS NULL
        """).fetchall()
        conn.close()
        mime_types = {"PDF": "application/pdf", "PNG": "image/png", "JPG": "image/jpeg", "JPEG": "image/jpeg", "WEBP": "image/webp", "TIFF": "image/tiff"}
        for row in rows:
            try:
                cls.enqueue(row["id"], row["user_id"], row["file_path"], mime_types.get(row["file_type"], "application/octet-stream"))
            except ValueError:
                pass


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
        rows = conn.execute("SELECT * FROM webhook_logs WHERE document_id = ? ORDER BY timestamp DESC", (doc_id,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

import base64
import hashlib
import hmac
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet

from backend.config import DATA_DIR, settings


def get_app_secret() -> str:
    if settings.app_secret_key:
        return settings.app_secret_key
    if os.getenv("DOCFLOW_TESTING") == "1":
        return "docflow-test-session-key-not-for-production"

    secret_path = DATA_DIR / "app.secret"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return secret_path.read_text(encoding="utf-8").strip()
    secret = secrets.token_urlsafe(48)
    with os.fdopen(descriptor, "w", encoding="utf-8") as secret_file:
        secret_file.write(secret)
    return secret


def _fernet() -> Fernet:
    configured_key = os.getenv("APP_ENCRYPTION_KEY", "").strip()
    if configured_key:
        return Fernet(configured_key.encode())
    if os.getenv("DOCFLOW_TESTING") == "1":
        key = base64.urlsafe_b64encode(hashlib.sha256(b"docflow-test-encryption-key").digest())
        return Fernet(key)

    key_path = DATA_DIR / "encryption.key"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        key = key_path.read_bytes().strip()
    else:
        key = Fernet.generate_key()
        with os.fdopen(descriptor, "wb") as key_file:
            key_file.write(key)
    return Fernet(key)


def encrypt_secret(value: str | None) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str | None) -> str:
    if not value:
        return ""
    return _fernet().decrypt(value.encode()).decode()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=64)
    return "$scrypt$16384$8$1${}${}".format(
        base64.urlsafe_b64encode(salt).decode(),
        base64.urlsafe_b64encode(digest).decode(),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        _, algorithm, n_value, r_value, p_value, salt_value, digest_value = encoded.split("$")
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_value.encode())
        expected = base64.urlsafe_b64decode(digest_value.encode())
        actual = hashlib.scrypt(
            password.encode(),
            salt=salt,
            n=int(n_value),
            r=int(r_value),
            p=int(p_value),
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
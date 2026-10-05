import re
import uuid

from fastapi.testclient import TestClient

from backend.main import app
from backend.database import get_connection, UserRepository


def register(client: TestClient) -> str:
    page = client.get("/register")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    response = client.post("/register", data={
        "email": f"{uuid.uuid4()}@example.test",
        "full_name": "Account Test",
        "company_name": "",
        "password": "valid-test-password-123",
        "csrf_token": token,
    }, follow_redirects=False)
    assert response.status_code == 303
    dashboard = client.get("/")
    return re.search(r'<meta name="csrf-token" content="([^"]+)"', dashboard.text).group(1)


def test_anonymous_users_cannot_access_workspace_or_document_api():
    with TestClient(app) as client:
        assert client.get("/", follow_redirects=False).status_code == 303
        assert client.get("/api/documents").status_code == 401
        assert client.post("/api/inbound/test-mock").status_code == 401


def test_login_attempts_are_throttled_by_client_address():
    with TestClient(app) as client:
        page = client.get("/login")
        token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        for _ in range(10):
            response = client.post("/login", data={
                "email": "unknown@example.test",
                "password": "incorrect-password",
                "csrf_token": token,
            }, follow_redirects=False)
            assert response.status_code == 401
        limited = client.post("/login", data={
            "email": "unknown@example.test",
            "password": "incorrect-password",
            "csrf_token": token,
        }, follow_redirects=False)
        assert limited.status_code == 429


def test_state_changing_api_requests_require_csrf():
    with TestClient(app) as client:
        register(client)
        assert client.post("/api/settings", json={"default_provider": "heuristic"}).status_code == 403


def test_accounts_cannot_read_each_others_documents():
    with TestClient(app) as first_client, TestClient(app) as second_client:
        first_token = register(first_client)
        first_client.headers["X-CSRF-Token"] = first_token
        created = first_client.post("/api/inbound/test-mock")
        assert created.status_code == 200
        document_id = created.json()["document_id"]

        register(second_client)
        assert second_client.get(f"/api/documents/{document_id}").status_code == 404
        assert second_client.get("/api/documents").json()["documents"] == []


def test_account_secrets_are_encrypted_and_not_returned_by_settings_api():
    with TestClient(app) as client:
        client.headers["X-CSRF-Token"] = register(client)
        response = client.post("/api/settings", json={
            "default_provider": "gemini",
            "gemini_api_key": "account-gemini-secret",
            "webhook_secret": "account-webhook-secret",
        })
        assert response.status_code == 200
        assert "account-gemini-secret" not in response.text
        assert "account-webhook-secret" not in response.text

        email = client.get("/api/settings").json()["email"]
        user = UserRepository.get_by_email(email)
        conn = get_connection()
        row = conn.execute(
            "SELECT gemini_api_key_enc, webhook_secret_enc FROM account_settings WHERE user_id = ?",
            (user["id"],),
        ).fetchone()
        conn.close()
        assert row["gemini_api_key_enc"] != "account-gemini-secret"
        assert row["webhook_secret_enc"] != "account-webhook-secret"

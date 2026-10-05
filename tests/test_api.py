import io
import re
import uuid
import pytest
from fastapi.testclient import TestClient
from backend.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        response = test_client.get("/register")
        csrf_token = re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)
        registered = test_client.post("/register", data={
            "email": f"api-{uuid.uuid4()}@example.test",
            "full_name": "API Test User",
            "company_name": "",
            "password": "long-test-password-123",
            "csrf_token": csrf_token,
        }, follow_redirects=False)
        assert registered.status_code == 303
        dashboard = test_client.get("/")
        csrf_header = re.search(r'<meta name="csrf-token" content="([^"]+)"', dashboard.text).group(1)
        test_client.headers.update({"X-CSRF-Token": csrf_header})
        yield test_client

def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_dashboard_root_renders_html(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "DocFlow" in response.text


def test_mock_inbound_email(client):
    response = client.post("/api/inbound/test-mock")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "document_id" in data
    detail_response = client.get(f"/documents/{data['document_id']}")
    assert detail_response.status_code == 200
    assert detail_response.headers["content-type"].startswith("text/html")
    updated = client.put(f"/api/documents/{data['document_id']}", json={
        "document_type": "INVOICE",
        "document_number": "INV-CSV-1",
        "document_date": "2026-10-01",
        "line_items": [{"line_no": 1, "description": "Parts", "quantity": 1, "unit_price": 10, "line_total": 10}],
        "summary": {"currency": "EUR", "subtotal": 10, "tax_amount": 0, "shipping_amount": 0, "discount_amount": 0, "grand_total": 10},
    })
    assert updated.status_code == 200
    csv_response = client.get(f"/api/documents/{data['document_id']}/export/csv")
    assert csv_response.status_code == 200
    assert "Currency" in csv_response.text.splitlines()[0]
    assert "EUR" in csv_response.text

def test_list_documents(client):
    response = client.get("/api/documents")
    assert response.status_code == 200
    data = response.json()
    assert "stats" in data
    assert "documents" in data

def test_settings_api(client):
    response = client.get("/api/settings")
    assert response.status_code == 200
    data = response.json()
    assert "email" in data
    assert "gemini_api_key" not in data

import io
from fastapi.testclient import TestClient
from backend.main import app
from backend.database import init_db

init_db()
client = TestClient(app)

def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_dashboard_root_renders_html():
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "DocFlow" in response.text


def test_mock_inbound_email():
    response = client.post("/api/inbound/test-mock")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "document_id" in data
    detail_response = client.get(f"/documents/{data['document_id']}")
    assert detail_response.status_code == 200
    assert detail_response.headers["content-type"].startswith("text/html")

def test_list_documents():
    response = client.get("/api/documents")
    assert response.status_code == 200
    data = response.json()
    assert "stats" in data
    assert "documents" in data

def test_settings_api():
    response = client.get("/api/settings")
    assert response.status_code == 200
    data = response.json()
    assert "app_name" in data
    assert data["app_name"] == "DocFlow"

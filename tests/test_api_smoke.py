from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health_endpoint() -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "n8n-workflow-editor"


def test_capabilities_endpoint() -> None:
    r = client.get("/api/capabilities")
    assert r.status_code == 200
    body = r.json()
    assert "database" in body
    assert isinstance(body["database"], bool)


def test_workflows_requires_config() -> None:
    r = client.get("/api/workflows")
    assert r.status_code in (503, 502)

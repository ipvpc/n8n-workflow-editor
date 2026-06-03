from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    with (
        patch("app.database.init_db", new=AsyncMock()),
        patch("app.database.close_db", new=AsyncMock()),
    ):
        from app.main import app

        with TestClient(app) as test_client:
            yield test_client


def test_health_endpoint(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "n8n-workflow-editor"
    assert body["multi_instance"] is True


def test_capabilities_endpoint(client: TestClient) -> None:
    r = client.get("/api/capabilities")
    assert r.status_code == 200
    body = r.json()
    assert body["database"] is True
    assert body["multi_instance"] is True


def test_workflows_requires_config(client: TestClient) -> None:
    r = client.get("/api/workflows")
    assert r.status_code == 503

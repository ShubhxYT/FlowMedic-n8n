import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_n8n_status_and_workflows(client):
    assert client.get("/api/v1/n8n/status").json() == {"connected": True}
    response = client.get("/api/v1/workflows").json()
    assert response["next_cursor"] == "workflow-next"
    assert set(response["data"][0]) == {"id", "name", "active"}


def test_sync_deduplicates_and_diagnosis_persists(client):
    url = "/api/v1/incidents"
    assert client.get(url).json() == {"data": []}
    assert client.post(url + "/sync").json() == {
        "scanned": 1,
        "created": 1,
        "next_cursor": "next-page",
    }
    assert client.post(url + "/sync").json()["created"] == 0
    incident = client.get(url).json()["data"][0]
    assert incident["status"] == "open"
    result = client.post(url + "/" + incident["id"] + "/diagnose")
    assert result.status_code == 200
    assert result.json()["diagnosis_provider"] == "mock"
    assert result.json()["status"] == "diagnosed"
    assert result.json()["diagnosis"]["confidence"] == 0.55
    assert client.get(url + "/" + incident["id"]).json() == result.json()


def test_failed_endpoint_does_not_persist_or_leak(client):
    response = client.get("/api/v1/executions/failed")
    assert response.status_code == 200
    assert response.json()["data"][0]["failed_node"] == "Fetch orders"
    for forbidden in ("private-value", "private stack", "sensitive customer", "runData"):
        assert forbidden not in response.text
    assert client.get("/api/v1/incidents").json()["data"] == []


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/incidents/not-a-uuid",
        "/api/v1/incidents?limit=0",
        "/api/v1/incidents?offset=-1",
        "/api/v1/executions/failed?limit=101",
        "/api/v1/workflows?limit=secret-value",
    ],
)
def test_validation(client, path):
    response = client.get(path)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert "secret-value" not in response.text


def test_missing_incident(client):
    response = client.get("/api/v1/incidents/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_missing_configuration():
    with TestClient(create_app(Settings(_env_file=None, database_url="sqlite:///:memory:"))) as c:
        assert c.get("/health").status_code == 200
        response = c.get("/api/v1/n8n/status")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "n8n_not_configured"


def test_system_status_and_frontend_cors():
    settings = Settings(_env_file=None, database_url="sqlite:///:memory:")
    with TestClient(create_app(settings)) as c:
        response = c.get("/api/v1/system/status")
        assert response.json() == {
            "n8n_configured": False,
            "ai_mode": "mock",
            "database_engine": "sqlite",
        }
        preflight = c.options(
            "/api/v1/incidents",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_bearer_authentication():
    settings = Settings(
        _env_file=None, database_url="sqlite:///:memory:", flowmedic_api_key="local"
    )
    with TestClient(create_app(settings)) as c:
        assert c.get("/health").status_code == 200
        assert c.get("/api/v1/incidents").status_code == 401
        assert (
            c.get("/api/v1/incidents", headers={"Authorization": "Bearer local"}).status_code == 200
        )


def test_demo_mode_seeds_a_diagnosed_synthetic_incident():
    settings = Settings(_env_file=None, database_url="sqlite:///:memory:", demo_mode=True)
    with TestClient(create_app(settings)) as c:
        index = c.get("/").json()
        assert index == {"service": "FlowMedic AI", "docs": "/docs", "demo": "/api/v1/demo"}
        demo = c.get("/api/v1/demo")
        assert demo.status_code == 200
        assert demo.json()["diagnosis_provider"] == "mock"
        assert "Synthetic" in demo.json()["note"]
        incidents = c.get("/api/v1/incidents").json()["data"]
        assert len(incidents) == 1
        assert incidents[0]["id"] == demo.json()["incident_id"]
        assert incidents[0]["status"] == "diagnosed"
        assert incidents[0]["diagnosis"]["confidence"] == 0.55


def test_demo_endpoint_is_unavailable_when_disabled(client):
    response = client.get("/api/v1/demo")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "demo_not_enabled"


def test_upstream_failure_has_safe_error():
    settings = Settings(
        _env_file=None,
        database_url="sqlite:///:memory:",
        n8n_base_url="https://n8n.example",
        n8n_api_key="secret",
    )
    transport = httpx.MockTransport(lambda r: httpx.Response(401, text="secret stack trace"))
    with TestClient(create_app(settings, n8n_transport=transport)) as c:
        response = c.get("/api/v1/n8n/status")
        assert response.status_code == 502
        assert "secret" not in response.text


def test_unexpected_error_has_no_trace(client):
    def broken(*args, **kwargs):
        raise RuntimeError("private stack trace")

    client.app.state.session_factory = broken
    client.raise_server_exceptions = False
    # TestClient passes this option to its transport.
    client._transport.raise_server_exceptions = False
    response = client.get("/api/v1/incidents")
    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal_error", "message": "An internal error occurred"}
    }

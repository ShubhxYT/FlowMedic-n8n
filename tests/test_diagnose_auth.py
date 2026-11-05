import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.ratelimit import RateLimiter
from app.main import create_app


def n8n_handler(execution):
    def handler(request):
        if request.url.path.endswith("/executions"):
            summary = {k: v for k, v in execution.items() if k not in {"data", "workflowData"}}
            return httpx.Response(200, json={"data": [summary]})
        if request.url.path.endswith("/executions/42"):
            return httpx.Response(200, json=execution)
        return httpx.Response(404)

    return handler


def make_client(execution, **values):
    settings = Settings(
        _env_file=None,
        database_url="sqlite:///:memory:",
        n8n_base_url="https://n8n.example",
        n8n_api_key="test-n8n-key",
        **values,
    )
    app = create_app(settings, n8n_transport=httpx.MockTransport(n8n_handler(execution)))
    return TestClient(app)


def seed_incident(client, headers=None):
    assert client.post("/api/v1/incidents/sync", headers=headers).status_code == 200
    return client.get("/api/v1/incidents", headers=headers).json()["data"][0]["id"]


@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/api/v1/incidents/sync"),
        ("post", "/api/v1/monitoring/sync"),
        ("post", "/api/v1/incidents/00000000-0000-0000-0000-000000000000/diagnose"),
        ("patch", "/api/v1/alerts/rules/00000000-0000-0000-0000-000000000000"),
    ],
)
def test_mutating_routes_fail_closed_without_api_key(execution, method, path):
    with make_client(execution, allow_unauthenticated_mutations=False) as client:
        response = getattr(client, method)(path)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "auth_not_configured"
        assert "FLOWMEDIC_API_KEY" in response.json()["error"]["message"]
        assert client.get("/health").status_code == 200
        assert client.get("/api/v1/incidents").status_code == 200


def test_unauthenticated_diagnose_is_rejected_when_key_configured(execution):
    with make_client(execution, flowmedic_api_key="local") as client:
        auth = {"Authorization": "Bearer local"}
        incident_id = seed_incident(client, auth)
        path = f"/api/v1/incidents/{incident_id}/diagnose"
        assert client.post(path).status_code == 401
        assert client.post(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert client.post(path, headers=auth).status_code == 200


def test_rediagnosis_requires_explicit_force(execution):
    with make_client(execution) as client:
        incident_id = seed_incident(client)
        path = f"/api/v1/incidents/{incident_id}/diagnose"
        assert client.post(path).status_code == 200
        repeat = client.post(path)
        assert repeat.status_code == 409
        assert repeat.json()["error"]["code"] == "already_diagnosed"
        assert client.post(path + "?force=true").status_code == 200


def test_diagnose_is_rate_limited_per_caller(execution):
    with make_client(execution, diagnose_rate_limit_per_minute=2) as client:
        incident_id = seed_incident(client)
        path = f"/api/v1/incidents/{incident_id}/diagnose?force=true"
        assert client.post(path).status_code == 200
        assert client.post(path).status_code == 200
        limited = client.post(path)
        assert limited.status_code == 429
        assert limited.json()["error"]["code"] == "rate_limited"


def test_rate_limiter_window_expires_and_isolates_keys():
    now = [0.0]
    limiter = RateLimiter(1, 60, clock=lambda: now[0])
    assert limiter.allow("a")
    assert not limiter.allow("a")
    assert limiter.allow("b")
    now[0] = 61
    assert limiter.allow("a")

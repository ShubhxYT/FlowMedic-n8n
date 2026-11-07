from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.ratelimit import RateLimiter
from app.main import create_app
from app.models.connections import N8nInstance
from app.models.incident import Incident

PASSWORD = "test-password-123456"
ORIGIN = "http://localhost:3000"
INSTANCE = "33333333-3333-3333-3333-333333333333"
INCIDENT = "44444444-4444-4444-4444-444444444444"


def make_client(**values):
    settings = Settings(
        _env_file=None,
        database_url="sqlite:///:memory:",
        auth_registration_enabled=True,
        auth_allow_insecure_cookies=True,
        auth_trusted_origins=ORIGIN,
        **values,
    )
    return TestClient(create_app(settings))


def csrf(client):
    client.get("/api/v1/auth/csrf")
    client.headers.update(
        {"Origin": ORIGIN, "X-CSRF-Token": client.cookies["fm_csrf"]}
    )


def admin(client):
    csrf(client)
    result = client.post(
        "/api/v1/auth/signup",
        json={"email": "owner@example.com", "password": PASSWORD},
    )
    assert result.status_code == 201, result.text
    client.headers["X-CSRF-Token"] = client.cookies["fm_csrf"]
    return client.get("/api/v1/auth/me").json()["workspace"]["id"]


def seed_incident(client, workspace_id):
    now = datetime.now(UTC)
    with client.app.state.session_factory() as session:
        session.add(
            N8nInstance(
                id=INSTANCE,
                workspace_id=workspace_id,
                name="Test instance",
                base_url="https://legacy.invalid",
                enabled=False,
                deleted=False,
                revision=1,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            Incident(
                id=INCIDENT,
                workspace_id=workspace_id,
                instance_id=INSTANCE,
                execution_id="execution",
                workflow_id="workflow",
                workflow_name="Orders",
                error_type="NodeApiError",
                error_message="Simulated failure",
                status="open",
                severity="medium",
            )
        )
        session.commit()


@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/api/v1/incidents/sync"),
        ("post", "/api/v1/monitoring/sync"),
        ("post", f"/api/v1/incidents/{INCIDENT}/diagnose"),
        ("patch", "/api/v1/alerts/rules/44444444-4444-4444-4444-444444444444"),
    ],
)
def test_anonymous_mutations_are_rejected_by_session_auth(method, path):
    with make_client() as client:
        response = getattr(client, method)(path)
        assert response.status_code in {401, 403}
        assert response.json()["error"]["code"] in {"unauthorized", "forbidden"}
        assert client.get("/health").status_code == 200
        assert client.get("/api/v1/incidents").status_code == 401


def test_rediagnosis_requires_explicit_force():
    with make_client() as client:
        workspace = admin(client)
        seed_incident(client, workspace)
        path = f"/api/v1/incidents/{INCIDENT}/diagnose"
        assert client.post(path).status_code == 200
        repeat = client.post(path)
        assert repeat.status_code == 409
        assert repeat.json()["error"]["code"] == "already_diagnosed"
        assert client.post(path + "?force=true").status_code == 200


def test_diagnose_is_rate_limited_per_caller():
    with make_client(diagnose_rate_limit_per_minute=2) as client:
        workspace = admin(client)
        seed_incident(client, workspace)
        path = f"/api/v1/incidents/{INCIDENT}/diagnose?force=true"
        assert client.post(path).status_code == 200
        assert client.post(path).status_code == 200
        limited = client.post(path)
        assert limited.status_code == 429
        assert limited.json()["error"]["code"] == "rate_limited"


def test_viewer_cannot_diagnose():
    with make_client() as client:
        workspace = admin(client)
        seed_incident(client, workspace)
        invite = client.post("/api/v1/auth/invites", json={"role": "viewer"}).json()
        token = invite["url"].split("token=", 1)[1]
        client.cookies.clear()
        csrf(client)
        client.post(
            "/api/v1/auth/signup",
            json={"email": "viewer@example.com", "password": PASSWORD},
        )
        client.headers["X-CSRF-Token"] = client.cookies["fm_csrf"]
        client.post("/api/v1/auth/invites/accept", json={"token": token})
        client.headers["X-CSRF-Token"] = client.cookies["fm_csrf"]
        response = client.post(f"/api/v1/incidents/{INCIDENT}/diagnose")
        assert response.status_code == 403


def test_rate_limiter_window_expires_and_isolates_keys():
    now = [0.0]
    limiter = RateLimiter(1, 60, clock=lambda: now[0])
    assert limiter.allow("a")
    assert not limiter.allow("a")
    assert limiter.allow("b")
    now[0] = 61
    assert limiter.allow("a")

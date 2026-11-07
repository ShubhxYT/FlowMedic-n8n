import json
import socket
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.models.connections import N8nInstance

PASSWORD = "test-password-123456"
ORIGIN = "http://localhost:3000"


def public_dns(host, port, *args, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def settings_for(keyring: str):
    return Settings(
        _env_file=None,
        database_url="sqlite:///:memory:",
        auth_registration_enabled=True,
        auth_allow_insecure_cookies=True,
        auth_trusted_origins=ORIGIN,
        connection_encryption_keys=keyring,
        connection_active_key_id="one",
        deployment_environment="production",
        outbound_egress_enforced=True,
        outbound_destinations=json.dumps(
            [
                {"origin": "https://n8n.example.test"},
                {"origin": "https://n8n2.example.test"},
                {"origin": "https://api.example.test"},
            ]
        ),
    )


@pytest.fixture
def harness(monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setattr(socket, "getaddrinfo", public_dns)
    calls = []

    def handler(request):
        calls.append((request.method, str(request.url)))
        path = request.url.path
        if path.endswith("/api/v1/workflows"):
            if request.headers.get("x-n8n-api-key") == "bad-key":
                return httpx.Response(401, json={"message": "denied"})
            return httpx.Response(200, json={"data": [], "nextCursor": None})
        if "/models/" in path:
            model = path.rsplit("/", 1)[-1]
            if model == "missing-model":
                return httpx.Response(404, json={"error": "missing"})
            return httpx.Response(200, json={"id": model})
        if path.endswith("/chat/completions"):
            return httpx.Response(
                200, json={"choices": [{"message": {"content": '{"ok": true}'}}]}
            )
        return httpx.Response(404, json={"error": "not found"})

    transport = httpx.MockTransport(handler)
    keyring = json.dumps({"one": Fernet.generate_key().decode()})
    app = create_app(settings_for(keyring), outbound_transport=transport)
    with TestClient(app) as client:
        yield client, calls


def csrf(client):
    client.get("/api/v1/auth/csrf")
    client.headers.update(
        {"Origin": ORIGIN, "X-CSRF-Token": client.cookies["fm_csrf"]}
    )


def signup(client, email="owner@example.com"):
    csrf(client)
    result = client.post(
        "/api/v1/auth/signup", json={"email": email, "password": PASSWORD}
    )
    assert result.status_code == 201, result.text
    client.headers["X-CSRF-Token"] = client.cookies["fm_csrf"]
    return client.get("/api/v1/auth/me").json()


def login(client, email="owner@example.com"):
    client.cookies.clear()
    csrf(client)
    result = client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert result.status_code == 200, result.text
    client.headers["X-CSRF-Token"] = client.cookies["fm_csrf"]


def create_n8n(client, name="Primary", url="https://n8n.example.test", key="n8n-secret-fixture"):
    return client.post(
        "/api/v1/settings/n8n-instances",
        json={"name": name, "base_url": url, "api_key": key},
    )


def create_ai(client, name="Provider", key="ai-secret-fixture"):
    return client.post(
        "/api/v1/settings/ai-providers",
        json={
            "name": name,
            "base_url": "https://api.example.test",
            "model": "probe-model",
            "kind": "openai-compatible",
            "api_key": key,
        },
    )


def test_crud_secret_semantics_and_revisions(harness):
    client, _ = harness
    signup(client)
    created = create_n8n(client)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["revision"] == 1 and body["enabled"] is False
    assert body["has_api_key"] is True and body["kind"] == "n8n"
    assert "n8n-secret-fixture" not in created.text

    with client.app.state.session_factory() as session:
        row = session.query(N8nInstance).one()
        assert row.api_key_ciphertext and "n8n-secret-fixture" not in row.api_key_ciphertext

    listed = client.get("/api/v1/settings/n8n-instances").json()
    assert listed["items"][0]["name"] == "Primary"
    assert listed["items"][0]["has_api_key"] is True
    assert "n8n-secret-fixture" not in json.dumps(listed)
    connection_id = body["id"]

    kept = client.patch(
        f"/api/v1/settings/n8n-instances/{connection_id}",
        json={"expected_revision": 1, "name": "Renamed"},
    )
    assert kept.status_code == 200 and kept.json()["has_api_key"] is True
    assert kept.json()["revision"] == 2

    stale = client.patch(
        f"/api/v1/settings/n8n-instances/{connection_id}",
        json={"expected_revision": 1, "name": "Stale"},
    )
    assert stale.status_code == 409

    unconfirmed = client.patch(
        f"/api/v1/settings/n8n-instances/{connection_id}",
        json={"expected_revision": 2, "base_url": "https://n8n2.example.test"},
    )
    assert unconfirmed.status_code == 422
    confirmed = client.patch(
        f"/api/v1/settings/n8n-instances/{connection_id}",
        json={
            "expected_revision": 2,
            "base_url": "https://n8n2.example.test",
            "confirm_same_server": True,
        },
    )
    assert confirmed.status_code == 200 and confirmed.json()["revision"] == 3

    enabled = client.patch(
        f"/api/v1/settings/n8n-instances/{connection_id}",
        json={"expected_revision": 3, "enabled": True},
    )
    assert enabled.status_code == 200 and enabled.json()["enabled"] is True
    revision = enabled.json()["revision"]

    removed = client.patch(
        f"/api/v1/settings/n8n-instances/{connection_id}",
        json={"expected_revision": revision, "remove_api_key": True},
    )
    assert removed.status_code == 200
    assert removed.json()["has_api_key"] is False
    assert removed.json()["enabled"] is False
    revision = removed.json()["revision"]

    masked = client.patch(
        f"/api/v1/settings/n8n-instances/{connection_id}",
        json={"expected_revision": revision, "api_key": "********"},
    )
    assert masked.status_code == 422
    explicit_null = client.patch(
        f"/api/v1/settings/n8n-instances/{connection_id}",
        json={"expected_revision": revision, "api_key": None},
    )
    assert explicit_null.status_code == 422
    both = client.patch(
        f"/api/v1/settings/n8n-instances/{connection_id}",
        json={"expected_revision": revision, "api_key": "new-key", "remove_api_key": True},
    )
    assert both.status_code == 422

    assert create_n8n(client, name="Primary").status_code == 201
    enabled_create = client.post(
        "/api/v1/settings/n8n-instances",
        json={
            "name": "Eager",
            "base_url": "https://n8n.example.test",
            "api_key": "k",
            "enabled": True,
        },
    )
    assert enabled_create.status_code == 422
    assert enabled_create.json()["error"]["code"] == "save_disabled_first"

    assert (
        client.delete(f"/api/v1/settings/n8n-instances/{connection_id}").status_code
        == 428
    )
    assert (
        client.delete(
            f"/api/v1/settings/n8n-instances/{connection_id}",
            headers={"If-Match": "nope"},
        ).status_code
        == 422
    )
    deleted = client.delete(
        f"/api/v1/settings/n8n-instances/{connection_id}",
        headers={"If-Match": f'"{revision}"'},
    )
    assert deleted.status_code == 200 and deleted.json() == {"deleted": True}
    names = [item["name"] for item in client.get("/api/v1/settings/n8n-instances").json()["items"]]
    assert "Renamed" not in names
    assert (
        client.patch(
            f"/api/v1/settings/n8n-instances/{connection_id}",
            json={"expected_revision": revision, "name": "gone"},
        ).status_code
        == 404
    )


def test_authorization_matrix_and_workspace_isolation(harness):
    client, calls = harness
    signup(client)
    owner_connection = create_n8n(client).json()

    client.cookies.clear()
    assert client.get("/api/v1/settings/n8n-instances").status_code == 401

    login(client)
    bad_csrf = client.patch(
        f"/api/v1/settings/n8n-instances/{owner_connection['id']}",
        json={"expected_revision": 1, "name": "x"},
        headers={"X-CSRF-Token": "wrong"},
    )
    assert bad_csrf.status_code == 403

    # Another workspace admin cannot read or touch the owner's connection.
    signup(client, "other@example.com")
    calls.clear()
    assert client.get("/api/v1/settings/n8n-instances").json()["items"] == []
    foreign = client.patch(
        f"/api/v1/settings/n8n-instances/{owner_connection['id']}",
        json={"expected_revision": 1, "name": "hijack"},
    )
    assert foreign.status_code == 404
    foreign_test = client.post(
        f"/api/v1/settings/n8n-instances/{owner_connection['id']}/test",
        json={"expected_revision": 1, "changes": {}},
    )
    assert foreign_test.status_code == 404
    assert calls == []

    # A viewer can read safe metadata but every mutation is rejected.
    login(client)
    invite = client.post("/api/v1/auth/invites", json={"role": "viewer"}).json()
    token = urlsplit(invite["url"]).fragment.removeprefix("token=")
    signup(client, "viewer@example.com")
    accepted = client.post("/api/v1/auth/invites/accept", json={"token": token})
    assert accepted.status_code == 200
    client.headers["X-CSRF-Token"] = client.cookies["fm_csrf"]
    listed = client.get("/api/v1/settings/n8n-instances")
    assert listed.status_code == 200
    assert "n8n-secret-fixture" not in listed.text
    assert create_n8n(client).status_code == 403
    assert (
        client.patch(
            f"/api/v1/settings/n8n-instances/{owner_connection['id']}",
            json={"expected_revision": 1, "name": "viewer"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/settings/n8n-instances/test",
            json={
                "candidate": {
                    "name": "probe",
                    "base_url": "https://n8n.example.test",
                    "api_key": "k",
                }
            },
        ).status_code
        == 403
    )


def test_connection_tests_never_persist_and_are_rate_limited(harness):
    client, calls = harness
    signup(client)
    connection = create_n8n(client).json()

    candidate = client.post(
        "/api/v1/settings/n8n-instances/test",
        json={
            "candidate": {
                "name": "probe",
                "base_url": "https://n8n.example.test",
                "api_key": "n8n-secret-fixture",
            }
        },
    )
    assert candidate.status_code == 200, candidate.text
    assert candidate.json()["capability"] == "workflows_read"
    assert candidate.json()["tested_revision"] is None

    saved = client.post(
        f"/api/v1/settings/n8n-instances/{connection['id']}/test",
        json={"expected_revision": 1, "changes": {}},
    )
    assert saved.status_code == 200 and saved.json()["tested_revision"] == 1

    bad = client.post(
        "/api/v1/settings/n8n-instances/test",
        json={
            "candidate": {
                "name": "probe",
                "base_url": "https://n8n.example.test",
                "api_key": "bad-key",
            }
        },
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "connection_auth_failed"

    listed = client.get("/api/v1/settings/n8n-instances").json()
    assert len(listed["items"]) == 1
    assert listed["items"][0]["revision"] == 1
    assert listed["items"][0]["has_api_key"] is True

    statuses = []
    for _ in range(4):
        statuses.append(
            client.post(
                "/api/v1/settings/n8n-instances/test",
                json={
                    "candidate": {
                        "name": "probe",
                        "base_url": "https://n8n.example.test",
                        "api_key": "n8n-secret-fixture",
                    }
                },
            ).status_code
        )
    assert 429 in statuses, statuses
    assert statuses.count(200) == 2
    assert client.get("/api/v1/settings/n8n-instances").json()["items"][0]["revision"] == 1


def test_ai_default_mock_and_structured_output(harness):
    client, calls = harness
    signup(client)
    created = create_ai(client)
    assert created.status_code == 201, created.text
    provider = created.json()
    assert provider["kind"] == "openai-compatible" and provider["is_default"] is False

    enabled = client.patch(
        f"/api/v1/settings/ai-providers/{provider['id']}",
        json={"expected_revision": 1, "enabled": True},
    )
    assert enabled.status_code == 200
    state_revision = client.get("/api/v1/settings/ai-providers").json()["state_revision"]

    default = client.patch(
        f"/api/v1/settings/ai-providers/{provider['id']}",
        json={
            "expected_revision": 2,
            "make_default": True,
            "expected_state_revision": state_revision,
        },
    )
    assert default.status_code == 200, default.text
    page = client.get("/api/v1/settings/ai-providers").json()
    assert page["items"][0]["is_default"] is True
    assert page["state_revision"] == state_revision + 1

    stale_default = client.patch(
        f"/api/v1/settings/ai-providers/{provider['id']}",
        json={
            "expected_revision": 2,
            "make_default": True,
            "expected_state_revision": state_revision,
        },
    )
    assert stale_default.status_code == 409

    no_ack = client.post(
        "/api/v1/settings/ai-providers/test",
        json={
            "candidate": {
                "name": "probe",
                "base_url": "https://api.example.test",
                "model": "probe-model",
                "kind": "openai-compatible",
                "api_key": "ai-secret-fixture",
            },
            "mode": "structured_output",
        },
    )
    assert no_ack.status_code == 422
    assert no_ack.json()["error"]["code"] == "structured_output_ack_required"

    ack = client.post(
        "/api/v1/settings/ai-providers/test",
        json={
            "candidate": {
                "name": "probe",
                "base_url": "https://api.example.test",
                "model": "probe-model",
                "kind": "openai-compatible",
                "api_key": "ai-secret-fixture",
            },
            "mode": "structured_output",
            "acknowledge_usage_cost": True,
        },
    )
    assert ack.status_code == 200, ack.text
    assert ack.json()["capability"] == "structured_output"

    switched = client.patch(
        f"/api/v1/settings/ai-providers/{provider['id']}",
        json={"expected_revision": 3, "kind": "mock"},
    )
    assert switched.status_code == 200, switched.text
    assert switched.json()["kind"] == "mock"
    assert switched.json()["base_url"] is None
    assert switched.json()["model"] is None
    assert switched.json()["has_api_key"] is False
    calls.clear()
    mock = client.post(
        "/api/v1/settings/ai-providers/test",
        json={
            "candidate": {"name": "mock", "kind": "mock"},
        },
    )
    assert mock.status_code == 200
    assert mock.json()["capability"] == "mock"
    assert calls == []


def test_oversized_body_rejected_before_validation(harness):
    client, _ = harness
    signup(client)
    response = client.post(
        "/api/v1/settings/n8n-instances",
        content=json.dumps({"name": "x" * 40000}),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"

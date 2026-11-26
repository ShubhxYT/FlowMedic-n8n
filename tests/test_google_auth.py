import base64
import json
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from auth_support import ORIGIN, csrf
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import Settings
from app.main import create_app
from app.models.auth import User

CLIENT_ID = "client-123.apps.googleusercontent.com"


def jwt(claims):
    part = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"e30.{part}.sig"


class Google:
    def __init__(self):
        self.claims = {}
        self.requests = []
        self.status = 200

    def handler(self, request):
        self.requests.append(parse_qs(request.content.decode()))
        return httpx.Response(self.status, json={"id_token": jwt(self.claims)})


def build(google, **extra):
    settings = Settings(
        _env_file=None,
        database_url="sqlite:///:memory:",
        auth_allow_insecure_cookies=True,
        auth_trusted_origins=ORIGIN,
        **extra,
    )
    return TestClient(create_app(settings, google_transport=httpx.MockTransport(google.handler)))


@pytest.fixture
def google():
    return Google()


def enabled(**extra):
    return {
        "google_client_id": CLIENT_ID,
        "google_client_secret": "secret-value",
        "auth_registration_enabled": True,
        **extra,
    }


def begin(client):
    csrf(client)
    result = client.post("/api/v1/auth/google/start")
    assert result.status_code == 200, result.text
    url = urlsplit(result.json()["url"])
    query = {key: value[0] for key, value in parse_qs(url.query).items()}
    assert url.netloc == "accounts.google.com"
    return query


def finish(client, google, query, **claims):
    google.claims = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "exp": int(time.time()) + 300,
        "nonce": query["nonce"],
        "email": "Person@Gmail.com",
        "email_verified": True,
        **claims,
    }
    csrf(client)
    return client.post(
        "/api/v1/auth/google/callback", json={"code": "auth-code", "state": query["state"]}
    )


def test_disabled_without_credentials(google):
    with build(google) as client:
        assert client.get("/api/v1/auth/csrf").json()["google_enabled"] is False
        csrf(client)
        assert client.post("/api/v1/auth/google/start").status_code == 404


def test_start_requests_only_openid_scopes_with_pkce(google):
    with build(google, **enabled()) as client:
        assert client.get("/api/v1/auth/csrf").json()["google_enabled"] is True
        query = begin(client)
        assert query["scope"] == "openid email profile"
        assert query["client_id"] == CLIENT_ID
        assert query["redirect_uri"] == f"{ORIGIN}/auth/google/callback"
        assert query["code_challenge_method"] == "S256"
        assert "secret-value" not in json.dumps(query)
        assert client.cookies["fm_oauth"].startswith(query["state"] + ".")


def test_callback_creates_account_and_session(google):
    with build(google, **enabled()) as client:
        query = begin(client)
        result = finish(client, google, query)
        assert result.status_code == 200, result.text
        sent = google.requests[0]
        assert sent["code"] == ["auth-code"] and sent["client_secret"] == ["secret-value"]
        assert sent["redirect_uri"] == [f"{ORIGIN}/auth/google/callback"]
        verifier = client.cookies.get("fm_oauth")
        assert verifier is None
        client.headers["X-CSRF-Token"] = client.cookies["fm_csrf"]
        me = client.get("/api/v1/auth/me").json()
        assert me["user"]["email"] == "person@gmail.com"
        assert me["workspace"]["role"] == "admin"


def test_callback_links_existing_email_account(google):
    with build(google, **enabled()) as client:
        csrf(client)
        password = "long-password-1234"
        signup = client.post(
            "/api/v1/auth/signup", json={"email": "person@gmail.com", "password": password}
        )
        assert signup.status_code == 201
        client.cookies.clear()
        result = finish(client, google, begin(client))
        assert result.status_code == 200, result.text
        with client.app.state.session_factory() as db:
            assert len(db.scalars(select(User)).all()) == 1


@pytest.mark.parametrize(
    "claims",
    [
        {"aud": "someone-else"},
        {"iss": "https://evil.example"},
        {"exp": 1},
        {"nonce": "wrong"},
        {"email_verified": False},
    ],
)
def test_callback_rejects_invalid_id_tokens(google, claims):
    with build(google, **enabled()) as client:
        result = finish(client, google, begin(client), **claims)
        assert result.status_code == 401
        with client.app.state.session_factory() as db:
            assert db.scalar(select(User)) is None


def test_callback_rejects_state_mismatch_and_missing_cookie(google):
    with build(google, **enabled()) as client:
        query = begin(client)
        csrf(client)
        bad = client.post("/api/v1/auth/google/callback", json={"code": "x", "state": "z" * 43})
        assert bad.status_code == 400
        # The one-time cookie was cleared, so the real state cannot be replayed either.
        assert finish(client, google, query).status_code == 400
        assert google.requests == []


def test_new_google_accounts_respect_closed_registration(google):
    with build(google, **enabled(auth_registration_enabled=False)) as client:
        assert finish(client, google, begin(client)).status_code == 403


def test_token_endpoint_failure_is_generic(google):
    google.status = 400
    with build(google, **enabled()) as client:
        result = finish(client, google, begin(client))
        assert result.status_code == 401
        assert "secret-value" not in result.text

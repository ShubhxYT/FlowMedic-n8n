import time
from urllib.parse import urlsplit

import pytest
from auth_support import ORIGIN, PASSWORD, csrf, login
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.commands.account import operate
from app.core.config import Settings
from app.main import create_app
from app.models.auth import AuthSession, User, WorkspaceInvite, WorkspaceMembership
from app.repositories.auth import digest
from app.services.auth import passwords


@pytest.fixture
def anonymous():
    settings = Settings(
        _env_file=None,
        database_url="sqlite:///:memory:",
        auth_registration_enabled=True,
        auth_allow_insecure_cookies=True,
        auth_trusted_origins=ORIGIN,
    )
    with TestClient(create_app(settings)) as client:
        yield client


def signup(client, email="person@example.com"):
    csrf(client)
    result = client.post("/api/v1/auth/signup", json={"email": email, "password": PASSWORD})
    assert result.status_code == 201, result.text
    client.headers["X-CSRF-Token"] = client.cookies["fm_csrf"]
    return client.get("/api/v1/auth/me").json()


def invite_token(client, role="viewer"):
    result = client.post("/api/v1/auth/invites", json={"role": role})
    assert result.status_code == 201, result.text
    return urlsplit(result.json()["url"]).fragment.removeprefix("token=")


def test_signup_hash_storage_and_generic_login(anonymous, caplog):
    c = anonymous
    assert c.get("/api/v1/auth/me").status_code == 401
    me = signup(c, "Person@Example.com")
    assert me["user"]["email"] == "person@example.com"
    token = c.cookies["fm_session"]
    with c.app.state.session_factory() as db:
        user = db.scalar(select(User))
        row = db.scalar(select(AuthSession))
        assert user.password_hash != PASSWORD and passwords.verify(PASSWORD, user.password_hash)
        assert row.token_hash == digest(token) and row.token_hash != token
        assert row.csrf_hash == digest(c.cookies["fm_csrf"])
    assert PASSWORD not in str(me) and token not in str(me) and PASSWORD not in caplog.text
    csrf(c)
    duplicate = c.post(
        "/api/v1/auth/signup", json={"email": "PERSON@example.com", "password": PASSWORD}
    )
    assert duplicate.status_code == 409
    c.cookies.clear()
    failures = []
    for email in ["person@example.com", "missing@example.com"]:
        csrf(c)
        failures.append(
            c.post("/api/v1/auth/login", json={"email": email, "password": "wrong-password-123"})
        )
    assert failures[0].status_code == failures[1].status_code == 401
    assert failures[0].json() == failures[1].json()
    login(c, "person@example.com")
    assert c.cookies["fm_session"] != token


@pytest.mark.parametrize("operation", ["expiry", "revoke", "disable", "reset", "membership"])
def test_revocation_takes_effect_on_next_request(anonymous, operation):
    c = anonymous
    me = signup(c)
    factory = c.app.state.session_factory
    if operation in {"revoke", "disable", "reset"}:
        operate(factory, "person@example.com", operation, "replacement-password-123")
    else:
        with factory() as db:
            if operation == "expiry":
                db.scalar(select(AuthSession)).expires_at = int(time.time()) - 1
            else:
                db.delete(db.get(WorkspaceMembership, (me["user"]["id"], me["workspace"]["id"])))
            db.commit()
    assert c.get("/api/v1/auth/me").status_code == 401


def test_logout_and_csrf(anonymous):
    c = anonymous
    me = signup(c)
    original = c.cookies["fm_session"]
    assert c.post("/api/v1/auth/logout", headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert (
        c.post("/api/v1/auth/logout", headers={"Origin": "https://attacker.example"}).status_code
        == 403
    )
    assert (
        c.post(
            "/api/v1/auth/workspace", json={"workspace_id": "00000000-0000-0000-0000-000000000001"}
        ).status_code
        == 404
    )
    assert c.post("/api/v1/auth/logout").status_code == 200
    assert c.get("/api/v1/auth/me").status_code == 401
    c.cookies.set("fm_session", original)
    assert c.get("/api/v1/auth/me").status_code == 401
    assert me["workspace"]["role"] == "admin"


def test_invites_expire_are_single_use_and_preserve_role_changes(anonymous):
    c = anonymous
    owner = signup(c)
    token = invite_token(c)
    with c.app.state.session_factory() as db:
        row = db.scalar(select(WorkspaceInvite))
        assert row.token_hash == digest(token)
    c.cookies.clear()
    visitor = signup(c, "visitor@example.com")
    assert c.post("/api/v1/auth/invites/accept", json={"token": token}).status_code == 200
    c.headers["X-CSRF-Token"] = c.cookies["fm_csrf"]
    assert c.get("/api/v1/auth/me").json()["workspace"]["role"] == "viewer"
    assert c.post("/api/v1/auth/invites/accept", json={"token": token}).status_code == 404
    # Role is read from membership every request, not copied into session authority.
    with c.app.state.session_factory() as db:
        membership = db.get(WorkspaceMembership, (visitor["user"]["id"], owner["workspace"]["id"]))
        membership.role = "admin"
        db.commit()
    assert c.get("/api/v1/auth/me").json()["workspace"]["role"] == "admin"
    expired = invite_token(c)
    with c.app.state.session_factory() as db:
        row = db.scalar(
            select(WorkspaceInvite).where(WorkspaceInvite.token_hash == digest(expired))
        )
        row.expires_at = int(time.time()) - 1
        db.commit()
    assert c.post("/api/v1/auth/invites/accept", json={"token": expired}).status_code == 404


def test_registration_toggle_throttling_and_secure_defaults(anonymous):
    c = anonymous
    c.app.state.settings.auth_registration_enabled = False
    csrf(c)
    assert (
        c.post(
            "/api/v1/auth/signup", json={"email": "x@example.com", "password": PASSWORD}
        ).status_code
        == 403
    )
    for _ in range(10):
        result = c.post(
            "/api/v1/auth/login", json={"email": "target@example.com", "password": PASSWORD}
        )
        assert result.status_code == 401
    assert (
        c.post(
            "/api/v1/auth/login", json={"email": "target@example.com", "password": PASSWORD}
        ).status_code
        == 429
    )
    assert Settings(_env_file=None).auth_allow_insecure_cookies is False
    assert c.get("/docs").status_code == 404
    c.app.state.settings.auth_allow_insecure_cookies = False
    response = c.get("/api/v1/auth/csrf")
    assert (
        "Secure" in response.headers["set-cookie"]
        and "Domain=" not in response.headers["set-cookie"]
    )

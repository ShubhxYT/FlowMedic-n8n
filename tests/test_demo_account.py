import pytest
from auth_support import ORIGIN, csrf
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.commands.demo_account import ensure_demo_account
from app.core.config import Settings
from app.main import create_app
from app.models.auth import User, WorkspaceMembership
from app.services.auth import passwords


def make_settings(**extra):
    return Settings(
        _env_file=None,
        database_url="sqlite:///:memory:",
        auth_allow_insecure_cookies=True,
        auth_trusted_origins=ORIGIN,
        **extra,
    )


def test_startup_provisions_short_password_demo_login():
    settings = make_settings(demo_account_email="Test@Gmail.com", demo_account_password="test")
    with TestClient(create_app(settings)) as client:
        with client.app.state.session_factory() as db:
            user = db.scalar(select(User))
            assert user.email == "test@gmail.com"
            assert user.password_hash != "test" and passwords.verify("test", user.password_hash)
            membership = db.scalar(select(WorkspaceMembership))
            assert membership.user_id == user.id and membership.role == "admin"
        csrf(client)
        bad = client.post("/api/v1/auth/login", json={"email": "test@gmail.com", "password": "x"})
        assert bad.status_code == 401
        csrf(client)
        ok = client.post("/api/v1/auth/login", json={"email": "test@gmail.com", "password": "test"})
        assert ok.status_code == 200, ok.text
        assert client.get("/api/v1/auth/me").json()["user"]["email"] == "test@gmail.com"


def test_demo_account_is_idempotent_and_never_overwritten():
    settings = make_settings(demo_account_email="test@gmail.com", demo_account_password="test")
    with TestClient(create_app(settings)) as client:
        factory = client.app.state.session_factory
        user_id, created = ensure_demo_account(factory, "test@gmail.com", "other")
        assert created is False
        with factory() as db:
            assert len(db.scalars(select(User)).all()) == 1
            assert passwords.verify("test", db.get(User, user_id).password_hash)


def test_signup_policy_still_requires_twelve_characters():
    settings = make_settings(auth_registration_enabled=True)
    with TestClient(create_app(settings)) as client:
        csrf(client)
        short = client.post("/api/v1/auth/signup", json={"email": "a@b.co", "password": "test"})
        assert short.status_code == 422


def test_demo_account_settings_must_be_paired():
    with pytest.raises(ValueError):
        make_settings(demo_account_email="test@gmail.com")

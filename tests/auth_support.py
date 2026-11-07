from fastapi.testclient import TestClient

from app.commands.bootstrap_owner import bootstrap
from app.models.auth import LEGACY_WORKSPACE_ID

PASSWORD = "test-password-123456"
ORIGIN = "http://localhost:3000"


def legacy_factory(factory):
    from app.core.workspaces import WorkspaceFactory, ensure_reserved_workspaces

    ensure_reserved_workspaces(factory)
    return WorkspaceFactory(factory, LEGACY_WORKSPACE_ID)


def csrf(client):
    client.get("/api/v1/auth/csrf")
    client.headers.update({"Origin": ORIGIN, "X-CSRF-Token": client.cookies["fm_csrf"]})


def login(client, email="owner@example.com", password=PASSWORD):
    csrf(client)
    result = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert result.status_code == 200, result.text
    client.headers["X-CSRF-Token"] = client.cookies["fm_csrf"]


class OperatorClient(TestClient):
    """Existing pipeline tests explicitly run as the bootstrapped operator."""

    def __init__(self, app, **kwargs):
        app.state.settings.auth_allow_insecure_cookies = True
        app.state.settings.auth_trusted_origins = ORIGIN
        key = app.state.settings.flowmedic_api_key.get_secret_value()
        if key:
            kwargs["headers"] = {"Authorization": f"Bearer {key}"}
        super().__init__(app, **kwargs)

    def __enter__(self):
        super().__enter__()
        if not self.app.state.settings.demo_mode:
            bootstrap(self.app.state.session_factory, "owner@example.com", PASSWORD)
            login(self)
        else:
            # Private demo tests use the synthetic-only public policy.
            self.app.state.settings.public_demo_readonly = True
        return self

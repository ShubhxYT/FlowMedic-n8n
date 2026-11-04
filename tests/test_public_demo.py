import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.main import create_app


def test_public_demo_must_be_synthetic():
    with pytest.raises(ValidationError, match="PUBLIC_DEMO_READONLY"):
        Settings(_env_file=None, public_demo_readonly=True)


def test_public_demo_allows_browsing_but_blocks_writes():
    settings = Settings(
        _env_file=None,
        database_url="sqlite:///:memory:",
        demo_mode=True,
        public_demo_readonly=True,
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/v1/incidents").status_code == 200
        assert client.get("/api/v1/metrics/overview").status_code == 200
        result = client.post("/api/v1/monitoring/sync")
        assert result.status_code == 403
        assert result.json()["error"]["code"] == "demo_readonly"
        assert client.post("/api/v1/incidents/sync").status_code == 403

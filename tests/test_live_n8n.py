"""Opt-in local validation against a developer-owned n8n instance; never selected by CI."""

import os

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

pytestmark = pytest.mark.integration

if not (
    os.getenv("RUN_LIVE_N8N_VALIDATION") == "true"
    and os.getenv("N8N_BASE_URL")
    and os.getenv("N8N_API_KEY")
):
    pytest.skip(
        (
            "set RUN_LIVE_N8N_VALIDATION=true, N8N_BASE_URL, and N8N_API_KEY "
            "to run live n8n validation"
        ),
        allow_module_level=True,
    )


def test_manual_monitoring_sync_is_read_only_and_idempotent(tmp_path):
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'live-validation.db'}",
    )
    with TestClient(create_app(settings)) as client:
        first = client.post("/api/v1/monitoring/sync")
        second = client.post("/api/v1/monitoring/sync")
        status = client.get("/api/v1/monitoring/status")

    assert first.status_code == 200
    assert first.json()["status"] == "completed"
    assert second.status_code == 200
    assert second.json()["status"] == "completed"
    assert status.status_code == 200
    assert status.json()["enabled"] is True
    assert status.json()["last_fresh_poll_at"] is not None

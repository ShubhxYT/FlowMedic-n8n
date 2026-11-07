"""Opt-in local validation against a developer-owned n8n instance; never selected by CI."""

import os

import pytest
from auth_support import OperatorClient
from connection_support import KEYRING, provision

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
        connection_encryption_keys=KEYRING,
        connection_active_key_id="test",
    )
    with OperatorClient(create_app(settings)) as client:
        instance = provision(
            client.app.state.session_factory,
            base_url=settings.n8n_base_url,
            api_key=settings.n8n_api_key.get_secret_value(),
        )
        client.portal.call(client.app.state.registry.reconcile)
        query = {"instance_id": instance.id}
        first = client.post("/api/v1/monitoring/sync", params=query)
        second = client.post("/api/v1/monitoring/sync", params=query)
        status = client.get("/api/v1/monitoring/status", params=query)

    assert first.status_code == 200
    assert first.json()["status"] == "completed"
    assert second.status_code == 200
    assert second.json()["status"] == "completed"
    assert status.status_code == 200
    assert status.json()["items"][0]["enabled"] is True
    assert status.json()["items"][0]["last_fresh_poll_at"] is not None

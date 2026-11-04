from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import Settings
from app.core.database import create_database
from app.integrations.n8n.client import Execution
from app.main import create_app
from app.models.incident import Base, ExecutionHistory
from app.services.health import build_workflow_health, global_recent_counts
from app.services.monitoring import MonitoringService


def history(execution_id, started, recorded, success):
    return ExecutionHistory(
        source="n8n",
        workflow_id="w",
        workflow_name="Workflow",
        execution_id=execution_id,
        execution_status="success" if success else "error",
        started_at=started,
        recorded_at=recorded,
        is_success=success,
    )


@pytest.mark.parametrize("count", [20, 100])
def test_backfill_does_not_displace_recent_execution_window(count):
    now = datetime(2026, 10, 31, tzinfo=UTC)
    rows = [history(str(i), now - timedelta(minutes=i), now, True) for i in range(count)]
    rows.append(history("old", now - timedelta(days=10), now + timedelta(minutes=1), False))
    if count == 20:
        assert build_workflow_health(rows, [])[0].health == "healthy"
    else:
        assert global_recent_counts(rows) == (100, 0)


def test_monitoring_updates_running_execution_and_sanitizes_name():
    settings = Settings(
        _env_file=None,
        database_url="sqlite:///:memory:",
        n8n_base_url="https://n8n.example",
        n8n_api_key="test-secret",
    )
    engine, factory = create_database(settings.database_url)
    Base.metadata.create_all(engine)
    service = MonitoringService(settings, factory, None)
    try:
        execution = Execution(
            id="1", workflowId="w", status="running", workflowData={"name": "token=test-secret"}
        )
        assert service._persist_entries([execution]) == (1, 0)
        execution.status = "success"
        execution.stoppedAt = datetime(2026, 10, 31, tzinfo=UTC)
        assert service._persist_entries([execution]) == (0, 0)
        with factory() as session:
            rows = session.scalars(select(ExecutionHistory)).all()
            assert len(rows) == 1
            assert rows[0].is_success is True
            assert rows[0].stopped_at is not None
            assert "test-secret" not in rows[0].workflow_name
        # A stale in-flight poll must not revert a completed execution.
        execution.status = "running"
        execution.stoppedAt = None
        service._persist_entries([execution])
        with factory() as session:
            assert session.scalar(select(ExecutionHistory)).is_success is True
    finally:
        engine.dispose()


def test_authenticated_cors_preflight_and_error_headers():
    settings = Settings(
        _env_file=None, database_url="sqlite:///:memory:", flowmedic_api_key="private"
    )
    with TestClient(create_app(settings)) as client:
        headers = {
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "Authorization,Content-Type",
        }
        response = client.options("/api/v1/alerts/rules/anything", headers=headers)
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
        response = client.get("/api/v1/incidents", headers={"Origin": headers["Origin"]})
        assert response.status_code == 401
        assert response.headers["access-control-allow-origin"] == headers["Origin"]
        assert client.get("/api/v1/incidents").status_code == 401


def test_workflow_names_are_redacted_before_history_persistence():
    settings = Settings(
        _env_file=None,
        database_url="sqlite:///:memory:",
        n8n_base_url="https://n8n.example",
        n8n_api_key="private-key",
    )
    engine, factory = create_database(settings.database_url)
    Base.metadata.create_all(engine)
    try:
        service = MonitoringService(settings, factory, None)
        service._persist_entries(
            [
                Execution(
                    id="1",
                    workflowId="w",
                    status="success",
                    workflowData={"name": "Customer user@example.com token=private-key"},
                )
            ]
        )
        with factory() as session:
            name = session.scalar(select(ExecutionHistory)).workflow_name
            assert "private-key" not in name
            assert "user@example.com" not in name
    finally:
        engine.dispose()

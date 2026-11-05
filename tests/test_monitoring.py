import asyncio
from datetime import UTC, datetime

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

from app.core.config import Settings
from app.core.database import create_database
from app.core.errors import ServiceError
from app.core.migrations import upgrade_database
from app.main import create_app
from app.models.incident import Base, ExecutionHistory
from app.services.health import HealthCounts, build_workflow_health, classify_health
from app.services.monitoring import MonitoringService


def test_monitoring_is_disabled_without_n8n_configuration(monkeypatch):
    monkeypatch.delenv("N8N_BASE_URL", raising=False)
    monkeypatch.delenv("N8N_API_KEY", raising=False)

    settings = Settings(
        _env_file=None,
        database_url="sqlite:///:memory:",
    )

    with TestClient(create_app(settings)) as client:
        status = client.get("/api/v1/monitoring/status").json()

        assert status["enabled"] is False
        assert status["state"] == "disabled"
        assert status["lease_state"] == "unclaimed"
        assert status["retention_days"] == 30

        assert client.post("/api/v1/monitoring/sync").json()["status"] == "disabled"


def test_monitoring_background_task_starts_once_and_stops_cleanly(execution):
    def handler(request):
        return httpx.Response(200, json={"data": [], "nextCursor": None})

    settings = Settings(
        _env_file=None,
        database_url="sqlite:///:memory:",
        n8n_base_url="https://n8n.example",
        n8n_api_key="key",
        monitor_poll_interval_seconds=60,
    )
    app = create_app(settings, n8n_transport=httpx.MockTransport(handler))
    with TestClient(app):
        task = app.state.monitoring._task
        assert task is not None
        assert task.get_name() == "flowmedic-monitor"
    assert app.state.monitoring._task is None


def test_manual_monitoring_sync_persists_history_checkpoint_and_incident(client):
    status = client.get("/api/v1/monitoring/status").json()
    assert status["enabled"] is True
    assert status["state"] == "idle"

    first = client.post("/api/v1/monitoring/sync").json()
    assert first == {
        "status": "completed",
        "executions_discovered": 1,
        "history_records_created": 1,
        "incidents_created": 1,
        "next_cursor": "next-page",
    }
    second = client.post("/api/v1/monitoring/sync").json()
    assert second["history_records_created"] == 0
    assert second["incidents_created"] == 0
    assert client.get("/api/v1/metrics/workflows").json()["data"][0]["health"] == "unhealthy"
    overview = client.get("/api/v1/metrics/overview").json()
    assert overview["monitored_workflow_count"] == 1
    assert overview["recent_failure_count"] == 1
    assert overview["last_successful_monitoring_sync_at"] is not None
    hardened = client.get("/api/v1/monitoring/status").json()
    assert hardened["last_fresh_poll_at"] is not None
    assert hardened["backfill_state"] == "pending"


def test_workflow_health_endpoint_is_safe_for_missing_history(client):
    response = client.get("/api/v1/workflows/missing/health")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "workflow_not_found"


class FakeN8n:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.release = asyncio.Event()

    async def recent_executions(self, limit, cursor):
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def execution_details(self, execution_id):
        raise AssertionError("No detail request expected")


def _page():
    from app.integrations.n8n.client import ExecutionPage

    return ExecutionPage(data=[], nextCursor=None)


def _service(fake, **settings_values):
    settings = Settings(
        _env_file=None,
        database_url="sqlite:///:memory:",
        n8n_base_url="https://n8n.example",
        n8n_api_key="key",
        monitor_retry_base_seconds=0.001,
        **settings_values,
    )
    engine, factory = create_database(settings.database_url)
    Base.metadata.create_all(engine)
    return MonitoringService(settings, factory, fake)


def test_transient_failure_retries_and_recovers():
    async def run():
        fake = FakeN8n([ServiceError("n8n_server_error", "temporary", 503), _page()])
        service = _service(fake, monitor_max_retries=2)
        result = await service.sync()
        assert result.status == "completed"
        assert fake.calls == 2
        assert service.status().state == "idle"

    asyncio.run(run())


def test_timeout_failure_retries_and_recovers():
    async def run():
        fake = FakeN8n([ServiceError("n8n_timeout", "n8n request timed out", 504), _page()])
        service = _service(fake, monitor_max_retries=1)
        assert (await service.sync()).status == "completed"
        assert fake.calls == 2

    asyncio.run(run())


def test_authentication_failure_does_not_retry():
    async def run():
        fake = FakeN8n([ServiceError("n8n_auth_failed", "n8n rejected credentials", 502)])
        service = _service(fake, monitor_max_retries=3)
        assert (await service.sync()).status == "failed"
        status = service.status()
        assert fake.calls == 1
        assert status.state == "degraded"
        assert status.consecutive_failure_count == 1
        assert status.last_error_summary == "n8n rejected credentials"

    asyncio.run(run())


def test_concurrent_sync_is_skipped_while_poll_is_running():
    class SlowN8n:
        def __init__(self):
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def recent_executions(self, limit, cursor):
            self.started.set()
            await self.release.wait()
            return _page()

    async def run():
        fake = SlowN8n()
        service = _service(fake)
        first = asyncio.create_task(service.sync())
        await fake.started.wait()
        assert (await service.sync()).status == "skipped"
        fake.release.set()
        assert (await first).status == "completed"

    asyncio.run(run())


def test_health_rules_are_deterministic():
    assert classify_health(HealthCounts(0, 0, 0, 0)) == "unknown"
    assert classify_health(HealthCounts(2, 2, 0, 0)) == "healthy"
    assert classify_health(HealthCounts(2, 1, 1, 0)) == "degraded"
    assert classify_health(HealthCounts(2, 0, 2, 0)) == "unhealthy"
    assert classify_health(HealthCounts(3, 3, 0, 2)) == "unhealthy"


def test_health_metrics_do_not_invent_a_success_rate():
    now = datetime(2026, 9, 10, tzinfo=UTC)
    history = [
        ExecutionHistory(
            source="n8n",
            workflow_id="workflow-1",
            workflow_name="Workflow one",
            execution_id="run-1",
            execution_status="running",
            recorded_at=now,
            started_at=now,
            stopped_at=None,
            is_success=None,
        )
    ]
    health = build_workflow_health(history, [])[0]
    assert health.health == "unknown"
    assert health.success_rate is None


def test_migration_preserves_existing_incident_table(tmp_path):
    database = tmp_path / "existing.db"
    url = f"sqlite:///{database}"
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE incidents (id VARCHAR(36) PRIMARY KEY, "
                "execution_id VARCHAR(255) UNIQUE)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO incidents (id, execution_id) VALUES ('old-incident', 'old-execution')"
            )
        )
    upgrade_database(url)
    tables = set(inspect(create_engine(url)).get_table_names())
    assert {
        "incidents",
        "monitoring_checkpoints",
        "execution_history",
        "alembic_version",
    } <= tables
    with create_engine(url).connect() as connection:
        assert (
            connection.execute(text("SELECT execution_id FROM incidents")).scalar_one()
            == "old-execution"
        )


def test_successful_executions_use_resolved_workflow_names():
    from app.integrations.n8n.client import (
        Execution,
        ExecutionPage,
        N8nWorkflow,
        N8nWorkflowPage,
    )

    class NamedN8n:
        async def recent_executions(self, limit, cursor):
            return ExecutionPage(
                data=[
                    Execution(id="1", workflowId="wf-1", status="success"),
                    Execution(id="2", workflowId="wf-2", status="success"),
                ],
                nextCursor=None,
            )

        async def execution_details(self, execution_id):
            raise AssertionError("No detail request expected for successful executions")

        async def workflows(self, limit=50, cursor=None):
            return N8nWorkflowPage(
                data=[
                    N8nWorkflow(id="wf-1", name="Real Workflow One", active=True),
                    N8nWorkflow(id="wf-2", name="Real Workflow Two", active=False),
                ],
                nextCursor=None,
            )

    async def run():
        service = _service(NamedN8n())
        await service.sync()
        return service

    service = asyncio.run(run())
    with service.session_factory() as session:
        names = {
            row.execution_id: row.workflow_name for row in session.query(ExecutionHistory).all()
        }
    assert names == {"1": "Real Workflow One", "2": "Real Workflow Two"}


def test_workflow_name_lookup_failure_does_not_break_sync():
    from app.integrations.n8n.client import Execution, ExecutionPage

    class BrokenNamesN8n:
        async def recent_executions(self, limit, cursor):
            return ExecutionPage(
                data=[Execution(id="1", workflowId="wf-1", status="success")],
                nextCursor=None,
            )

        async def execution_details(self, execution_id):
            raise AssertionError("No detail request expected")

        async def workflows(self, limit=50, cursor=None):
            raise ServiceError("n8n_server_error", "listing unavailable", 503)

    async def run():
        service = _service(BrokenNamesN8n())
        await service.sync()
        return service

    service = asyncio.run(run())
    with service.session_factory() as session:
        row = session.query(ExecutionHistory).one()
    assert row.workflow_name == "Unknown workflow"


def test_missing_execution_detail_does_not_abort_mixed_fresh_page(monkeypatch):
    from unittest.mock import Mock

    from sqlalchemy import select

    from app.integrations.n8n.client import Execution, ExecutionPage
    from app.models.incident import Incident

    class MixedPageN8n:
        async def recent_executions(self, limit, cursor):
            return ExecutionPage(
                data=[
                    Execution(id="gone", workflowId="wf-1", status="error"),
                    Execution(id="failed", workflowId="wf-1", status="error"),
                    Execution(id="healthy", workflowId="wf-1", status="success"),
                ],
                nextCursor=None,
            )

        async def execution_details(self, execution_id):
            if execution_id == "gone":
                raise ServiceError("n8n_http_error", "n8n returned an unsuccessful response", 404)
            return Execution(
                id=execution_id,
                workflowId="wf-1",
                status="error",
            )

    async def run():
        service = _service(MixedPageN8n())
        result = await service.sync()
        assert result.status == "completed"
        assert result.executions_discovered == 3
        assert result.history_records_created == 3
        assert result.incidents_created == 1

        with service.session_factory() as session:
            history = {
                item.execution_id: item for item in session.scalars(select(ExecutionHistory)).all()
            }
            incidents = session.scalars(select(Incident)).all()

        assert set(history) == {"gone", "failed", "healthy"}
        assert history["gone"].execution_status is None
        assert history["gone"].is_success is None
        assert history["gone"].incident_id is None
        assert history["healthy"].execution_status == "success"
        assert len(incidents) == 1
        assert incidents[0].execution_id == "failed"

    warning = Mock()
    monkeypatch.setattr("app.services.monitoring.logger.warning", warning)
    asyncio.run(run())
    warning.assert_any_call(
        "monitoring_execution_detail_unavailable execution_id=%s status_code=%s",
        "gone",
        404,
    )
    assert all("payload" not in str(call).lower() for call in warning.call_args_list)

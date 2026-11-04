import asyncio
from datetime import timedelta

from sqlalchemy import create_engine, inspect, select

from app.core.config import Settings
from app.core.database import create_database
from app.core.migrations import upgrade_database
from app.models.incident import Base, ExecutionHistory, Incident
from app.repositories.monitoring import MonitoringRepository
from app.services.health import build_workflow_health
from app.services.monitoring import MonitoringService, utc_now


class FreshThenBackfillN8n:
    def __init__(self):
        self.calls: list[str | None] = []
        self.fresh_count = 0
        self.repeat_fresh = False

    async def recent_executions(self, limit, cursor):
        from app.integrations.n8n.client import Execution, ExecutionPage

        self.calls.append(cursor)
        if cursor is None:
            self.fresh_count += 1
            return ExecutionPage(
                data=[
                    Execution(
                        id="fresh-1" if self.repeat_fresh else f"fresh-{self.fresh_count}",
                        workflowId="workflow",
                        status="success",
                    )
                ],
                nextCursor="older-1",
            )
        if cursor == "older-1":
            return ExecutionPage(
                data=[Execution(id="older-1", workflowId="workflow", status="success")],
                nextCursor=None,
            )
        raise AssertionError(f"unexpected cursor: {cursor}")

    async def execution_details(self, execution_id):
        raise AssertionError(f"details are not needed for {execution_id}")


def service_with_database(tmp_path, n8n):
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'monitoring.db'}",
        n8n_base_url="https://n8n.example",
        n8n_api_key="test-key",
        monitor_backfill_pages_per_cycle=1,
        retention_cleanup_interval_seconds=60,
    )
    engine, factory = create_database(settings.database_url)
    Base.metadata.create_all(engine)
    return MonitoringService(settings, factory, n8n), factory


def test_fresh_poll_finds_new_execution_while_backfill_is_pending(tmp_path):
    async def run():
        n8n = FreshThenBackfillN8n()
        service, factory = service_with_database(tmp_path, n8n)

        first = await service.sync()
        assert first.executions_discovered == 1
        assert n8n.calls == [None]
        assert service.status().backfill_pending is True

        second = await service.sync()
        assert second.executions_discovered == 2
        assert n8n.calls == [None, None, "older-1"]
        assert service.status().backfill_pending is False

        with factory() as session:
            history_ids = set(session.scalars(select(ExecutionHistory.execution_id)))
        assert history_ids == {"fresh-1", "fresh-2", "older-1"}

    asyncio.run(run())


def test_repeated_fresh_pages_do_not_duplicate_history(tmp_path):
    async def run():
        n8n = FreshThenBackfillN8n()
        n8n.repeat_fresh = True
        service, factory = service_with_database(tmp_path, n8n)
        await service.sync()
        await service.sync()
        await service.sync()
        with factory() as session:
            count = len(list(session.scalars(select(ExecutionHistory))))
        assert count == 2

    asyncio.run(run())


def test_database_lease_contends_and_recovers_after_expiry(tmp_path):
    n8n = FreshThenBackfillN8n()
    first, factory = service_with_database(tmp_path, n8n)
    second = MonitoringService(first.settings, factory, n8n)

    with factory() as session:
        repository = MonitoringRepository(session)
        acquired, recovered = repository.acquire_lease("n8n", "default", first._owner_id, 120)
        assert acquired is True
        assert recovered is False

    with factory() as session:
        acquired, recovered = MonitoringRepository(session).acquire_lease(
            "n8n", "default", second._owner_id, 120
        )
        assert acquired is False
        assert recovered is False

    with factory() as session:
        checkpoint = MonitoringRepository(session).checkpoint("n8n", "default")
        checkpoint.lease_expires_at = utc_now() - timedelta(seconds=1)
        session.commit()

    with factory() as session:
        acquired, recovered = MonitoringRepository(session).acquire_lease(
            "n8n", "default", second._owner_id, 120
        )
        assert acquired is True
        assert recovered is True


def test_retention_removes_only_old_history_and_health_uses_retained_window(tmp_path):
    async def run():
        n8n = FreshThenBackfillN8n()
        service, factory = service_with_database(tmp_path, n8n)
        old_time = utc_now() - timedelta(days=31)
        with factory() as session:
            incident = Incident(
                id="retained-incident",
                execution_id="failed-old",
                workflow_id="workflow",
                workflow_name="Workflow",
                failed_node=None,
                error_type="test",
                error_message="safe test failure",
                execution_timestamp=old_time,
                status="open",
                severity="medium",
                diagnosis=None,
                diagnosis_provider=None,
            )
            session.add(incident)
            session.add(
                ExecutionHistory(
                    source="n8n",
                    workflow_id="workflow",
                    workflow_name="Workflow",
                    execution_id="failed-old",
                    execution_status="error",
                    recorded_at=old_time,
                    started_at=old_time,
                    stopped_at=old_time,
                    is_success=False,
                    incident_id=incident.id,
                )
            )
            session.commit()

        await service.sync()
        with factory() as session:
            history = list(session.scalars(select(ExecutionHistory)))
            incidents = list(session.scalars(select(Incident)))
            health = build_workflow_health(history, incidents)[0]
        assert {item.execution_id for item in history} == {"fresh-1"}
        assert len(incidents) == 1
        assert health.health == "degraded"

    asyncio.run(run())


def test_monitoring_hardening_migration_adds_non_destructive_columns(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'migrated.db'}"
    upgrade_database(database_url)
    columns = {
        column["name"]
        for column in inspect(create_engine(database_url)).get_columns("monitoring_checkpoints")
    }
    assert {
        "backfill_cursor",
        "backfill_completed_at",
        "last_fresh_poll_at",
        "last_retention_at",
        "lease_expires_at",
    } <= columns

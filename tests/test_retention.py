import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.core.database import create_database
from app.models.incident import Base, ExecutionHistory
from app.repositories.monitoring import ExecutionHistoryRepository
from app.services.monitoring import MonitoringRepository, MonitoringService, utc_now


class UnusedN8n:
    """Retention cleanup never touches the n8n client."""

    def __getattr__(self, name):
        def fail(*args, **kwargs):
            raise AssertionError(f"retention cleanup must not call n8n.{name}")

        return fail


class FakeMonotonic:
    """Deterministic monotonic clock; every call advances by a fixed step."""

    def __init__(self, step_seconds: float, start: float = 0.0):
        self.value = start
        self.step_seconds = step_seconds

    def __call__(self) -> float:
        value = self.value
        self.value += self.step_seconds
        return value


def make_service(tmp_path, **overrides):
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'retention.db'}",
        n8n_base_url="https://n8n.example",
        n8n_api_key="test-key",
        **overrides,
    )
    engine, factory = create_database(settings.database_url)
    Base.metadata.create_all(engine)
    return MonitoringService(settings, factory, UnusedN8n()), factory


def seed_history(factory, now, rows):
    """rows: list of (execution_id, recorded_at) tuples."""
    with factory() as session:
        for execution_id, recorded_at in rows:
            session.add(
                ExecutionHistory(
                    source="n8n",
                    workflow_id="workflow",
                    workflow_name="Workflow",
                    execution_id=execution_id,
                    execution_status="success",
                    recorded_at=recorded_at,
                    is_success=True,
                )
            )
        session.commit()


def seed_backlog(factory, count, now, age_days=31):
    seed_history(
        factory,
        now,
        [
            (f"backlog-{index}", now - timedelta(days=age_days, seconds=index))
            for index in range(count)
        ],
    )


def eligible_count(factory):
    with factory() as session:
        return len(
            list(
                session.scalars(
                    select(ExecutionHistory).where(
                        ExecutionHistory.execution_id.like("backlog-%")
                    )
                )
            )
        )


def surviving_history(factory):
    with factory() as session:
        return {item.execution_id for item in session.scalars(select(ExecutionHistory))}


def rewind_last_retention_at(factory, seconds):
    """Force the next sync to consider retention due."""
    with factory() as session:
        checkpoint = MonitoringRepository(session).checkpoint("n8n", "default")
        checkpoint.last_retention_at = datetime.now(UTC) - timedelta(seconds=seconds)
        session.commit()


def count_delete_batches(monkeypatch):
    """Instrument the repository so each batch invocation is observable."""
    batches = []
    batch_size_observed = []
    original = ExecutionHistoryRepository.delete_recorded_before

    def wrapped(self, cutoff, batch_size):
        deleted = original(self, cutoff, batch_size)
        batch_size_observed.append(batch_size)
        batches.append(deleted)
        return deleted

    monkeypatch.setattr(ExecutionHistoryRepository, "delete_recorded_before", wrapped)
    return batches, batch_size_observed


def test_cleanup_deletes_multiple_batches_when_backlog_exceeds_batch_size(tmp_path, monkeypatch):
    async def run():
        batches, _ = count_delete_batches(monkeypatch)
        service, factory = make_service(tmp_path, retention_cleanup_batch_size=500)
        seed_backlog(factory, 1100, utc_now())
        service._run_retention_if_due(monotonic=FakeMonotonic(step_seconds=1))
        assert batches == [500, 500, 100]
        assert eligible_count(factory) == 0

    asyncio.run(run())


def test_cleanup_stops_when_fewer_than_full_batch_is_deleted(tmp_path, monkeypatch):
    async def run():
        batches, _ = count_delete_batches(monkeypatch)
        service, factory = make_service(tmp_path, retention_cleanup_batch_size=500)
        seed_backlog(factory, 1200, utc_now())
        service._run_retention_if_due(monotonic=FakeMonotonic(step_seconds=1))
        # 500 + 500 + 200: the partial batch signals an empty backlog and the loop stops.
        assert batches == [500, 500, 200]
        assert eligible_count(factory) == 0

    asyncio.run(run())


def test_cleanup_respects_the_total_time_budget(tmp_path, monkeypatch):
    async def run():
        batches, requested = count_delete_batches(monkeypatch)
        service, factory = make_service(
            tmp_path,
            retention_cleanup_batch_size=500,
            retention_cleanup_budget_seconds=250,
        )
        # A backlog that would need far more batches than the budget allows.
        seed_backlog(factory, 10000, utc_now())
        # Each clock call advances 100s against a 250s budget: the deadline check
        # passes at t=100 and t=200, then t=300 >= 250 stops before a third batch.
        service._run_retention_if_due(monotonic=FakeMonotonic(step_seconds=100))
        assert batches == [500, 500]
        assert requested == [500, 500]
        assert eligible_count(factory) == 10000 - 1000

    asyncio.run(run())


def test_recent_records_inside_the_retention_window_are_preserved(tmp_path, monkeypatch):
    async def run():
        count_delete_batches(monkeypatch)
        service, factory = make_service(tmp_path, retention_cleanup_batch_size=500)
        now = utc_now()
        seed_history(
            factory,
            now,
            [
                ("recent-1", now - timedelta(days=1)),
                ("boundary-1", now - timedelta(days=30) + timedelta(seconds=30)),
            ],
        )
        seed_backlog(factory, 700, now)
        service._run_retention_if_due(monotonic=FakeMonotonic(step_seconds=1))
        assert surviving_history(factory) == {"recent-1", "boundary-1"}

    asyncio.run(run())


def test_1440_rows_per_day_backlog_converges_with_default_batch_size(tmp_path, monkeypatch):
    async def run():
        batches, _ = count_delete_batches(monkeypatch)
        service, factory = make_service(tmp_path, retention_cleanup_batch_size=500)
        seed_backlog(factory, 1440, utc_now())
        service._run_retention_if_due(monotonic=FakeMonotonic(step_seconds=1))
        # 500 + 500 + 440: one run drains a full day's worst-case backlog.
        assert batches == [500, 500, 440]
        assert eligible_count(factory) == 0

    asyncio.run(run())


def test_repeated_cleanup_runs_do_not_reprocess_or_misdelete(tmp_path, monkeypatch):
    async def run():
        batches, _ = count_delete_batches(monkeypatch)
        service, factory = make_service(tmp_path, retention_cleanup_batch_size=500)
        now = utc_now()
        seed_backlog(factory, 1200, now, age_days=31)
        seed_history(
            factory,
            now,
            [("recent-1", now - timedelta(days=1))],
        )
        service._run_retention_if_due(monotonic=FakeMonotonic(step_seconds=1))
        assert batches == [500, 500, 200]
        assert surviving_history(factory) == {"recent-1"}

        # Within the configured interval the next run is a no-op and never reprocesses.
        runs_so_far = len(batches)
        service._run_retention_if_due(monotonic=FakeMonotonic(step_seconds=1))
        assert len(batches) == runs_so_far
        assert surviving_history(factory) == {"recent-1"}

        # Once the interval has elapsed and a new backlog has accumulated,
        # a due run repeats safely and only deletes what is outside the window.
        rewind_last_retention_at(factory, seconds=86401)
        seed_backlog(factory, 3, now)
        service._run_retention_if_due(monotonic=FakeMonotonic(step_seconds=1))
        assert surviving_history(factory) == {"recent-1"}
        assert eligible_count(factory) == 0

    asyncio.run(run())


def test_retention_cleanup_budget_setting_is_validated():
    assert (
        Settings(
            _env_file=None,
            n8n_base_url="https://n8n.example",
            n8n_api_key="test-key",
        ).retention_cleanup_budget_seconds
        == 300
    )
    for value in (0, -1, 3601):
        with pytest.raises(ValueError):
            Settings(
                _env_file=None,
                n8n_base_url="https://n8n.example",
                n8n_api_key="test-key",
                retention_cleanup_budget_seconds=value,
            )

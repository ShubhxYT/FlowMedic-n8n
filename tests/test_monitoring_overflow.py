import asyncio

from sqlalchemy import create_engine, inspect, select

from app.core.config import Settings
from app.core.database import create_database
from app.core.errors import ServiceError
from app.core.migrations import upgrade_database
from app.integrations.n8n.client import Execution, ExecutionPage
from app.models.incident import Base, ExecutionHistory
from app.repositories.monitoring import MonitoringRepository
from app.services.monitoring import MonitoringService

SOURCE = "n8n"
IDENTIFIER = "default"


class OverflowN8n:
    """Deterministic cursor -> page fake with optional one-shot failures."""

    def __init__(self, pages: dict[str | None, tuple[list[Execution], str | None]]):
        self.pages = pages  # mutable between cycles for evolving scenarios
        self.calls: list[str | None] = []
        self.failures: dict[str | None, list[Exception]] = {}
        self.detail_failures: dict[str, list[Execution]] = {}

    async def recent_executions(self, limit, cursor):
        self.calls.append(cursor)
        if self.failures.get(cursor):
            raise self.failures[cursor].pop(0)
        data, next_cursor = self.pages.get(cursor, ([], None))
        return ExecutionPage(data=list(data), nextCursor=next_cursor)

    async def execution_details(self, execution_id):
        if self.detail_failures.get(execution_id):
            raise self.detail_failures[execution_id].pop(0)
        return Execution(
            id=execution_id,
            workflowId="wf-1",
            status="error",
            data={"resultData": {"error": {"message": "boom"}}},
        )


def executions(start: int, stop: int) -> list[Execution]:
    """Inclusive range of synthetic executions, ids e-<n>."""
    return [
        Execution(id=f"e-{i}", workflowId="wf-1", status="success") for i in range(start, stop + 1)
    ]


def _service(tmp_path, n8n, **overrides):
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'monitoring.db'}",
        n8n_base_url="https://n8n.example",
        n8n_api_key="test-key",
        monitor_retry_base_seconds=0.001,
        **overrides,
    )
    engine, factory = create_database(settings.database_url)
    Base.metadata.create_all(engine)
    return MonitoringService(settings, factory, n8n), factory


def _checkpoint_state(factory) -> dict:
    with factory() as session:
        checkpoint = MonitoringRepository(session).checkpoint(SOURCE, IDENTIFIER)
        return {
            "pending": list(checkpoint.pending_cursors or []),
            "backfill_cursor": checkpoint.backfill_cursor,
            "backfill_completed_at": checkpoint.backfill_completed_at,
        }


def _persisted_ids(factory) -> set[str]:
    with factory() as session:
        return set(session.scalars(select(ExecutionHistory.execution_id)))


def test_burst_of_200_is_ingested_in_a_single_cycle(tmp_path):
    pages = {
        None: (executions(1, 50), "c1"),
        "c1": (executions(51, 100), "c2"),
        "c2": (executions(101, 150), "c3"),
        "c3": (executions(151, 200), None),
    }
    n8n = OverflowN8n(pages)
    service, factory = _service(tmp_path, n8n)

    result = asyncio.run(service.sync())

    assert result.status == "completed"
    assert result.executions_discovered == 200
    assert result.next_cursor is None
    assert _persisted_ids(factory) == {f"e-{i}" for i in range(1, 201)}
    assert _checkpoint_state(factory)["pending"] == []
    assert _checkpoint_state(factory)["backfill_cursor"] is None


def test_burst_bound_documented_by_overflow_pages_setting(tmp_path):
    """N = ceil(burst / (page_size * (1 + overflow_pages_per_cycle)))."""
    pages = {
        None: (executions(1, 50), "c1"),
        "c1": (executions(51, 100), "c2"),
        "c2": (executions(101, 150), "c3"),
        "c3": (executions(151, 200), "c4"),
        "c4": ([Execution(id="e-201", workflowId="wf-1", status="success")], None),
    }
    n8n = OverflowN8n(pages)
    service, factory = _service(tmp_path, n8n, monitor_overflow_pages_per_cycle=3)

    first = asyncio.run(service.sync())
    assert first.executions_discovered == 200  # head page + 3 overflow pages
    assert _checkpoint_state(factory)["pending"] == ["c4"]

    second = asyncio.run(service.sync())
    assert second.executions_discovered == 1
    assert _persisted_ids(factory) == {f"e-{i}" for i in range(1, 202)}
    assert _checkpoint_state(factory)["pending"] == []

    assert n8n.calls == [None, "c1", "c2", "c3", "c4", None, "c1"]


def test_fresh_cursor_is_queued_while_backfill_sweep_is_active(tmp_path):
    """Issue #12 regression: the fresh cursor must not be discarded."""
    pages = {
        None: (executions(1, 50), "c1"),
        "c1": (executions(51, 100), "c2"),
        "c2": (executions(101, 150), None),
        "sweep-1": ([Execution(id="old-1", workflowId="wf-1", status="success")], None),
    }
    n8n = OverflowN8n(pages)
    service, factory = _service(tmp_path, n8n, monitor_overflow_pages_per_cycle=1)
    with factory() as session:
        checkpoint = MonitoringRepository(session).checkpoint(SOURCE, IDENTIFIER)
        checkpoint.backfill_cursor = "sweep-1"
        session.commit()

    asyncio.run(service.sync())
    state = _checkpoint_state(factory)
    assert state["pending"] == ["c2"]  # fresh cursor durably queued, not dropped
    assert state["backfill_cursor"] is None  # sweep owned the historical cursor
    assert state["backfill_completed_at"] is not None  # sweep completed to bottom

    second = asyncio.run(service.sync())
    assert _persisted_ids(factory) == {f"e-{i}" for i in range(1, 151)} | {"old-1"}
    assert _checkpoint_state(factory)["pending"] == []
    assert second.status == "completed"


def test_pending_fresh_work_survives_process_restart(tmp_path):
    pages = {
        None: (executions(1, 50), "c1"),
        "c1": (executions(51, 100), "c2"),
        "c2": (executions(101, 150), None),
    }
    n8n = OverflowN8n(pages)
    service, factory = _service(tmp_path, n8n, monitor_overflow_pages_per_cycle=1)

    asyncio.run(service.sync())
    assert _checkpoint_state(factory)["pending"] == ["c2"]

    # Simulate a process restart: a brand new service instance over the same DB.
    restarted = MonitoringService(service.settings, factory, n8n)
    assert restarted.status().pending_fresh_cursor_count == 1

    result = asyncio.run(restarted.sync())

    assert _persisted_ids(factory) == {f"e-{i}" for i in range(1, 151)}
    assert _checkpoint_state(factory)["pending"] == []
    assert result.status == "completed"


def test_backfill_completion_reseeds_from_newest_fresh_boundary(tmp_path):
    pages = {
        None: ([Execution(id="e-1", workflowId="wf-1", status="success")], "c1"),
        "c1": ([Execution(id="e-2", workflowId="wf-1", status="success")], "c2"),
        "c2": ([Execution(id="e-3", workflowId="wf-1", status="success")], None),
    }
    n8n = OverflowN8n(pages)
    service, factory = _service(tmp_path, n8n)

    asyncio.run(service.sync())
    assert _checkpoint_state(factory)["backfill_cursor"] is None

    asyncio.run(service.sync())
    # Head page is a duplicate: sweep seeded from the newest fresh boundary.
    assert _checkpoint_state(factory)["backfill_cursor"] == "c2"
    assert _checkpoint_state(factory)["backfill_completed_at"] is None

    asyncio.run(service.sync())
    state = _checkpoint_state(factory)
    assert state["backfill_cursor"] is None  # reached the bottom
    assert state["backfill_completed_at"] is not None

    asyncio.run(service.sync())
    state = _checkpoint_state(factory)
    # Sweep re-seeded after completion instead of staying obsolete.
    assert state["backfill_cursor"] == "c2"
    assert state["backfill_completed_at"] is None


def test_zero_backfill_pages_warns_and_keeps_fresh_recovery(tmp_path, caplog):
    pages = {
        None: (executions(1, 50), "c1"),
        "c1": (executions(51, 100), "c2"),
        "c2": (executions(101, 150), None),
    }
    n8n = OverflowN8n(pages)
    service, factory = _service(
        tmp_path, n8n, monitor_overflow_pages_per_cycle=1, monitor_backfill_pages_per_cycle=0
    )

    with caplog.at_level("WARNING", logger="app.services.monitoring"):
        asyncio.run(service.sync())
    assert "monitor_backfill_disabled_deferred_ranges" in caplog.text
    assert _persisted_ids(factory) == {f"e-{i}" for i in range(1, 101)}
    assert _checkpoint_state(factory)["pending"] == ["c2"]

    caplog.clear()
    with caplog.at_level("WARNING", logger="app.services.monitoring"):
        asyncio.run(service.sync())
    # Fresh recovery still progresses, and the historical boundary keeps its warning.
    assert _persisted_ids(factory) == {f"e-{i}" for i in range(1, 151)}
    assert "monitor_backfill_disabled_deferred_ranges" in caplog.text
    assert _checkpoint_state(factory)["pending"] == []


def test_duplicate_pages_never_create_duplicate_rows(tmp_path):
    duplicated = executions(1, 50)
    pages = {
        None: (duplicated, "c1"),
        "c1": (duplicated, "c2"),
        "c2": ([Execution(id="e-51", workflowId="wf-1", status="success")], None),
    }
    n8n = OverflowN8n(pages)
    service, factory = _service(tmp_path, n8n)

    asyncio.run(service.sync())

    with factory() as session:
        rows = session.scalars(select(ExecutionHistory)).all()
    assert len(rows) == 51  # 50 unique ids + the genuinely new e-51 caught below
    assert len({row.execution_id for row in rows}) == 51


def test_failed_page_does_not_advance_cursor(tmp_path):
    pages = {
        None: (executions(1, 50), "c1"),
        "c1": (executions(51, 100), "c2"),
        "c2": (executions(101, 150), None),
    }
    n8n = OverflowN8n(pages)
    service, factory = _service(
        tmp_path,
        n8n,
        monitor_overflow_pages_per_cycle=1,
        monitor_max_retries=0,
    )
    with factory() as session:
        checkpoint = MonitoringRepository(session).checkpoint(SOURCE, IDENTIFIER)
        checkpoint.pending_cursors = ["c2"]
        session.commit()
    n8n.failures["c2"] = [ServiceError("n8n_server_error", "temporary", 503)]

    failed = asyncio.run(service.sync())
    assert failed.status == "failed"
    # Nothing was persisted and no cursor moved past the unpersisted page.
    assert _persisted_ids(factory) == set()
    state = _checkpoint_state(factory)
    assert state["pending"] == ["c2"]
    assert state["backfill_cursor"] is None

    result = asyncio.run(service.sync())
    assert result.status == "completed"
    assert _persisted_ids(factory) == {f"e-{i}" for i in range(1, 151)}
    # The residual head-drain cursor is cleaned up by the next cycle.
    asyncio.run(service.sync())
    assert _checkpoint_state(factory)["pending"] == []


def test_transient_failure_recovers_without_loss(tmp_path):
    pages = {
        None: (executions(1, 50), "c1"),
        "c1": (executions(51, 100), "c2"),
        "c2": (executions(101, 150), None),
    }
    n8n = OverflowN8n(pages)
    service, factory = _service(tmp_path, n8n, monitor_overflow_pages_per_cycle=2)
    n8n.failures["c2"] = [ServiceError("n8n_rate_limited", "busy", 429)]

    result = asyncio.run(service.sync())
    assert result.status == "completed"
    workaround_ids = _persisted_ids(factory)
    assert {f"e-{i}" for i in range(1, 101)} <= workaround_ids

    asyncio.run(service.sync())
    assert _persisted_ids(factory) == {f"e-{i}" for i in range(1, 151)}


def test_persistence_failure_does_not_advance_cursor(tmp_path):
    pages = {
        None: (executions(1, 50), "c1"),
        "c1": (executions(51, 100), "c2"),
        "c2": ([Execution(id="f-1", workflowId="wf-1", status="error")], None),
    }
    n8n = OverflowN8n(pages)
    service, factory = _service(
        tmp_path, n8n, monitor_overflow_pages_per_cycle=2, monitor_max_retries=1
    )
    n8n.detail_failures["f-1"] = [ServiceError("n8n_timeout", "detail timed out", 504)]

    asyncio.run(service.sync())

    ids = _persisted_ids(factory)
    assert "f-1" not in ids  # page was never persisted
    assert {f"e-{i}" for i in range(1, 101)} <= ids
    assert _checkpoint_state(factory)["pending"] == []
    # The cursor did not advance past the unpersisted page.
    second = asyncio.run(service.sync())
    assert second.status == "completed"
    assert "f-1" in _persisted_ids(factory)


def test_empty_pages_and_opaque_cursors_are_handled(tmp_path):
    empty = executions(51, 90)
    pages = {
        None: ([], "weird-cursor"),
        "weird-cursor": (empty, None),
    }
    n8n = OverflowN8n(pages)
    service, factory = _service(tmp_path, n8n)

    result = asyncio.run(service.sync())

    assert result.status == "completed"
    assert _persisted_ids(factory) == {f"e-{i}" for i in range(51, 91)}
    assert result.next_cursor is None
    assert _checkpoint_state(factory)["pending"] == []


def test_multiple_overflow_cuts_are_queued_and_drained_in_order(tmp_path):
    pages = {
        None: (executions(1, 50), "c1"),
        "c1": (executions(51, 100), "c2"),
        "c2": (executions(101, 150), "c3"),
        "c3": (executions(151, 200), None),
    }
    n8n = OverflowN8n(pages)
    service, factory = _service(tmp_path, n8n, monitor_overflow_pages_per_cycle=1)

    asyncio.run(service.sync())
    assert _checkpoint_state(factory)["pending"] == ["c2"]

    second = asyncio.run(service.sync())
    assert _checkpoint_state(factory)["pending"] == ["c3"]
    assert second.executions_discovered == 50  # pending range walked; head page duplicates

    asyncio.run(service.sync())
    assert _persisted_ids(factory) == {f"e-{i}" for i in range(1, 201)}
    assert _checkpoint_state(factory)["pending"] == []


def test_pending_cursors_column_is_migrated(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'migrated.db'}"
    upgrade_database(database_url)
    columns = {
        column["name"]
        for column in inspect(create_engine(database_url)).get_columns("monitoring_checkpoints")
    }
    assert "pending_cursors" in columns

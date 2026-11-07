from datetime import UTC, datetime, timedelta

import pytest

from app.core.database import create_database
from app.models.incident import Base, ExecutionHistory, Incident
from app.services.health import build_workflow_health, global_recent_counts
from app.services.health_queries import (
    global_recent_counts as sql_global_recent_counts,
)
from app.services.health_queries import (
    open_incident_count,
    workflow_health_rows,
)


@pytest.fixture
def session_factory():
    engine, factory = create_database("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    yield factory
    engine.dispose()


def make_history(
    execution_id,
    workflow_id,
    started_at,
    recorded_at,
    success,
    workflow_name="Workflow",
):
    return ExecutionHistory(
        source="n8n",
        workflow_id=workflow_id,
        workflow_name=workflow_name,
        execution_id=execution_id,
        execution_status=(
            "success" if success is True else "error" if success is False else "unknown"
        ),
        started_at=started_at,
        recorded_at=recorded_at,
        is_success=success,
    )


def make_incident(execution_id, workflow_id, status="open"):
    return Incident(
        execution_id=execution_id,
        workflow_id=workflow_id,
        workflow_name="Workflow",
        error_type="TestError",
        error_message="Test incident",
        severity="high",
        status=status,
    )


def test_sql_workflow_health_preserves_recent_window_and_incident_rules(
    session_factory,
):
    now = datetime(2026, 10, 31, 12, tzinfo=UTC)

    rows = [
        make_history(
            execution_id=f"execution-{i}",
            workflow_id="workflow-1",
            started_at=now - timedelta(minutes=i),
            recorded_at=now - timedelta(minutes=i),
            success=True if i < 20 else False,
            workflow_name=("Unknown workflow" if i == 0 else "Payments workflow"),
        )
        for i in range(25)
    ]

    # A late backfill must not displace a newer execution.
    rows.append(
        make_history(
            execution_id="old-backfill",
            workflow_id="workflow-1",
            started_at=now - timedelta(days=30),
            recorded_at=now + timedelta(days=1),
            success=False,
            workflow_name="Old workflow name",
        )
    )

    incidents = [
        make_incident("incident-1", "workflow-1"),
        make_incident("incident-2", "workflow-1"),
        make_incident("incident-3", "workflow-1", status="closed"),
    ]

    with session_factory() as session:
        session.add_all(rows + incidents)
        session.commit()

        sql_result = workflow_health_rows(session)
        python_result = build_workflow_health(rows, incidents)

        assert len(sql_result) == 1
        assert sql_result[0] == python_result[0]

        result = sql_result[0]
        assert result.recent_executions_count == 20
        assert result.recent_success_count == 20
        assert result.recent_failure_count == 0
        assert result.workflow_name == "Payments workflow"
        assert result.health == "unhealthy"
        assert result.open_incident_count == 2
        assert result.last_checked_at == now + timedelta(days=1)
        assert open_incident_count(session) == 2


def test_sql_global_recent_counts_matches_python_reference(session_factory):
    now = datetime(2026, 10, 31, 12, tzinfo=UTC)

    rows = [
        make_history(
            execution_id=f"global-{i}",
            workflow_id="workflow-1",
            started_at=now - timedelta(minutes=i),
            recorded_at=now,
            success=(True if i % 3 == 0 else False if i % 3 == 1 else None),
        )
        for i in range(105)
    ]

    # This record is recorded later but its execution time is much older.
    rows.append(
        make_history(
            execution_id="late-backfill",
            workflow_id="workflow-1",
            started_at=now - timedelta(days=20),
            recorded_at=now + timedelta(days=1),
            success=False,
        )
    )

    with session_factory() as session:
        session.add_all(rows)
        session.commit()

        assert sql_global_recent_counts(session) == global_recent_counts(rows)


def test_sql_workflow_health_can_filter_by_workflow_id(session_factory):
    now = datetime(2026, 10, 31, 12, tzinfo=UTC)

    rows = [
        make_history("one", "workflow-1", now, now, True),
        make_history("two", "workflow-2", now, now, False),
    ]

    with session_factory() as session:
        session.add_all(rows)
        session.commit()

        result = workflow_health_rows(session, workflow_id="workflow-2")

        assert len(result) == 1
        assert result[0].workflow_id == "workflow-2"
        assert result[0].health == "unhealthy"

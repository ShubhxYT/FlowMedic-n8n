"""Synthetic data used only to make the public demo runnable without credentials."""

from datetime import UTC, datetime, timedelta

from app.repositories.monitoring import ExecutionHistoryRepository
from app.schemas.domain import Failure

DEMO_EXECUTION_ID = "demo-execution-auth-401"


def demo_failure() -> Failure:
    """Return a fixed, non-sensitive failure that exercises the diagnosis flow."""
    return Failure(
        workflow_id="demo-order-import",
        workflow_name="Demo order import",
        execution_id=DEMO_EXECUTION_ID,
        failed_node="Fetch orders",
        error_type="NodeApiError",
        error_message="401 Unauthorized from a simulated upstream service.",
        execution_timestamp=datetime(2026, 9, 9, 8, 0, tzinfo=UTC),
        severity="medium",
    )


def seed_demo_history(session, incident_id: str) -> None:
    """Seed a small, deterministic, synthetic history for the dashboard preview."""
    repository = ExecutionHistoryRepository(session)
    start = datetime(2026, 9, 9, 8, 0, tzinfo=UTC)
    records = [
        ("demo-healthy-workflow", "Demo inventory sync", "demo-inventory-1", "success", True, 1),
        ("demo-healthy-workflow", "Demo inventory sync", "demo-inventory-2", "success", True, 2),
        ("demo-degraded-workflow", "Demo catalog enrichment", "demo-catalog-1", "success", True, 3),
        ("demo-degraded-workflow", "Demo catalog enrichment", "demo-catalog-2", "error", False, 4),
        ("demo-order-import", "Demo order import", "demo-order-import-previous", "error", False, 5),
        ("demo-order-import", "Demo order import", DEMO_EXECUTION_ID, "error", False, 6),
    ]
    for workflow_id, workflow_name, execution_id, status, is_success, offset in records:
        history, _ = repository.create_once(
            source="demo",
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            execution_id=execution_id,
            execution_status=status,
            recorded_at=start + timedelta(minutes=offset),
            started_at=start + timedelta(minutes=offset),
            stopped_at=None,
            is_success=is_success,
        )
        if execution_id == DEMO_EXECUTION_ID:
            repository.attach_incident(history, incident_id)

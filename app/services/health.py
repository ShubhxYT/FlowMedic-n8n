from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime

from app.models.incident import ExecutionHistory, Incident
from app.schemas.domain import WorkflowHealth

RECENT_EXECUTIONS_PER_WORKFLOW = 20
RECENT_EXECUTIONS_GLOBAL = 100


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def execution_time(item: ExecutionHistory) -> datetime:
    # Backfilled executions arrive later but must not displace newer executions.
    return as_utc(item.started_at or item.stopped_at or item.recorded_at)


@dataclass(frozen=True)
class HealthCounts:
    execution_count: int
    success_count: int
    failure_count: int
    open_incident_count: int


def classify_health(counts: HealthCounts) -> str:
    """Return a deterministic state from the persisted, classified execution window."""
    if counts.execution_count == 0 or counts.success_count + counts.failure_count == 0:
        return "unknown"
    if (
        counts.failure_count >= 2
        or (counts.failure_count > 0 and counts.success_count == 0)
        or counts.open_incident_count >= 2
    ):
        return "unhealthy"
    if counts.failure_count > 0 or counts.open_incident_count > 0:
        return "degraded"
    return "healthy"


def _latest(values: list[ExecutionHistory], success: bool) -> datetime | None:
    matching = [item for item in values if item.is_success is success]
    if not matching:
        return None
    return max(execution_time(item) for item in matching)


def build_workflow_health(
    history: list[ExecutionHistory], incidents: list[Incident]
) -> list[WorkflowHealth]:
    by_workflow: dict[str, list[ExecutionHistory]] = defaultdict(list)
    for item in history:
        by_workflow[item.workflow_id].append(item)
    open_counts: dict[str, int] = defaultdict(int)
    for incident in incidents:
        if incident.status == "open":
            open_counts[incident.workflow_id] += 1

    result = []
    for workflow_id, values in by_workflow.items():
        recent = sorted(values, key=execution_time, reverse=True)[:RECENT_EXECUTIONS_PER_WORKFLOW]
        success_count = sum(item.is_success is True for item in recent)
        failure_count = sum(item.is_success is False for item in recent)
        classified = success_count + failure_count
        counts = HealthCounts(
            execution_count=len(recent),
            success_count=success_count,
            failure_count=failure_count,
            open_incident_count=open_counts[workflow_id],
        )
        newest = recent[0]
        known_names = [
            item.workflow_name for item in recent if item.workflow_name != "Unknown workflow"
        ]
        result.append(
            WorkflowHealth(
                workflow_id=workflow_id,
                workflow_name=known_names[0] if known_names else newest.workflow_name,
                health=classify_health(counts),
                last_checked_at=max(as_utc(item.recorded_at) for item in values),
                last_successful_execution_at=_latest(recent, True),
                last_failed_execution_at=_latest(recent, False),
                recent_executions_count=len(recent),
                recent_success_count=success_count,
                recent_failure_count=failure_count,
                success_rate=success_count / classified if classified else None,
                open_incident_count=open_counts[workflow_id],
            )
        )
    return sorted(result, key=lambda item: (item.health == "unknown", item.workflow_name.lower()))


def global_recent_counts(history: list[ExecutionHistory]) -> tuple[int, int]:
    recent = sorted(history, key=execution_time, reverse=True)[:RECENT_EXECUTIONS_GLOBAL]
    return (
        sum(item.is_success is True for item in recent),
        sum(item.is_success is False for item in recent),
    )

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.incident import ExecutionHistory, Incident
from app.schemas.domain import WorkflowHealth
from app.services.health import (
    RECENT_EXECUTIONS_GLOBAL,
    RECENT_EXECUTIONS_PER_WORKFLOW,
    HealthCounts,
    as_utc,
    classify_health,
)


def workflow_health_rows(
    session: Session,
    workflow_id: str | None = None,
) -> list[WorkflowHealth]:
    effective_time = func.coalesce(
        ExecutionHistory.started_at,
        ExecutionHistory.stopped_at,
        ExecutionHistory.recorded_at,
    )

    history_query = select(
        ExecutionHistory.workflow_id.label("workflow_id"),
        ExecutionHistory.workflow_name.label("workflow_name"),
        ExecutionHistory.is_success.label("is_success"),
        ExecutionHistory.recorded_at.label("recorded_at"),
        effective_time.label("execution_time"),
        func.row_number()
        .over(
            partition_by=ExecutionHistory.workflow_id,
            order_by=(effective_time.desc(), ExecutionHistory.id.desc()),
        )
        .label("execution_rank"),
    )

    if workflow_id is not None:
        history_query = history_query.where(ExecutionHistory.workflow_id == workflow_id)

    ranked = history_query.subquery("ranked_executions")

    recent_query = select(
        ranked.c.workflow_id,
        ranked.c.workflow_name,
        ranked.c.is_success,
        ranked.c.recorded_at,
        ranked.c.execution_time,
        func.row_number()
        .over(
            partition_by=ranked.c.workflow_id,
            order_by=(
                case(
                    (ranked.c.workflow_name != "Unknown workflow", 0),
                    else_=1,
                ),
                ranked.c.execution_rank,
            ),
        )
        .label("name_rank"),
    ).where(ranked.c.execution_rank <= RECENT_EXECUTIONS_PER_WORKFLOW)

    recent = recent_query.subquery("recent_executions")

    aggregates = (
        select(
            recent.c.workflow_id,
            func.max(
                case(
                    (recent.c.name_rank == 1, recent.c.workflow_name),
                    else_=None,
                )
            ).label("workflow_name"),
            func.count().label("execution_count"),
            func.sum(case((recent.c.is_success.is_(True), 1), else_=0)).label("success_count"),
            func.sum(case((recent.c.is_success.is_(False), 1), else_=0)).label("failure_count"),
            func.max(
                case(
                    (recent.c.is_success.is_(True), recent.c.execution_time),
                    else_=None,
                )
            ).label("last_successful_execution_at"),
            func.max(
                case(
                    (recent.c.is_success.is_(False), recent.c.execution_time),
                    else_=None,
                )
            ).label("last_failed_execution_at"),
        )
        .group_by(recent.c.workflow_id)
        .subquery("workflow_aggregates")
    )

    last_checked_query = select(
        ExecutionHistory.workflow_id.label("workflow_id"),
        func.max(ExecutionHistory.recorded_at).label("last_checked_at"),
    ).group_by(ExecutionHistory.workflow_id)

    if workflow_id is not None:
        last_checked_query = last_checked_query.where(ExecutionHistory.workflow_id == workflow_id)

    last_checked = last_checked_query.subquery("last_checked")

    open_incidents_query = (
        select(
            Incident.workflow_id.label("workflow_id"),
            func.count().label("open_incident_count"),
        )
        .where(Incident.status == "open")
        .group_by(Incident.workflow_id)
    )

    if workflow_id is not None:
        open_incidents_query = open_incidents_query.where(Incident.workflow_id == workflow_id)

    open_incidents = open_incidents_query.subquery("open_incidents")

    query = (
        select(
            aggregates,
            last_checked.c.last_checked_at,
            func.coalesce(open_incidents.c.open_incident_count, 0).label("open_incident_count"),
        )
        .join(
            last_checked,
            last_checked.c.workflow_id == aggregates.c.workflow_id,
        )
        .outerjoin(
            open_incidents,
            open_incidents.c.workflow_id == aggregates.c.workflow_id,
        )
    )

    rows = session.execute(query).all()
    result = []

    for row in rows:
        success_count = int(row.success_count or 0)
        failure_count = int(row.failure_count or 0)
        execution_count = int(row.execution_count or 0)
        incident_count = int(row.open_incident_count or 0)
        classified = success_count + failure_count

        result.append(
            WorkflowHealth(
                workflow_id=row.workflow_id,
                workflow_name=row.workflow_name or "Unknown workflow",
                health=classify_health(
                    HealthCounts(
                        execution_count=execution_count,
                        success_count=success_count,
                        failure_count=failure_count,
                        open_incident_count=incident_count,
                    )
                ),
                last_checked_at=as_utc(row.last_checked_at),
                last_successful_execution_at=(
                    as_utc(row.last_successful_execution_at)
                    if row.last_successful_execution_at is not None
                    else None
                ),
                last_failed_execution_at=(
                    as_utc(row.last_failed_execution_at)
                    if row.last_failed_execution_at is not None
                    else None
                ),
                recent_executions_count=execution_count,
                recent_success_count=success_count,
                recent_failure_count=failure_count,
                success_rate=success_count / classified if classified else None,
                open_incident_count=incident_count,
            )
        )

    return sorted(
        result,
        key=lambda item: (item.health == "unknown", item.workflow_name.lower()),
    )


def global_recent_counts(session: Session) -> tuple[int, int]:
    effective_time = func.coalesce(
        ExecutionHistory.started_at,
        ExecutionHistory.stopped_at,
        ExecutionHistory.recorded_at,
    )

    ranked = select(
        ExecutionHistory.is_success.label("is_success"),
        func.row_number()
        .over(order_by=(effective_time.desc(), ExecutionHistory.id.desc()))
        .label("execution_rank"),
    ).subquery("global_ranked_executions")

    query = select(
        func.coalesce(
            func.sum(case((ranked.c.is_success.is_(True), 1), else_=0)),
            0,
        ).label("success_count"),
        func.coalesce(
            func.sum(case((ranked.c.is_success.is_(False), 1), else_=0)),
            0,
        ).label("failure_count"),
    ).where(ranked.c.execution_rank <= RECENT_EXECUTIONS_GLOBAL)

    row = session.execute(query).one()
    return int(row.success_count), int(row.failure_count)


def open_incident_count(session: Session) -> int:
    return int(
        session.scalar(select(func.count()).select_from(Incident).where(Incident.status == "open"))
        or 0
    )

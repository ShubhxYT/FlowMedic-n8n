import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.ai.providers import CompatibleAIProvider, MockDiagnosisProvider
from app.api.dependencies import current_context
from app.core.errors import ServiceError
from app.core.security import sanitize
from app.core.workspaces import WorkspaceFactory
from app.models.connections import AIProvider, N8nInstance, WorkspaceConnectionState
from app.models.incident import AlertRule, ExecutionHistory, Incident
from app.repositories.alerts import AlertEventRepository, AlertRuleRepository
from app.repositories.connections import ConnectionRepository
from app.repositories.incidents import IncidentRepository
from app.schemas.domain import (
    AlertDeliveryAttemptPage,
    AlertEventPage,
    AlertRuleCreate,
    AlertRuleOut,
    AlertRulePage,
    AlertRuleUpdate,
    DemoInfo,
    Failure,
    FailurePage,
    IncidentOut,
    IncidentPage,
    InstanceConfigurationStatus,
    MonitoringStatus,
    MonitoringStatusPage,
    MonitoringSyncResult,
    OverviewMetrics,
    SyncResult,
    SystemStatus,
    Workflow,
    WorkflowHealth,
    WorkflowHealthPage,
    WorkflowPage,
)
from app.services.connection_registry import read_default_ai, snapshot_guard
from app.services.health import build_workflow_health, global_recent_counts
from app.services.incidents import normalize

router = APIRouter(prefix="/api/v1", dependencies=[Depends(current_context)])
logger = logging.getLogger(__name__)
Limit = Annotated[int, Query(ge=1, le=100)]
Cursor = Annotated[str | None, Query(max_length=2048)]
OptionalInstance = Annotated[str | None, Query(max_length=36)]


def get_session(request: Request):
    context = request.state.auth
    with WorkspaceFactory(
        request.app.state.session_factory, context.workspace_id, context.user_id
    )() as session:
        yield session


DB = Annotated[Session, Depends(get_session)]


def require_admin(request: Request):
    if request.state.auth.role != "admin":
        raise ServiceError("forbidden", "Workspace admin permission is required", 403)


def _uuid(value, *, code="instance_not_found"):
    try:
        UUID(value)
    except ValueError:
        raise ServiceError(code, "Object was not found", 404) from None
    return value


def instance_row(
    session: Session,
    workspace_id: str,
    instance_id: str,
    *,
    include_deleted: bool = False,
    require_enabled: bool = False,
) -> N8nInstance:
    _uuid(instance_id)
    statement = select(N8nInstance).where(
        N8nInstance.workspace_id == workspace_id,
        N8nInstance.id == instance_id,
    )
    if not include_deleted:
        statement = statement.where(N8nInstance.deleted.is_(False))
    if require_enabled:
        statement = statement.where(N8nInstance.enabled.is_(True))
    row = session.scalar(statement)
    if row is None:
        raise ServiceError("instance_not_found", "n8n instance was not found", 404)
    return row


def required_instance(request: Request, session: DB, instance_id: Annotated[UUID, Query()]):
    row = instance_row(
        session, request.state.auth.workspace_id, str(instance_id), require_enabled=True
    )
    session.commit()
    return row


RequiredInstance = Annotated[N8nInstance, Depends(required_instance)]


def historical_instance(request: Request, session: DB, instance_id: Annotated[UUID, Query()]):
    return instance_row(
        session, request.state.auth.workspace_id, str(instance_id), include_deleted=True
    )


HistoricalInstance = Annotated[N8nInstance, Depends(historical_instance)]


def optional_instance(request: Request, session: Session) -> N8nInstance | None:
    value = request.query_params.get("instance_id")
    if not value:
        return None
    return instance_row(session, request.state.auth.workspace_id, value, include_deleted=True)


def worker_for(request: Request, instance_id: str):
    worker = request.app.state.registry.get(request.state.auth.workspace_id, instance_id)
    if worker is None:
        raise ServiceError("instance_not_ready", "This n8n instance is not active", 503)
    return worker


def secrets(request):
    config = request.app.state.settings
    return (config.flowmedic_api_key.get_secret_value(),)


@router.get("/n8n/status")
async def n8n_status(request: Request, row: RequiredInstance) -> dict[str, bool]:
    return await worker_for(request, row.id).n8n.connectivity()


@router.get("/system/status", response_model=SystemStatus)
def system_status(request: Request, session: DB) -> SystemStatus:
    config = request.app.state.settings
    workspace_id = request.state.auth.workspace_id
    rows = session.scalars(select(N8nInstance).where(N8nInstance.deleted.is_(False))).all()
    enabled = sum(row.enabled for row in rows)
    state = session.get(WorkspaceConnectionState, workspace_id)
    provider = session.scalar(
        select(AIProvider).where(
            AIProvider.workspace_id == workspace_id,
            AIProvider.id == (state.default_ai_provider_id if state else None),
        )
    )
    return SystemStatus(
        n8n_configured=bool(enabled),
        enabled_n8n_count=enabled,
        n8n_instances=[
            InstanceConfigurationStatus(
                instance_id=row.id,
                instance_name=row.name,
                state=configuration_state(request, session, "n8n", row),
            )
            for row in rows
        ],
        ai_mode="configured" if provider and provider.kind == "openai-compatible" else "mock",
        ai_state=configuration_state(request, session, "ai", provider),
        database_engine=config.database_url.split(":", 1)[0].lower(),
        poll_interval_seconds=config.monitor_poll_interval_seconds,
        poll_concurrency=config.connection_poll_concurrency,
        lease_seconds=config.monitor_lease_seconds,
        retention_days=config.execution_history_retention_days,
    )


def configuration_state(request, session, kind, row):
    if row is None:
        return "not_configured"
    if row.deleted or not row.enabled:
        return "disabled"
    cipher = request.app.state.connection_cipher
    if cipher is None:
        return "configuration_error"
    try:
        ConnectionRepository(session, session.workspace_id, cipher).snapshot(kind, row.id)
    except ServiceError:
        return "configuration_error"
    return "ready"


def status_for(session: Session, row: N8nInstance, request: Request) -> MonitoringStatus:
    worker = request.app.state.registry.get(request.state.auth.workspace_id, row.id)
    if worker is not None:
        return worker.status().model_copy(
            update={
                "instance_name": row.name,
                "enabled": bool(row.enabled and not row.deleted),
                "configuration_state": configuration_state(request, session, "n8n", row),
            }
        )
    return MonitoringStatus(
        workspace_id=row.workspace_id,
        instance_id=row.id,
        instance_name=row.name,
        configuration_state=configuration_state(request, session, "n8n", row),
        enabled=bool(row.enabled),
        state="disabled" if not row.enabled else "idle",
        poll_interval_seconds=request.app.state.settings.monitor_poll_interval_seconds,
        retention_days=request.app.state.settings.execution_history_retention_days,
    )


@router.get("/monitoring/status", response_model=MonitoringStatusPage)
def monitoring_status(request: Request, session: DB, instance_id: OptionalInstance = None):
    workspace_id = request.state.auth.workspace_id
    if instance_id:
        row = instance_row(session, workspace_id, instance_id, include_deleted=True)
        return MonitoringStatusPage(items=[status_for(session, row, request)])
    rows = session.scalars(
        select(N8nInstance)
        .where(
            N8nInstance.workspace_id == workspace_id,
            N8nInstance.deleted.is_(False),
        )
        .order_by(N8nInstance.created_at, N8nInstance.id)
    ).all()
    return MonitoringStatusPage(items=[status_for(session, row, request) for row in rows])


@router.post(
    "/monitoring/sync", response_model=MonitoringSyncResult, dependencies=[Depends(require_admin)]
)
async def monitoring_sync(request: Request, row: RequiredInstance):
    require_admin(request)
    return await worker_for(request, row.id).sync()


def workflow_health_rows(session: Session, instance_id: str | None = None):
    history = session.scalars(
        select(ExecutionHistory).where(
            ExecutionHistory.instance_id == instance_id if instance_id else True
        )
    ).all()
    incidents = session.scalars(
        select(Incident).where(Incident.instance_id == instance_id if instance_id else True)
    ).all()
    return build_workflow_health(history, incidents)


@router.get("/metrics/overview", response_model=OverviewMetrics)
def overview_metrics(
    request: Request, session: DB, instance_id: OptionalInstance = None
) -> OverviewMetrics:
    row = (
        instance_row(session, request.state.auth.workspace_id, instance_id, include_deleted=True)
        if instance_id
        else None
    )
    workflows = workflow_health_rows(session, instance_id)
    history = session.scalars(
        select(ExecutionHistory).where(
            ExecutionHistory.instance_id == instance_id if instance_id else True
        )
    ).all()
    successes, failures = global_recent_counts(history)
    monitoring = (
        status_for(session, row, request)
        if row is not None
        else MonitoringStatus(
            workspace_id=request.state.auth.workspace_id,
            enabled=False,
            state="disabled",
            poll_interval_seconds=request.app.state.settings.monitor_poll_interval_seconds,
            retention_days=request.app.state.settings.execution_history_retention_days,
        )
    )
    incidents = session.scalars(
        select(Incident).where(Incident.instance_id == instance_id if instance_id else True)
    ).all()
    if row is None:
        statuses = [
            status_for(session, instance, request)
            for instance in session.scalars(
                select(N8nInstance).where(N8nInstance.deleted.is_(False))
            )
        ]
        successful = [
            item.last_successful_sync_at for item in statuses if item.last_successful_sync_at
        ]
        monitoring = monitoring.model_copy(
            update={
                "last_successful_sync_at": max(successful) if successful else None,
                "enabled": any(item.enabled for item in statuses),
                "state": next(
                    (
                        state
                        for state in ("degraded", "running", "idle")
                        if any(item.enabled and item.state == state for item in statuses)
                    ),
                    "disabled",
                ),
                "configuration_state": next(
                    (
                        state
                        for state in ("configuration_error", "ready", "disabled")
                        if any(item.configuration_state == state for item in statuses)
                    ),
                    "not_configured",
                ),
                "backfill_pending": any(item.backfill_pending for item in statuses),
            }
        )
    return OverviewMetrics(
        monitored_workflow_count=len(workflows),
        healthy_workflow_count=sum(item.health == "healthy" for item in workflows),
        degraded_workflow_count=sum(item.health == "degraded" for item in workflows),
        unhealthy_workflow_count=sum(item.health == "unhealthy" for item in workflows),
        unknown_workflow_count=sum(item.health == "unknown" for item in workflows),
        open_incident_count=sum(item.status == "open" for item in incidents),
        recent_failure_count=failures,
        recent_success_count=successes,
        last_successful_monitoring_sync_at=monitoring.last_successful_sync_at,
        monitoring=monitoring,
    )


@router.get("/metrics/workflows", response_model=WorkflowHealthPage)
def workflow_metrics(session: DB, instance_id: OptionalInstance = None) -> WorkflowHealthPage:
    if instance_id:
        instance_row(session, session.workspace_id, instance_id, include_deleted=True)
    return WorkflowHealthPage(data=workflow_health_rows(session, instance_id))


@router.get("/workflows/{workflow_id}/health", response_model=WorkflowHealth)
def workflow_health(workflow_id: str, session: DB, row: HistoricalInstance) -> WorkflowHealth:
    if len(workflow_id) > 255:
        raise ServiceError("workflow_not_found", "Workflow health was not found", 404)
    for health in workflow_health_rows(session, row.id):
        if health.workflow_id == workflow_id:
            return health
    raise ServiceError("workflow_not_found", "Workflow health was not found", 404)


@router.get("/workflows", response_model=WorkflowPage)
async def workflows(
    request: Request, row: RequiredInstance, limit: Limit = 50, cursor: Cursor = None
):
    worker = worker_for(request, row.id)
    page = await worker.n8n.workflows(limit, cursor)
    return WorkflowPage(
        data=[
            Workflow(
                id=w.id,
                workspace_id=row.workspace_id,
                instance_id=row.id,
                name=sanitize(w.name, worker._secrets()),
                active=w.active,
            )
            for w in page.data
        ],
        next_cursor=page.nextCursor,
    )


@router.get("/executions/failed", response_model=FailurePage)
async def failed_executions(
    request: Request, row: RequiredInstance, limit: Limit = 50, cursor: Cursor = None
):
    worker = worker_for(request, row.id)
    failures, next_cursor, _ = await worker.n8n.failed_executions(limit, cursor)
    return FailurePage(
        data=[normalize(item, worker._secrets()) for item in failures],
        next_cursor=next_cursor,
    )


@router.post("/incidents/sync", response_model=SyncResult, dependencies=[Depends(require_admin)])
async def sync_incidents(request: Request, row: RequiredInstance):
    require_admin(request)
    result = await worker_for(request, row.id).sync()
    return SyncResult(
        scanned=result.executions_discovered,
        created=result.incidents_created,
        next_cursor=result.next_cursor,
    )


@router.get("/alerts/rules", response_model=AlertRulePage)
def alert_rules(session: DB) -> AlertRulePage:
    return AlertRulePage(data=AlertRuleRepository(session).list_rules())


@router.post("/alerts/rules", response_model=AlertRuleOut, status_code=201)
def create_alert_rule(
    request: Request, payload: AlertRuleCreate, session: DB, instance_id: OptionalInstance = None
) -> AlertRuleOut:
    require_admin(request)
    if instance_id:
        instance_row(session, request.state.auth.workspace_id, instance_id)
    existing = session.scalar(select(AlertRule).where(AlertRule.name == payload.name))
    if existing is not None:
        raise ServiceError("alert_rule_exists", "An alert rule with this name already exists", 409)
    if payload.delivery_provider == "webhook" and not (
        request.app.state.alert_providers.get("webhook")
        and not request.app.state.settings.demo_mode
    ):
        raise ServiceError(
            "alert_provider_not_configured",
            "The selected alert delivery provider is not configured",
            422,
        )
    values = payload.model_dump()
    if values["cooldown_seconds"] is None:
        values["cooldown_seconds"] = request.app.state.settings.alert_default_cooldown_seconds
    values["instance_id"] = instance_id
    rule = AlertRuleRepository(session).create(**values)
    session.commit()
    return rule


@router.patch("/alerts/rules/{rule_id}", response_model=AlertRuleOut)
def update_alert_rule(
    request: Request, rule_id: UUID, payload: AlertRuleUpdate, session: DB
) -> AlertRuleOut:
    require_admin(request)
    rule = AlertRuleRepository(session).get(str(rule_id))
    if rule is None:
        raise ServiceError("alert_rule_not_found", "Alert rule was not found", 404)
    values = payload.model_dump(exclude_unset=True)
    if "name" in values:
        duplicate = session.scalar(select(AlertRule).where(AlertRule.name == values["name"]))
        if duplicate is not None and duplicate.id != rule.id:
            raise ServiceError(
                "alert_rule_exists", "An alert rule with this name already exists", 409
            )
    if (
        values.get("enabled")
        and rule.delivery_provider == "webhook"
        and not (
            request.app.state.alert_providers.get("webhook")
            and not request.app.state.settings.demo_mode
        )
    ):
        raise ServiceError(
            "alert_provider_not_configured",
            "The selected alert delivery provider is not configured",
            422,
        )
    AlertRuleRepository(session).update(rule, **values)
    session.commit()
    return rule


@router.get("/alerts/events", response_model=AlertEventPage)
def alert_events(
    session: DB,
    limit: Limit = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    instance_id: OptionalInstance = None,
) -> AlertEventPage:
    if instance_id:
        instance_row(session, session.workspace_id, instance_id, include_deleted=True)
    return AlertEventPage(
        data=AlertEventRepository(session).list_events(limit, offset, instance_id)
    )


@router.get("/alerts/events/{event_id}/deliveries", response_model=AlertDeliveryAttemptPage)
def alert_deliveries(event_id: UUID, session: DB) -> AlertDeliveryAttemptPage:
    events = AlertEventRepository(session)
    if events.get(str(event_id)) is None:
        raise ServiceError("alert_event_not_found", "Alert event was not found", 404)
    return AlertDeliveryAttemptPage(data=events.deliveries(str(event_id)))


@router.get("/demo", response_model=DemoInfo)
def demo_info(request: Request):
    if not request.app.state.settings.demo_mode:
        raise ServiceError("demo_not_enabled", "Demo mode is disabled", 404)
    return DemoInfo(
        mode="synthetic",
        incident_id=request.app.state.demo_incident_id,
        diagnosis_provider="mock",
        note="Synthetic data only; no n8n instance or AI provider was contacted.",
    )


@router.get("/incidents", response_model=IncidentPage)
def incidents(
    session: DB,
    limit: Limit = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    instance_id: OptionalInstance = None,
):
    if instance_id:
        instance_row(session, session.workspace_id, instance_id, include_deleted=True)
    return IncidentPage(
        data=IncidentRepository(session, session.workspace_id, instance_id).list(limit, offset)
    )


def require_incident(session, incident_id):
    incident = IncidentRepository(session, session.workspace_id).get(str(incident_id))
    if incident is None:
        raise ServiceError("incident_not_found", "Incident was not found", 404)
    return incident


@router.get("/incidents/{incident_id}", response_model=IncidentOut)
def incident(incident_id: UUID, session: DB):
    return require_incident(session, incident_id)


def diagnosis_provider(request: Request, workspace_id: str):
    cipher = request.app.state.connection_cipher
    if cipher is None:
        return MockDiagnosisProvider(), None
    snapshot = read_default_ai(request.app.state.session_factory, cipher, workspace_id)
    if snapshot is None:
        return MockDiagnosisProvider(), None
    guard = snapshot_guard(request.app.state.session_factory, AIProvider, snapshot)
    if snapshot.kind == "mock":
        return MockDiagnosisProvider(), snapshot
    transient = list(secrets(request))
    with request.app.state.session_factory() as db:
        repo = ConnectionRepository(db, workspace_id, cipher)
        for kind in ("n8n", "ai"):
            for row in repo.enabled(kind):
                try:
                    transient.append(repo.snapshot(kind, row.id).api_key.get_secret_value())
                except ServiceError:
                    continue
    return CompatibleAIProvider(
        snapshot, request.app.state.ai_outbound, guard, transient_secrets=tuple(transient)
    ), snapshot


@router.post("/incidents/{incident_id}/diagnose", response_model=IncidentOut)
async def diagnose(incident_id: UUID, request: Request, session: DB, force: bool = False):
    require_admin(request)
    incident = require_incident(session, incident_id)
    if incident.diagnosis is not None and not force:
        raise ServiceError(
            "already_diagnosed",
            "Incident already has a diagnosis; pass force=true to re-diagnose",
            409,
        )
    caller = request.state.auth.user_id or (
        request.client.host if request.client else "unknown"
    )
    if not request.app.state.diagnose_limiter.allow(caller):
        raise ServiceError("rate_limited", "Too many diagnosis requests; try again shortly", 429)
    provider, snapshot = diagnosis_provider(request, session.workspace_id)
    context = Failure.model_validate(incident, from_attributes=True)
    session.commit()
    diagnosis = await provider.diagnose(context)
    if snapshot is not None:
        current = session.execute(
            update(AIProvider)
            .where(
                AIProvider.workspace_id == session.workspace_id,
                AIProvider.id == snapshot.id,
                AIProvider.revision == snapshot.revision,
                AIProvider.enabled.is_(True),
                AIProvider.deleted.is_(False),
            )
            .values(revision=AIProvider.revision)
        )
        if current.rowcount != 1:
            raise ServiceError("connection_changed", "Connection changed; retry the operation", 409)
    fresh = require_incident(session, incident_id)
    fresh.diagnosis = diagnosis.model_dump()
    fresh.diagnosis_provider = provider.name
    fresh.status = "diagnosed"
    session.commit()
    return fresh

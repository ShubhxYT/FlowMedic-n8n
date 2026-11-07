"""Mandatory, immutable ownership scope for all business persistence."""

from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session, sessionmaker, with_loader_criteria

from app.models.audit import AuditEvent
from app.models.auth import DEMO_WORKSPACE_ID, LEGACY_WORKSPACE_ID, Workspace
from app.models.incident import (
    AlertConditionState,
    AlertDeliveryAttempt,
    AlertEvent,
    AlertRule,
    ExecutionHistory,
    Incident,
    MonitoringCheckpoint,
)

OWNED = (
    Incident,
    ExecutionHistory,
    MonitoringCheckpoint,
    AlertRule,
    AlertEvent,
    AlertConditionState,
)


class WorkspaceSession(Session):
    def __init__(self, *args, workspace_id, actor_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.__workspace_id = workspace_id
        self.actor_id = actor_id

    @property
    def workspace_id(self):
        return self.__workspace_id

    # Legacy bulk APIs bypass ownership checks and are deliberately unsupported.
    def bulk_save_objects(self, *args, **kwargs):
        raise RuntimeError("Use normal scoped ORM persistence")

    def bulk_insert_mappings(self, *args, **kwargs):
        raise RuntimeError("Use normal scoped ORM persistence")

    def bulk_update_mappings(self, *args, **kwargs):
        raise RuntimeError("Use normal scoped ORM persistence")


class WorkspaceFactory:
    def __init__(self, factory, workspace_id, actor_id=None):
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self._factory = sessionmaker(
            factory.kw["bind"],
            class_=WorkspaceSession,
            expire_on_commit=False,
            workspace_id=workspace_id,
            actor_id=actor_id,
        )

    def __call__(self):
        return self._factory()


def require_workspace(session):
    if not isinstance(session, WorkspaceSession):
        raise RuntimeError("Business repositories require an explicit workspace session")
    return session.workspace_id


def require_environment_factory(factory):
    if not isinstance(factory, WorkspaceFactory) or factory.workspace_id not in {
        LEGACY_WORKSPACE_ID,
        DEMO_WORKSPACE_ID,
    }:
        raise RuntimeError("Environment integration services require their reserved workspace")
    return factory.workspace_id


def ensure_reserved_workspaces(factory):
    with factory() as session:
        for wid, name in [
            (LEGACY_WORKSPACE_ID, "Legacy operator"),
            (DEMO_WORKSPACE_ID, "Synthetic demo"),
        ]:
            if session.get(Workspace, wid) is None:
                session.add(Workspace(id=wid, name=name))
        session.commit()


@event.listens_for(WorkspaceSession, "do_orm_execute")
def scope_statement(state):
    if not state.is_orm_statement or state.is_insert:
        raise RuntimeError(
            "Raw SQL, Core statements and bulk inserts are forbidden in workspace sessions"
        )
    if state.is_update:
        mapper = state.bind_mapper
        if mapper is None or mapper.class_ not in OWNED:
            raise RuntimeError("Bulk updates are allowed only on owned business models")
        allowed = {
            "lease_owner_id",
            "lease_acquired_at",
            "lease_heartbeat_at",
            "lease_expires_at",
            "updated_at",
        }
        changed_columns = {column.key for column in state.statement._values}
        if mapper.class_ is not MonitoringCheckpoint or not changed_columns <= allowed:
            raise RuntimeError("Use normal ORM persistence for ownership-sensitive updates")
    if state.is_delete and (state.bind_mapper is None or state.bind_mapper.class_ not in OWNED):
        raise RuntimeError("Bulk deletion requires an owned model")
    if not (state.is_select or state.is_update or state.is_delete):
        raise RuntimeError("Unsupported business statement")
    wid = state.session.workspace_id
    statement = state.statement
    for model in OWNED:
        statement = statement.options(
            with_loader_criteria(model, model.workspace_id == wid, include_aliases=True)
        )
    statement = statement.options(
        with_loader_criteria(
            AlertDeliveryAttempt,
            AlertDeliveryAttempt.alert_event_id.in_(
                select(AlertEvent.id).where(AlertEvent.workspace_id == wid)
            ),
            include_aliases=True,
        )
    )
    state.statement = statement
    if state.is_update or state.is_delete:
        state.session.add(
            AuditEvent(
                actor_id=state.session.actor_id,
                workspace_id=wid,
                action="business.bulk_mutation",
                outcome="success",
            )
        )


def check_parent(session, model, parent_id):
    if parent_id is None:
        return
    with session.no_autoflush:
        row = session.get(model, parent_id)
        if row is None:
            # Parent may have been added in the same flush, with an explicit ID.
            row = next(
                (obj for obj in session.new if isinstance(obj, model) and obj.id == parent_id), None
            )
        if row is None or getattr(row, "workspace_id", None) != session.workspace_id:
            raise ValueError("Related object is outside the workspace")


@event.listens_for(WorkspaceSession, "before_flush")
def scope_objects(session, flush_context, instances):
    changed = list(session.new) + list(session.dirty) + list(session.deleted)
    for obj in changed:
        if isinstance(obj, OWNED):
            if obj in session.new and obj.workspace_id is None:
                obj.workspace_id = session.workspace_id
            if obj.workspace_id != session.workspace_id:
                raise ValueError("Object is outside the workspace")
            if obj not in session.new and inspect(obj).attrs.workspace_id.history.has_changes():
                raise ValueError("Workspace ownership cannot change")
    for obj in changed:
        if isinstance(obj, ExecutionHistory):
            check_parent(session, Incident, obj.incident_id)
        elif isinstance(obj, AlertEvent):
            check_parent(session, AlertRule, obj.rule_id)
            check_parent(session, Incident, obj.incident_id)
        elif isinstance(obj, AlertDeliveryAttempt):
            check_parent(session, AlertEvent, obj.alert_event_id)
        elif not isinstance(obj, (*OWNED, AuditEvent)):
            raise ValueError("Identity objects cannot be changed through a business session")
    for obj in changed:
        if isinstance(obj, (*OWNED, AlertDeliveryAttempt)):
            session.add(
                AuditEvent(
                    actor_id=session.actor_id,
                    workspace_id=session.workspace_id,
                    action=f"business.{obj.__tablename__}.write",
                    outcome="success",
                )
            )


@event.listens_for(Session, "before_flush")
def append_only_audit(session, flush_context, instances):
    if any(isinstance(obj, AuditEvent) for obj in list(session.dirty) + list(session.deleted)):
        raise ValueError("Audit events are append-only")

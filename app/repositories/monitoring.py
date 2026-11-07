from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ServiceError
from app.core.workspaces import require_workspace
from app.models.connections import N8nInstance
from app.models.incident import ExecutionHistory, Incident, MonitoringCheckpoint


def utc_now() -> datetime:
    return datetime.now(UTC)


def is_expired(value: datetime | None, now: datetime | None = None) -> bool:
    if value is None:
        return False
    comparable = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return comparable <= (now or utc_now())


class MonitoringRepository:
    def __init__(self, session: Session, workspace_id: str, instance_id: str):
        self.workspace_id = require_workspace(session)
        if workspace_id != self.workspace_id:
            raise ValueError("Monitoring session is outside the workspace")
        self.instance_id = instance_id
        self.session = session

    def checkpoint(self, source: str) -> MonitoringCheckpoint:
        checkpoint = self.existing_checkpoint(source)
        if checkpoint is not None:
            return checkpoint
        now = utc_now()
        checkpoint = MonitoringCheckpoint(
            workspace_id=self.workspace_id,
            instance_id=self.instance_id,
            source=source,
            source_identifier=self.instance_id,
            created_at=now,
            updated_at=now,
        )
        try:
            with self.session.begin_nested():
                self.session.add(checkpoint)
                self.session.flush()
        except IntegrityError:
            checkpoint = self.existing_checkpoint(source)
            if checkpoint is None:
                raise
        return checkpoint

    def existing_checkpoint(self, source: str, source_identifier: str | None = None):
        return self.session.scalar(
            select(MonitoringCheckpoint).where(
                MonitoringCheckpoint.workspace_id == self.workspace_id,
                MonitoringCheckpoint.instance_id == self.instance_id,
                MonitoringCheckpoint.source == source,
            )
        )

    def acquire_lease(self, source: str, owner_id: str, lease_seconds: float):
        """Acquire the instance lease atomically; returns (acquired, recovered)."""
        checkpoint = self.checkpoint(source)
        now = utc_now()
        previous_expiry = checkpoint.lease_expires_at
        result = self.session.execute(
            update(MonitoringCheckpoint)
            .where(
                MonitoringCheckpoint.workspace_id == self.workspace_id,
                MonitoringCheckpoint.instance_id == self.instance_id,
                MonitoringCheckpoint.source == source,
                MonitoringCheckpoint.id == checkpoint.id,
                or_(
                    MonitoringCheckpoint.lease_owner_id == owner_id,
                    MonitoringCheckpoint.lease_expires_at.is_(None),
                    MonitoringCheckpoint.lease_expires_at <= now,
                ),
            )
            .values(
                lease_owner_id=owner_id,
                lease_acquired_at=now,
                lease_heartbeat_at=now,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                updated_at=now,
            ),
            execution_options={"synchronize_session": False},
        )
        self.session.commit()
        return result.rowcount == 1, is_expired(previous_expiry, now)

    def heartbeat_lease(self, source: str, owner_id: str, lease_seconds: float) -> bool:
        checkpoint = self.existing_checkpoint(source)
        if checkpoint is None:
            return False
        now = utc_now()
        result = self.session.execute(
            update(MonitoringCheckpoint)
            .where(
                MonitoringCheckpoint.workspace_id == self.workspace_id,
                MonitoringCheckpoint.instance_id == self.instance_id,
                MonitoringCheckpoint.source == source,
                MonitoringCheckpoint.id == checkpoint.id,
                MonitoringCheckpoint.lease_owner_id == owner_id,
                MonitoringCheckpoint.lease_expires_at > now,
            )
            .values(
                lease_heartbeat_at=now,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                updated_at=now,
            ),
            execution_options={"synchronize_session": False},
        )
        self.session.commit()
        return result.rowcount == 1

    def release_lease(self, source: str, owner_id: str) -> None:
        checkpoint = self.existing_checkpoint(source)
        if checkpoint is None:
            return
        now = utc_now()
        self.session.execute(
            update(MonitoringCheckpoint)
            .where(
                MonitoringCheckpoint.workspace_id == self.workspace_id,
                MonitoringCheckpoint.instance_id == self.instance_id,
                MonitoringCheckpoint.source == source,
                MonitoringCheckpoint.id == checkpoint.id,
                MonitoringCheckpoint.lease_owner_id == owner_id,
            )
            .values(lease_owner_id=None, lease_expires_at=now, updated_at=now),
            execution_options={"synchronize_session": False},
        )
        self.session.commit()


def fence_poll_write(
    session,
    *,
    workspace_id: str,
    instance_id: str,
    revision: int,
    owner_id: str,
    lease_seconds: float,
) -> None:
    """Fail closed unless this connection revision and lease are still current."""
    current = session.execute(
        update(N8nInstance)
        .where(
            N8nInstance.workspace_id == workspace_id,
            N8nInstance.id == instance_id,
            N8nInstance.revision == revision,
            N8nInstance.enabled.is_(True),
            N8nInstance.deleted.is_(False),
        )
        .values(revision=N8nInstance.revision),
        execution_options={"synchronize_session": False},
    )
    if current.rowcount != 1:
        raise ServiceError("connection_changed", "Connection changed; retry the operation", 409)
    now = utc_now()
    owned = session.execute(
        update(MonitoringCheckpoint)
        .where(
            MonitoringCheckpoint.workspace_id == workspace_id,
            MonitoringCheckpoint.instance_id == instance_id,
            MonitoringCheckpoint.source == "n8n",
            MonitoringCheckpoint.lease_owner_id == owner_id,
            MonitoringCheckpoint.lease_expires_at > now,
        )
        .values(
            lease_heartbeat_at=now,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
        ),
        execution_options={"synchronize_session": False},
    )
    if owned.rowcount != 1:
        raise ServiceError("monitoring_lease_lost", "Monitoring ownership changed", 409)


class ExecutionHistoryRepository:
    def __init__(self, session: Session, workspace_id: str, instance_id: str):
        self.workspace_id = require_workspace(session)
        if workspace_id != self.workspace_id:
            raise ValueError("History session is outside the workspace")
        self.instance_id = instance_id
        self.session = session

    def create_once(self, **values) -> tuple[ExecutionHistory, bool]:
        values["workspace_id"] = self.workspace_id
        values["instance_id"] = self.instance_id
        predicate = (
            ExecutionHistory.workspace_id == self.workspace_id,
            ExecutionHistory.instance_id == self.instance_id,
            ExecutionHistory.source == values["source"],
            ExecutionHistory.execution_id == values["execution_id"],
        )
        existing = self.session.scalar(select(ExecutionHistory).where(*predicate))
        if existing is not None:
            terminal = {"success", "error", "crashed", "canceled"}
            if existing.execution_status not in terminal:
                for field in ("execution_status", "is_success", "stopped_at", "started_at"):
                    if values.get(field) is not None:
                        setattr(existing, field, values[field])
            if values.get("workflow_name") != "Unknown workflow":
                existing.workflow_name = values["workflow_name"]
            return existing, False
        history = ExecutionHistory(**values)
        try:
            with self.session.begin_nested():
                self.session.add(history)
                self.session.flush()
        except IntegrityError:
            existing = self.session.scalar(select(ExecutionHistory).where(*predicate))
            if existing is None:
                raise
            return existing, False
        return history, True

    def attach_incident(self, history: ExecutionHistory, incident_id: str) -> None:
        if history.workspace_id != self.workspace_id or history.instance_id != self.instance_id:
            raise ValueError("History is outside the instance scope")
        if history.incident_id is None:
            incident = self.session.scalar(
                select(Incident).where(
                    Incident.id == incident_id,
                    Incident.workspace_id == self.workspace_id,
                    Incident.instance_id == self.instance_id,
                )
            )
            if incident is None:
                raise ValueError("Incident is outside the instance scope")
            history.incident_id = incident_id

    def delete_recorded_before(self, cutoff: datetime, batch_size: int) -> int:
        ids = list(
            self.session.scalars(
                select(ExecutionHistory.id)
                .where(
                    ExecutionHistory.workspace_id == self.workspace_id,
                    ExecutionHistory.instance_id == self.instance_id,
                    ExecutionHistory.recorded_at < cutoff,
                )
                .order_by(ExecutionHistory.recorded_at)
                .limit(batch_size)
            )
        )
        if not ids:
            return 0
        result = self.session.execute(
            delete(ExecutionHistory).where(
                ExecutionHistory.workspace_id == self.workspace_id,
                ExecutionHistory.instance_id == self.instance_id,
                ExecutionHistory.id.in_(ids),
            ),
            execution_options={"synchronize_session": False},
        )
        return int(result.rowcount or 0)

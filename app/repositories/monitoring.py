from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.incident import ExecutionHistory, MonitoringCheckpoint


def utc_now() -> datetime:
    return datetime.now(UTC)


def is_expired(value: datetime | None, now: datetime | None = None) -> bool:
    if value is None:
        return False
    comparable = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return comparable < (now or utc_now())


class MonitoringRepository:
    def __init__(self, session: Session):
        self.session = session

    def checkpoint(self, source: str, source_identifier: str) -> MonitoringCheckpoint:
        checkpoint = self.existing_checkpoint(source, source_identifier)
        if checkpoint is not None:
            return checkpoint
        now = utc_now()
        checkpoint = MonitoringCheckpoint(
            source=source,
            source_identifier=source_identifier,
            created_at=now,
            updated_at=now,
        )
        try:
            with self.session.begin_nested():
                self.session.add(checkpoint)
                self.session.flush()
        except IntegrityError:
            checkpoint = self.existing_checkpoint(source, source_identifier)
            if checkpoint is None:
                raise
        return checkpoint

    def existing_checkpoint(
        self, source: str, source_identifier: str
    ) -> MonitoringCheckpoint | None:
        return self.session.scalar(
            select(MonitoringCheckpoint).where(
                MonitoringCheckpoint.source == source,
                MonitoringCheckpoint.source_identifier == source_identifier,
            )
        )

    def acquire_lease(
        self,
        source: str,
        source_identifier: str,
        owner_id: str,
        lease_seconds: float,
    ) -> tuple[bool, bool]:
        """Acquire the source lease atomically; returns (acquired, stale_lease_recovered)."""
        checkpoint = self.checkpoint(source, source_identifier)
        now = utc_now()
        previous_expiry = checkpoint.lease_expires_at
        expiry = now + timedelta(seconds=lease_seconds)
        result = self.session.execute(
            update(MonitoringCheckpoint)
            .where(
                MonitoringCheckpoint.id == checkpoint.id,
                or_(
                    MonitoringCheckpoint.lease_owner_id == owner_id,
                    MonitoringCheckpoint.lease_expires_at.is_(None),
                    MonitoringCheckpoint.lease_expires_at < now,
                ),
            )
            .values(
                lease_owner_id=owner_id,
                lease_acquired_at=now,
                lease_heartbeat_at=now,
                lease_expires_at=expiry,
                updated_at=now,
            ),
            execution_options={"synchronize_session": False},
        )
        self.session.commit()
        recovered = is_expired(previous_expiry, now)
        return result.rowcount == 1, recovered

    def heartbeat_lease(
        self,
        source: str,
        source_identifier: str,
        owner_id: str,
        lease_seconds: float,
    ) -> bool:
        checkpoint = self.existing_checkpoint(source, source_identifier)
        if checkpoint is None:
            return False
        now = utc_now()
        result = self.session.execute(
            update(MonitoringCheckpoint)
            .where(
                MonitoringCheckpoint.id == checkpoint.id,
                MonitoringCheckpoint.lease_owner_id == owner_id,
                MonitoringCheckpoint.lease_expires_at >= now,
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

    def release_lease(self, source: str, source_identifier: str, owner_id: str) -> None:
        checkpoint = self.existing_checkpoint(source, source_identifier)
        if checkpoint is None:
            return
        now = utc_now()
        self.session.execute(
            update(MonitoringCheckpoint)
            .where(
                MonitoringCheckpoint.id == checkpoint.id,
                MonitoringCheckpoint.lease_owner_id == owner_id,
            )
            .values(lease_owner_id=None, lease_expires_at=now, updated_at=now),
            execution_options={"synchronize_session": False},
        )
        self.session.commit()


class ExecutionHistoryRepository:
    def __init__(self, session: Session):
        self.session = session

    def create_once(self, **values) -> tuple[ExecutionHistory, bool]:
        existing = self.session.scalar(
            select(ExecutionHistory).where(
                ExecutionHistory.source == values["source"],
                ExecutionHistory.execution_id == values["execution_id"],
            )
        )
        if existing is not None:
            # Polls can observe running/waiting executions before their final result.
            # Never regress a terminal result when a stale response arrives later.
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
            existing = self.session.scalar(
                select(ExecutionHistory).where(
                    ExecutionHistory.source == values["source"],
                    ExecutionHistory.execution_id == values["execution_id"],
                )
            )
            if existing is None:
                raise
            return existing, False
        return history, True

    def attach_incident(self, history: ExecutionHistory, incident_id: str) -> None:
        if history.incident_id is None:
            history.incident_id = incident_id

    def delete_recorded_before(self, cutoff: datetime, batch_size: int) -> int:
        ids = list(
            self.session.scalars(
                select(ExecutionHistory.id)
                .where(ExecutionHistory.recorded_at < cutoff)
                .order_by(ExecutionHistory.recorded_at)
                .limit(batch_size)
            )
        )
        if not ids:
            return 0
        result = self.session.execute(delete(ExecutionHistory).where(ExecutionHistory.id.in_(ids)))
        return int(result.rowcount or 0)

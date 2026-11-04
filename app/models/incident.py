import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Incident(Base):
    __tablename__ = "incidents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    execution_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    workflow_id: Mapped[str] = mapped_column(String(255))
    workflow_name: Mapped[str] = mapped_column(String(1000))
    failed_node: Mapped[str | None] = mapped_column(String(1000))
    error_type: Mapped[str] = mapped_column(String(1000))
    error_message: Mapped[str] = mapped_column(Text)
    execution_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="open")
    severity: Mapped[str] = mapped_column(String(20))
    diagnosis: Mapped[dict | None] = mapped_column(JSON)
    diagnosis_provider: Mapped[str | None] = mapped_column(String(50))


class MonitoringCheckpoint(Base):
    __tablename__ = "monitoring_checkpoints"
    __table_args__ = (UniqueConstraint("source", "source_identifier"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source: Mapped[str] = mapped_column(String(50), index=True)
    source_identifier: Mapped[str] = mapped_column(String(255))
    # Kept only for the Phase 3 migration compatibility path. New monitoring uses backfill_cursor.
    cursor_value: Mapped[str | None] = mapped_column(Text)
    backfill_cursor: Mapped[str | None] = mapped_column(Text)
    backfill_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_execution_id: Mapped[str | None] = mapped_column(String(255))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_fresh_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_successful_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_retention_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_summary: Mapped[str | None] = mapped_column(String(500))
    consecutive_failure_count: Mapped[int] = mapped_column(Integer, default=0)
    # The owner identifier remains private to the process and is never returned by the API.
    lease_owner_id: Mapped[str | None] = mapped_column(String(36))
    lease_acquired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ExecutionHistory(Base):
    __tablename__ = "execution_history"
    __table_args__ = (UniqueConstraint("source", "execution_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source: Mapped[str] = mapped_column(String(50), index=True)
    workflow_id: Mapped[str] = mapped_column(String(255), index=True)
    workflow_name: Mapped[str] = mapped_column(String(1000))
    execution_id: Mapped[str] = mapped_column(String(255))
    execution_status: Mapped[str | None] = mapped_column(String(100))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_success: Mapped[bool | None] = mapped_column()
    incident_id: Mapped[str | None] = mapped_column(ForeignKey("incidents.id"), index=True)


class AlertRule(Base):
    __tablename__ = "alert_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255), unique=True)
    trigger_type: Mapped[str] = mapped_column(String(50), index=True)
    enabled: Mapped[bool] = mapped_column(default=False)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=300)
    delivery_provider: Mapped[str] = mapped_column(String(50), default="mock")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AlertEvent(Base):
    __tablename__ = "alert_events"
    __table_args__ = (UniqueConstraint("event_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    rule_id: Mapped[str] = mapped_column(ForeignKey("alert_rules.id"), index=True)
    trigger_type: Mapped[str] = mapped_column(String(50), index=True)
    event_key: Mapped[str] = mapped_column(String(64), unique=True)
    deduplication_key: Mapped[str] = mapped_column(String(255), index=True)
    incident_id: Mapped[str | None] = mapped_column(ForeignKey("incidents.id"), index=True)
    workflow_id: Mapped[str | None] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    cooldown_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_error_summary: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AlertDeliveryAttempt(Base):
    __tablename__ = "alert_delivery_attempts"
    __table_args__ = (UniqueConstraint("alert_event_id", "attempt_number"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    alert_event_id: Mapped[str] = mapped_column(ForeignKey("alert_events.id"), index=True)
    provider: Mapped[str] = mapped_column(String(50))
    attempt_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    error_summary: Mapped[str | None] = mapped_column(String(500))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AlertConditionState(Base):
    __tablename__ = "alert_condition_states"
    __table_args__ = (UniqueConstraint("condition_type", "scope_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    condition_type: Mapped[str] = mapped_column(String(50), index=True)
    scope_key: Mapped[str] = mapped_column(String(255))
    current_state: Mapped[str] = mapped_column(String(50))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Diagnosis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1, max_length=2000)
    probable_root_cause: str = Field(min_length=1, max_length=2000)
    affected_component: str = Field(min_length=1, max_length=1000)
    recommended_fix: str = Field(min_length=1, max_length=2000)
    confidence: float = Field(ge=0, le=1)
    risk_level: Literal["low", "medium", "high"]


class Failure(BaseModel):
    workflow_id: str
    workflow_name: str
    execution_id: str
    failed_node: str | None = None
    error_type: str
    error_message: str
    execution_timestamp: datetime | None = None
    severity: Literal["medium", "high"] = "medium"


class IncidentOut(Failure):
    model_config = ConfigDict(from_attributes=True)
    id: str
    status: Literal["open", "diagnosed"]
    diagnosis: Diagnosis | None = None
    diagnosis_provider: str | None = None


class Workflow(BaseModel):
    id: str
    name: str
    active: bool


class WorkflowPage(BaseModel):
    data: list[Workflow]
    next_cursor: str | None = None


class FailurePage(BaseModel):
    data: list[Failure]
    next_cursor: str | None = None


class IncidentPage(BaseModel):
    data: list[IncidentOut]


class SyncResult(BaseModel):
    scanned: int
    created: int
    next_cursor: str | None = None


class DemoInfo(BaseModel):
    mode: Literal["synthetic"]
    incident_id: str
    diagnosis_provider: Literal["mock"]
    note: str


class SystemStatus(BaseModel):
    n8n_configured: bool
    ai_mode: Literal["mock", "configured"]
    database_engine: str


MonitoringState = Literal["disabled", "idle", "running", "degraded"]
BackfillState = Literal["disabled", "pending", "complete"]
LeaseState = Literal["active", "standby", "unclaimed"]
WorkflowHealthState = Literal["unknown", "healthy", "degraded", "unhealthy"]


class MonitoringStatus(BaseModel):
    enabled: bool
    state: MonitoringState
    poll_interval_seconds: float
    last_checked_at: datetime | None = None
    last_fresh_poll_at: datetime | None = None
    last_successful_sync_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error_summary: str | None = None
    consecutive_failure_count: int = 0
    backfill_pending: bool = False
    backfill_completed_at: datetime | None = None
    backfill_state: BackfillState = "complete"
    lease_state: LeaseState = "unclaimed"
    retention_days: int
    last_retention_at: datetime | None = None


class MonitoringSyncResult(BaseModel):
    status: Literal["completed", "skipped", "disabled", "failed"]
    executions_discovered: int = 0
    history_records_created: int = 0
    incidents_created: int = 0
    next_cursor: str | None = None


class WorkflowHealth(BaseModel):
    workflow_id: str
    workflow_name: str
    health: WorkflowHealthState
    last_checked_at: datetime | None = None
    last_successful_execution_at: datetime | None = None
    last_failed_execution_at: datetime | None = None
    recent_executions_count: int = 0
    recent_success_count: int = 0
    recent_failure_count: int = 0
    success_rate: float | None = None
    open_incident_count: int = 0


class WorkflowHealthPage(BaseModel):
    data: list[WorkflowHealth]


class OverviewMetrics(BaseModel):
    monitored_workflow_count: int = 0
    healthy_workflow_count: int = 0
    degraded_workflow_count: int = 0
    unhealthy_workflow_count: int = 0
    unknown_workflow_count: int = 0
    open_incident_count: int = 0
    recent_failure_count: int = 0
    recent_success_count: int = 0
    last_successful_monitoring_sync_at: datetime | None = None
    monitoring: MonitoringStatus


AlertTriggerType = Literal[
    "new_incident",
    "workflow_unhealthy",
    "monitoring_degraded",
]
AlertDeliveryStatus = Literal["pending", "delivered", "failed"]
AlertDeliveryProviderName = Literal["mock", "webhook"]


class AlertRuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    trigger_type: AlertTriggerType
    enabled: bool = False
    cooldown_seconds: int | None = Field(default=None, ge=60, le=86400)
    delivery_provider: AlertDeliveryProviderName = "mock"


class AlertRuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    enabled: bool | None = None
    cooldown_seconds: int | None = Field(default=None, ge=60, le=86400)


class AlertRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    trigger_type: AlertTriggerType
    enabled: bool
    cooldown_seconds: int
    delivery_provider: AlertDeliveryProviderName
    created_at: datetime
    updated_at: datetime


class AlertRulePage(BaseModel):
    data: list[AlertRuleOut]


class AlertEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    rule_id: str
    trigger_type: AlertTriggerType
    deduplication_key: str
    incident_id: str | None = None
    workflow_id: str | None = None
    status: AlertDeliveryStatus
    cooldown_until: datetime
    last_error_summary: str | None = None
    created_at: datetime
    delivered_at: datetime | None = None


class AlertEventPage(BaseModel):
    data: list[AlertEventOut]


class AlertDeliveryAttemptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    alert_event_id: str
    provider: AlertDeliveryProviderName
    attempt_number: int
    status: AlertDeliveryStatus
    error_summary: str | None = None
    started_at: datetime
    completed_at: datetime | None = None


class AlertDeliveryAttemptPage(BaseModel):
    data: list[AlertDeliveryAttemptOut]


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail

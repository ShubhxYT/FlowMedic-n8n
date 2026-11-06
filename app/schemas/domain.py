from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ItemShape = Literal["empty", "object", "array", "scalar", "mixed"]


class DiagnosisEvidence(BaseModel):
    """Explicit allowlist of sanitized execution metadata shared with the AI provider.

    Never add raw items, node parameters, credentials, headers, or runData here.
    """

    model_config = ConfigDict(extra="forbid")
    error_description: str | None = Field(default=None, max_length=2000)
    http_code: int | None = Field(default=None, ge=100, le=599)
    line_number: int | None = Field(default=None, ge=1)
    node_type: str | None = Field(default=None, max_length=1000)
    item_count: int | None = Field(default=None, ge=0)
    output_branch_count: int | None = Field(default=None, ge=0)
    node_run_count: int | None = Field(default=None, ge=0)
    item_shape: ItemShape | None = None


class Hypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rank: int = Field(ge=1, le=100)
    explanation: str = Field(min_length=1, max_length=2000)
    supporting_evidence: list[str] = Field(min_length=1, max_length=50)
    contradictory_evidence: str = Field(min_length=1, max_length=2000)
    verification_step: str = Field(min_length=1, max_length=2000)


class Recommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(min_length=1, max_length=2000)
    verification: str = Field(min_length=1, max_length=2000)


class Diagnosis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1, max_length=2000)
    probable_root_cause: str = Field(min_length=1, max_length=2000)
    affected_component: str = Field(min_length=1, max_length=1000)
    recommended_fix: str = Field(min_length=1, max_length=2000)
    confidence: float = Field(ge=0, le=1)
    confidence_basis: str = Field(min_length=1, max_length=2000)
    risk_level: Literal["low", "medium", "high"]
    hypotheses: list[Hypothesis] = Field(min_length=1, max_length=10)
    recommendations: list[Recommendation] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def require_unique_rank_order(self):
        ranks = [hypothesis.rank for hypothesis in self.hypotheses]
        if len(ranks) != len(set(ranks)):
            raise ValueError("hypothesis ranks must be unique")
        self.hypotheses.sort(key=lambda hypothesis: hypothesis.rank)
        return self


class Failure(BaseModel):
    workflow_id: str
    workflow_name: str
    execution_id: str
    failed_node: str | None = None
    error_type: str
    error_message: str
    execution_timestamp: datetime | None = None
    severity: Literal["medium", "high"] = "medium"
    evidence: DiagnosisEvidence | None = None


def upgrade_legacy_diagnosis(value: object) -> object:
    """Backfill display-only fields for diagnoses persisted before the richer contract.

    This shim lives on the output model only. Provider responses must still satisfy
    the full `Diagnosis` schema, so an AI model cannot silently skip the new fields.
    """

    if not isinstance(value, dict):
        return value
    if {"confidence_basis", "hypotheses", "recommendations"} <= value.keys():
        return value

    upgraded = dict(value)
    fix = str(upgraded.get("recommended_fix") or "").strip()
    cause = str(upgraded.get("probable_root_cause") or "").strip()
    confidence = upgraded.get("confidence", 0)
    action = fix or "Review the sanitized failure evidence before changing the workflow."
    upgraded.setdefault(
        "confidence_basis",
        (
            f"Legacy diagnosis stored without a confidence basis; its original confidence was "
            f"{confidence}. It only used the normalized failure fields, so this explanation is "
            f"reconstructed rather than based on the richer evidence bundle."
        ),
    )
    upgraded.setdefault(
        "hypotheses",
        [
            {
                "rank": 1,
                "explanation": cause or "The original diagnosis did not record a root cause.",
                "supporting_evidence": [
                    "Legacy diagnosis produced before execution evidence was captured."
                ],
                "contradictory_evidence": (
                    "Legacy diagnoses did not record contradictory evidence or missing evidence."
                ),
                "verification_step": action,
            }
        ],
    )
    upgraded.setdefault(
        "recommendations",
        [
            {
                "action": action,
                "verification": (
                    "Re-run the workflow after the check and confirm the recorded failure no "
                    "longer reproduces."
                ),
            }
        ],
    )
    return upgraded


class IncidentOut(Failure):
    model_config = ConfigDict(from_attributes=True)
    id: str
    status: Literal["open", "diagnosed"]
    diagnosis: Diagnosis | None = None
    diagnosis_provider: str | None = None

    @field_validator("diagnosis", mode="before")
    @classmethod
    def upgrade_legacy_diagnosis(cls, value: object) -> object:
        return upgrade_legacy_diagnosis(value)


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

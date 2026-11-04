export type IncidentStatus = "open" | "diagnosed";
export type Severity = "medium" | "high";
export type RiskLevel = "low" | "medium" | "high";

export interface Diagnosis {
  summary: string;
  probable_root_cause: string;
  affected_component: string;
  recommended_fix: string;
  confidence: number;
  risk_level: RiskLevel;
}

export interface Incident {
  id: string;
  workflow_id: string;
  workflow_name: string;
  execution_id: string;
  failed_node: string | null;
  error_type: string;
  error_message: string;
  execution_timestamp: string | null;
  severity: Severity;
  status: IncidentStatus;
  diagnosis: Diagnosis | null;
  diagnosis_provider: string | null;
}

export interface Workflow {
  id: string;
  name: string;
  active: boolean;
}

export interface IncidentPage {
  data: Incident[];
}

export interface WorkflowPage {
  data: Workflow[];
  next_cursor: string | null;
}

export interface HealthResponse {
  status: "ok";
}

export interface N8nConnectionResponse {
  connected: boolean;
}

export interface SystemStatus {
  n8n_configured: boolean;
  ai_mode: "mock" | "configured";
  database_engine: string;
}

export type MonitoringState = "disabled" | "idle" | "running" | "degraded";
export type BackfillState = "disabled" | "pending" | "complete";
export type LeaseState = "active" | "standby" | "unclaimed";
export type WorkflowHealthState = "unknown" | "healthy" | "degraded" | "unhealthy";

export interface MonitoringStatus {
  enabled: boolean;
  state: MonitoringState;
  poll_interval_seconds: number;
  last_checked_at: string | null;
  last_fresh_poll_at: string | null;
  last_successful_sync_at: string | null;
  last_error_at: string | null;
  last_error_summary: string | null;
  consecutive_failure_count: number;
  backfill_pending: boolean;
  backfill_completed_at: string | null;
  backfill_state: BackfillState;
  lease_state: LeaseState;
  retention_days: number;
  last_retention_at: string | null;
}

export interface WorkflowHealth {
  workflow_id: string;
  workflow_name: string;
  health: WorkflowHealthState;
  last_checked_at: string | null;
  last_successful_execution_at: string | null;
  last_failed_execution_at: string | null;
  recent_executions_count: number;
  recent_success_count: number;
  recent_failure_count: number;
  success_rate: number | null;
  open_incident_count: number;
}

export interface WorkflowHealthPage {
  data: WorkflowHealth[];
}

export interface OverviewMetrics {
  monitored_workflow_count: number;
  healthy_workflow_count: number;
  degraded_workflow_count: number;
  unhealthy_workflow_count: number;
  unknown_workflow_count: number;
  open_incident_count: number;
  recent_failure_count: number;
  recent_success_count: number;
  last_successful_monitoring_sync_at: string | null;
  monitoring: MonitoringStatus;
}

export interface ApiErrorBody {
  error?: { code?: string; message?: string };
}

export type AlertTriggerType = "new_incident" | "workflow_unhealthy" | "monitoring_degraded";
export type AlertDeliveryStatus = "pending" | "delivered" | "failed";
export type AlertDeliveryProvider = "mock" | "webhook";

export interface AlertRule {
  id: string;
  name: string;
  trigger_type: AlertTriggerType;
  enabled: boolean;
  cooldown_seconds: number;
  delivery_provider: AlertDeliveryProvider;
  created_at: string;
  updated_at: string;
}

export interface AlertEvent {
  id: string;
  rule_id: string;
  trigger_type: AlertTriggerType;
  deduplication_key: string;
  incident_id: string | null;
  workflow_id: string | null;
  status: AlertDeliveryStatus;
  cooldown_until: string;
  last_error_summary: string | null;
  created_at: string;
  delivered_at: string | null;
}

export interface AlertRulePage {
  data: AlertRule[];
}

export interface AlertEventPage {
  data: AlertEvent[];
}

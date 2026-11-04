"use client";

import { PageHeader } from "@/components/page-header";
import { StatePanel } from "@/components/state-panel";
import { api } from "@/lib/api";
import { formatTimestamp } from "@/lib/format";
import { useApi } from "@/lib/use-api";

function ownershipLabel(
  enabled: boolean | undefined,
  leaseState: "active" | "standby" | "unclaimed" | undefined,
): string {
  if (!enabled) return "Disabled";
  if (leaseState === "active") return "Active";
  if (leaseState === "standby") return "Standby";
  return "Idle";
}

export default function SettingsPage() {
  const system = useApi(() => api.systemStatus());
  const health = useApi(() => api.health());
  const n8n = useApi(() => api.n8nStatus());
  const monitoring = useApi(() => api.monitoringStatus());
  const backendError = system.error ?? health.error ?? monitoring.error;

  if (backendError) {
    return (
      <>
        <PageHeader
          title="Settings & connection"
          description="Safe connection and monitoring state. Secret values are never shown."
        />
        <StatePanel
          title="Backend unavailable"
          message={backendError.message}
          action={
            <button
              className="button button-secondary"
              onClick={() => {
                void system.refresh();
                void health.refresh();
                void n8n.refresh();
                void monitoring.refresh();
              }}
            >
              Try again
            </button>
          }
        />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Settings & connection"
        description="Operational state provided by the backend. API keys and connection secrets are never exposed."
      />
      {system.loading || health.loading || monitoring.loading ? (
        <StatePanel
          title="Loading backend status"
          message="Checking FlowMedic configuration without reading secrets."
        />
      ) : (
        <section className="settings-grid">
          <article className="panel setting-card">
            <h2>Backend health</h2>
            <p>Local process liveness from the health endpoint.</p>
            <strong className="text-emerald-300">
              {health.data?.status === "ok" ? "Operational" : "Unknown"}
            </strong>
            <small>GET /health</small>
          </article>
          <article className="panel setting-card">
            <h2>n8n configuration</h2>
            <p>Reports configuration only; no credentials are returned.</p>
            <strong>{system.data?.n8n_configured ? "Configured" : "Not configured"}</strong>
            <small>
              {n8n.loading
                ? "Checking connectivity…"
                : n8n.data?.connected
                  ? "Connected"
                  : n8n.error
                    ? "Connection not available"
                    : "Not available yet"}
            </small>
          </article>
          <article className="panel setting-card">
            <h2>Monitoring ownership</h2>
            <p>A database-backed lease prevents concurrent polling across API processes.</p>
            <strong>{ownershipLabel(monitoring.data?.enabled, monitoring.data?.lease_state)}</strong>
            <small>
              {monitoring.data?.enabled
                ? "Poll interval: " + monitoring.data.poll_interval_seconds + " seconds"
                : "No n8n credentials configured"}
            </small>
          </article>
          <article className="panel setting-card">
            <h2>Fresh polling</h2>
            <p>Every cycle checks the newest n8n page before any historical backfill.</p>
            <strong>{formatTimestamp(monitoring.data?.last_fresh_poll_at ?? null)}</strong>
            <small>
              {monitoring.data?.backfill_state === "pending"
                ? "Historical backfill is pending"
                : monitoring.data?.backfill_state === "disabled"
                  ? "Historical backfill is disabled"
                  : "Historical backfill is complete"}
            </small>
          </article>
          <article className="panel setting-card">
            <h2>History retention</h2>
            <p>Health metrics use retained execution history. Incidents are kept separately.</p>
            <strong>{monitoring.data?.retention_days ?? "Unknown"} days</strong>
            <small>
              Last cleanup: {formatTimestamp(monitoring.data?.last_retention_at ?? null)}
            </small>
          </article>
          <article className="panel setting-card">
            <h2>Last successful sync</h2>
            <p>Persisted monitoring checkpoint; no execution payload is stored here.</p>
            <strong>{formatTimestamp(monitoring.data?.last_successful_sync_at ?? null)}</strong>
            <small>
              {monitoring.data?.consecutive_failure_count
                ? monitoring.data.consecutive_failure_count + " consecutive monitoring failures"
                : "No active monitoring failure"}
            </small>
          </article>
          <article className="panel setting-card">
            <h2>AI diagnosis provider</h2>
            <p>Diagnosis remains advisory and never changes an automation workflow.</p>
            <strong>
              {system.data?.ai_mode === "configured" ? "Configured provider" : "Deterministic mock mode"}
            </strong>
            <small>
              {system.data?.ai_mode === "configured"
                ? "Live provider key is present"
                : "No AI key is configured"}
            </small>
          </article>
          <article className="panel setting-card">
            <h2>Database</h2>
            <p>Database engine only. Connection strings are not shown.</p>
            <strong>{system.data?.database_engine ?? "Unknown"}</strong>
            <small>Managed by the FastAPI service</small>
          </article>
        </section>
      )}
    </>
  );
}

"use client";

import Link from "next/link";

import { IncidentTable } from "@/components/incident-table";
import { MetricCard } from "@/components/metric-card";
import { PageHeader } from "@/components/page-header";
import { StatePanel } from "@/components/state-panel";
import { api } from "@/lib/api";
import { formatTimestamp } from "@/lib/format";
import { useApi } from "@/lib/use-api";

export default function OverviewPage() {
  const metrics = useApi(() => api.overviewMetrics());
  const incidents = useApi(() => api.incidents(50, 0));
  const incidentRows = incidents.data?.data ?? [];
  const monitoring = metrics.data?.monitoring;

  return <>
    <PageHeader title="Automation health" description="Persisted execution history and deterministic workflow health. FlowMedic observes n8n read-only and never applies a fix." action={<Link className="button button-secondary" href="/incidents">Review incidents</Link>} />
    {metrics.error && <StatePanel title="Monitoring metrics unavailable" message={metrics.error.message} action={<button className="button button-secondary" onClick={() => void metrics.refresh()}>Try again</button>} />}
    {!metrics.error && <section className="metric-grid" aria-label="Automation health metrics">
      <MetricCard label="Monitoring" value={metrics.loading ? "…" : monitoring?.state ?? "unknown"} detail={monitoring?.enabled ? "Polls every " + monitoring.poll_interval_seconds + "s" : "Disabled until n8n is configured"} tone={monitoring?.state === "degraded" ? "danger" : monitoring?.enabled ? "success" : "muted"} />
      <MetricCard label="Monitored workflows" value={metrics.loading ? "…" : metrics.data?.monitored_workflow_count ?? "—"} detail="Workflows with persisted execution history" />
      <MetricCard label="Healthy" value={metrics.loading ? "…" : metrics.data?.healthy_workflow_count ?? "—"} detail="No recent failures or open incidents" tone="success" />
      <MetricCard label="Degraded" value={metrics.loading ? "…" : metrics.data?.degraded_workflow_count ?? "—"} detail="Recent failures need attention" tone="danger" />
      <MetricCard label="Unhealthy" value={metrics.loading ? "…" : metrics.data?.unhealthy_workflow_count ?? "—"} detail="Repeated failures or unresolved incidents" tone="danger" />
      <MetricCard label="Open incidents" value={metrics.loading ? "…" : metrics.data?.open_incident_count ?? "—"} detail="Requires human review" tone={metrics.data?.open_incident_count ? "danger" : "success"} />
      <MetricCard label="Recent failures" value={metrics.loading ? "…" : metrics.data?.recent_failure_count ?? "—"} detail="Latest persisted execution window" tone="danger" />
      <MetricCard label="Recent successes" value={metrics.loading ? "…" : metrics.data?.recent_success_count ?? "—"} detail="Latest persisted execution window" tone="success" />
    </section>}
    <section className="overview-secondary mt-4">
      <div className="panel overflow-hidden">
        <div className="panel-header"><div><h2>Recent incidents</h2><p>Persisted incidents from monitoring or explicit sync.</p></div><Link href="/incidents" className="text-sm text-blue-300 hover:text-blue-200">View all</Link></div>
        {incidents.loading && <StatePanel title="Loading incidents" message="Requesting persisted incidents from FlowMedic." />}
        {incidents.error && <StatePanel title="Incidents unavailable" message={incidents.error.message} action={<button className="button button-secondary" onClick={() => void incidents.refresh()}>Try again</button>} />}
        {!incidents.loading && !incidents.error && incidentRows.length === 0 && <StatePanel title="No incidents yet" message="Connect n8n and let monitoring observe failed executions to populate this dashboard." />}
        {!incidents.loading && !incidents.error && incidentRows.length > 0 && <IncidentTable incidents={incidentRows.slice(0, 6)} compact />}
      </div>
      <aside className="panel trend-placeholder"><h2>Monitoring cadence</h2><p className="mt-1 text-sm text-slate-400">Last successful synchronization from persisted monitoring state.</p><div className="trend-grid" aria-label="Monitoring status detail"><span className="trend-line" /></div><small>{formatTimestamp(metrics.data?.last_successful_monitoring_sync_at ?? null)}</small></aside>
    </section>
  </>;
}

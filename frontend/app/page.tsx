"use client";

import Link from "next/link";

import { IncidentTable } from "@/components/incident-table";
import { MetricCard } from "@/components/metric-card";
import { PageHeader } from "@/components/page-header";
import { StatePanel } from "@/components/state-panel";
import { Timestamp } from "@/components/timestamp";
import { api } from "@/lib/api";
import { useApi } from "@/lib/use-api";

export default function OverviewPage() {
  const metrics = useApi(() => api.overviewMetrics());
  const incidents = useApi(() => api.incidents(50, 0));
  const incidentRows = incidents.data?.data ?? [];
  const monitoring = metrics.data?.monitoring;
  const loading = metrics.loading;
  const dash = "—";

  return <>
    <PageHeader eyebrow="Overview" title="Automation health" description="Persisted execution history and deterministic workflow health. FlowMedic observes n8n read-only and never applies a fix." action={<Link className="button button-secondary" href="/incidents">Review incidents</Link>} />
    {metrics.error && <StatePanel tone="error" title="Monitoring metrics unavailable" message={metrics.error.message} action={<button type="button" className="button button-secondary" onClick={() => void metrics.refresh()}>Try again</button>} />}
    {!metrics.error && <section className="metric-grid" aria-label="Automation health metrics">
      <MetricCard loading={loading} label="Monitoring" value={monitoring?.state ?? "unknown"} detail={monitoring?.enabled ? "Polls every " + monitoring.poll_interval_seconds + "s" : "Disabled until n8n is configured"} tone={monitoring?.state === "degraded" ? "danger" : monitoring?.enabled ? "success" : "muted"} />
      <MetricCard loading={loading} label="Monitored workflows" value={metrics.data?.monitored_workflow_count ?? dash} detail="Workflows with persisted execution history" />
      <MetricCard loading={loading} label="Healthy" value={metrics.data?.healthy_workflow_count ?? dash} detail="No recent failures or open incidents" tone="success" />
      <MetricCard loading={loading} label="Degraded" value={metrics.data?.degraded_workflow_count ?? dash} detail="Recent failures need attention" tone="danger" />
      <MetricCard loading={loading} label="Unhealthy" value={metrics.data?.unhealthy_workflow_count ?? dash} detail="Repeated failures or unresolved incidents" tone="danger" />
      <MetricCard loading={loading} label="Open incidents" value={metrics.data?.open_incident_count ?? dash} detail="Requires human review" tone={metrics.data?.open_incident_count ? "danger" : "success"} />
      <MetricCard loading={loading} label="Recent failures" value={metrics.data?.recent_failure_count ?? dash} detail="Latest persisted execution window" tone="danger" />
      <MetricCard loading={loading} label="Recent successes" value={metrics.data?.recent_success_count ?? dash} detail="Latest persisted execution window" tone="success" />
    </section>}
    <section className="overview-secondary">
      <div className="panel overflow-hidden">
        <div className="panel-header"><div><h2>Recent incidents</h2><p>Persisted incidents from monitoring or explicit sync.</p></div><Link href="/incidents" className="panel-link">View all</Link></div>
        {incidents.loading && <StatePanel inline tone="loading" title="Loading incidents" message="Requesting persisted incidents from FlowMedic." />}
        {incidents.error && <StatePanel inline tone="error" title="Incidents unavailable" message={incidents.error.message} action={<button type="button" className="button button-secondary" onClick={() => void incidents.refresh()}>Try again</button>} />}
        {!incidents.loading && !incidents.error && incidentRows.length === 0 && <StatePanel inline title="No incidents yet" message="Connect n8n and let monitoring observe failed executions to populate this dashboard." />}
        {!incidents.loading && !incidents.error && incidentRows.length > 0 && <IncidentTable incidents={incidentRows.slice(0, 6)} compact />}
      </div>
      <aside className="panel" aria-labelledby="monitoring-detail-title">
        <div className="panel-header"><div><h2 id="monitoring-detail-title">Monitoring detail</h2><p>Persisted monitoring state from the backend.</p></div></div>
        {metrics.loading && <StatePanel inline tone="loading" title="Loading monitoring state" message="Reading the latest monitoring checkpoint." />}
        {!metrics.loading && metrics.error && <p className="muted-text p-5 text-sm">Monitoring state could not be loaded. See the error above.</p>}
        {!metrics.loading && !metrics.error && <dl className="kv-list">
          <div><dt>Last successful sync</dt><dd><Timestamp value={metrics.data?.last_successful_monitoring_sync_at ?? null} /></dd></div>
          <div><dt>Last checked</dt><dd><Timestamp value={monitoring?.last_checked_at ?? null} /></dd></div>
          <div><dt>Last error</dt><dd>{monitoring?.last_error_at ? <Timestamp value={monitoring.last_error_at} /> : <span className="muted-text">None recorded</span>}</dd></div>
          <div><dt>Consecutive failures</dt><dd>{monitoring?.consecutive_failure_count ?? 0}</dd></div>
        </dl>}
      </aside>
    </section>
  </>;
}

import { Activity, ArrowDownRight, ArrowRight, ArrowUpRight, CircleAlert, Radio, Workflow as WorkflowIcon } from "lucide-react";
import { useCallback, useState } from "react";
import type { ReactNode } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import type { Incident, MonitoringStatus } from "@/lib/types";
import {
  Badge, Heading, Meta, Notice, Panel, Timestamp, number, time, useResource,
} from "./ui";
import type { PageProps } from "./ui";
import { IncidentDetail, prioritize } from "./triage";

function Stat({ icon, label, value, note, href, tone = "" }: {
  icon: ReactNode; label: string; value: ReactNode; note: ReactNode; href?: string; tone?: string;
}) {
  const contents = <>
    <div className="stat-top"><span className="stat-icon">{icon}</span>{href && <ArrowUpRight size={16} aria-hidden="true" />}</div>
    <div className="stat-label">{label}</div>
    <div className="stat-value">{value}</div>
    <div className="stat-note">{note}</div>
  </>;
  return href ? <Link href={href} className={"stat " + tone}>{contents}</Link> : <div className={"stat " + tone}>{contents}</div>;
}

export function Monitoring({ rows }: { rows: MonitoringStatus[] }) {
  if (!rows.length) return <Notice title="No monitoring data">Connect an n8n instance in FlowMedic to begin monitoring.</Notice>;
  return <div className="monitor-list">{rows.map((row) =>
    <article className="monitor" key={row.instance_id ?? "workspace"}>
      <div className="monitor-name"><span><Radio size={17} aria-hidden="true" /></span><strong>{row.instance_name ?? "Workspace monitoring"}</strong><Badge value={row.state} /></div>
      <dl className="facts">
        <div><dt>Last successful sync</dt><dd>{time(row.last_successful_sync_at)}</dd></div>
        <div><dt>Last fresh poll</dt><dd>{time(row.last_fresh_poll_at)}</dd></div>
        <div><dt>Poll interval</dt><dd>{row.poll_interval_seconds} sec</dd></div>
        <div><dt>Consecutive failures</dt><dd>{row.consecutive_failure_count}</dd></div>
      </dl>
      {row.last_error_summary && <p className="monitor-error">{row.last_error_summary}</p>}
    </article>)}</div>;
}

export function Overview({ instance, connections }: PageProps) {
  const [selected, select] = useState<Incident | null>(null);
  const load = useCallback(() => Promise.allSettled([
    api.overviewMetrics(instance), api.monitoringStatus(instance), api.incidents(instance),
  ]), [instance]);
  const { data, loading, error } = useResource(load);
  const metrics = data?.[0].status === "fulfilled" ? data[0].value : null;
  const monitoring = data?.[1].status === "fulfilled" ? data[1].value.items : null;
  const incidents = data?.[2].status === "fulfilled" ? data[2].value.data : null;
  const urgent = prioritize((incidents ?? []).filter((item) => item.status !== "resolved"));
  const monitored = metrics?.monitored_workflow_count ?? 0;
  const health = metrics ? [
    { key: "healthy", count: metrics.healthy_workflow_count, label: "Healthy" },
    { key: "degraded", count: metrics.degraded_workflow_count, label: "Degraded" },
    { key: "unhealthy", count: metrics.unhealthy_workflow_count, label: "Unhealthy" },
    { key: "unknown", count: metrics.unknown_workflow_count, label: "Unknown" },
  ] : [];
  return <>
    <Heading kicker="Workspace overview" title="Automation health" description="See what needs attention across your n8n workflows."
      aside={<Link className="button button-primary" href="/incidents">Review incidents <ArrowRight size={17} aria-hidden="true" /></Link>} />
    {error && <Notice kind="error" title={error} />}
    {loading && !data && <Notice kind="loading" title="Loading live overview" />}
    {data && <>
      <div className="stats">
        <Stat label="Open incidents" value={metrics ? number(metrics.open_incident_count) : "—"}
          note="Awaiting triage" href="/incidents?status=open" tone="danger" icon={<CircleAlert size={19} />} />
        <Stat label="Unhealthy workflows" value={metrics ? number(metrics.unhealthy_workflow_count) : "—"}
          note="Need investigation" href="/workflows?health=unhealthy" tone="warn" icon={<ArrowDownRight size={19} />} />
        <Stat label="Monitored workflows" value={metrics ? number(monitored) : "—"}
          note={metrics ? number(metrics.healthy_workflow_count) + " currently healthy" : "Metrics unavailable"} href="/workflows" icon={<WorkflowIcon size={19} />} />
        <Stat label="Monitoring freshness" value={metrics?.last_successful_monitoring_sync_at
          ? <span className="stat-time">{time(metrics.last_successful_monitoring_sync_at, false, false)}</span> : <span className="stat-time">Unavailable</span>}
          note={metrics?.last_successful_monitoring_sync_at ? "Last successful sync · " + time(metrics.last_successful_monitoring_sync_at).split(",")[0] : "Last successful sync"}
          icon={<Activity size={19} />} />
      </div>
      {!metrics && <Notice kind="error" title="Health counts are temporarily unavailable" />}
      <div className="overview-grid">
        <Panel title="Triage queue" description="Unresolved incidents from the first loaded page."
          action={<Link className="text-link" href="/incidents">All incidents <ArrowRight size={15} aria-hidden="true" /></Link>}>
          {incidents === null ? <Notice kind="error" title="Could not load incidents" />
            : !urgent.length ? <Notice title="No unresolved incidents on this page">Open Incidents to browse more results.</Notice>
            : <ul className="queue">{urgent.slice(0, 5).map((item) => <li key={item.id}>
              <button className="queue-row" onClick={() => select(item)}>
                <span className={"severity-mark " + item.severity}><CircleAlert size={17} aria-hidden="true" /></span>
                <span className="queue-title"><strong>{item.workflow_name}</strong><Meta>{item.failed_node ?? item.error_type}</Meta></span>
                <Badge value={item.severity} />
                <ArrowUpRight size={15} className="queue-arrow" aria-hidden="true" />
              </button>
            </li>)}</ul>}
        </Panel>
        <Panel title="Monitoring status" description="Polling and sync status by instance.">
          {monitoring === null ? <Notice kind="error" title="Could not load monitoring status" /> : <Monitoring rows={monitoring} />}
        </Panel>
      </div>
      {metrics && <Panel title="Workflow health" description="Distribution across workflows with persisted execution history."
        action={<Link className="text-link" href="/workflows">Browse workflows <ArrowRight size={15} aria-hidden="true" /></Link>}>
        <div className="distribution">
          {monitored > 0 && <div className="distribution-bar" aria-hidden="true">{health.map((entry) =>
            entry.count > 0 && <span key={entry.key} className={entry.key} style={{ flexGrow: entry.count }} />)}</div>}
          <div className="distribution-legend">{health.map((entry) => <Link key={entry.key} href={"/workflows?health=" + entry.key}>
            <Badge value={entry.key} label={entry.label} /> <strong>{number(entry.count)}</strong>
          </Link>)}</div>
          <div className="execution-summary"><span><strong>{number(metrics.recent_success_count)}</strong> recent successes</span>
            <span><strong>{number(metrics.recent_failure_count)}</strong> recent failures</span>
            {metrics.last_successful_monitoring_sync_at && <Timestamp value={metrics.last_successful_monitoring_sync_at} />}
          </div>
        </div>
      </Panel>}
    </>}
    {selected && <IncidentDetail incident={selected} connections={connections} onClose={() => select(null)} />}
  </>;
}

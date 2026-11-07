"use client";

import { useConnectionScope } from "@/components/connection-scope";

import { PageHeader } from "@/components/page-header";
import { StatePanel } from "@/components/state-panel";
import { api } from "@/lib/api";
import { formatTimestamp, healthTone } from "@/lib/format";
import { useApi } from "@/lib/use-api";

function rate(value: number | null): string {
  return value === null ? "Not enough data" : Math.round(value * 100) + "%";
}

export default function WorkflowsPage() {
  const {workspaceId, instanceId, instances} = useConnectionScope();
  const metrics = useApi(() => api.workflowMetrics(instanceId), `${workspaceId}:${instanceId ?? "all"}:workflows`);
  return <><PageHeader title="Workflows" description="Deterministic health is calculated from persisted, sanitized execution history. Unknown means there is not enough classified history yet." />
    <WorkflowCatalog key={`${workspaceId}:${instanceId ?? "all"}`} />
    <section className="panel overflow-hidden">
      <div className="panel-header"><div><h2>Monitored workflows</h2><p>Latest 20 classified executions per workflow; n8n remains read-only.</p></div></div>
      {metrics.loading && <StatePanel title="Loading workflow health" message="Requesting persisted monitoring history." />}
      {metrics.error && <StatePanel title="Workflow health unavailable" message={metrics.error.message} action={<button className="button button-secondary" onClick={() => void metrics.refresh()}>Try again</button>} />}
      {!metrics.loading && !metrics.error && metrics.data?.data.length === 0 && <StatePanel title="Not enough data yet" message="Connect n8n and wait for monitoring to observe executions. No workflow health is inferred without persisted history." />}
      {!metrics.loading && !metrics.error && (metrics.data?.data.length ?? 0) > 0 && <div className="data-table-wrap"><table className="data-table"><thead><tr><th>Workflow</th><th>Health</th><th>Last checked</th><th>Last success</th><th>Last failure</th><th>Success rate</th><th>Open incidents</th></tr></thead><tbody>{metrics.data?.data.map((workflow) => <tr key={`${workflow.instance_id}:${workflow.workflow_id}`}><td className="font-medium text-slate-100">{workflow.workflow_name}<span className="subtle">{instances.find(item => item.id === workflow.instance_id)?.name ?? workflow.instance_id.slice(0, 8)} · {workflow.workflow_id} · {workflow.recent_executions_count} recent executions</span></td><td><span className={healthTone(workflow.health)}>{workflow.health}</span></td><td>{formatTimestamp(workflow.last_checked_at)}</td><td>{formatTimestamp(workflow.last_successful_execution_at)}</td><td>{formatTimestamp(workflow.last_failed_execution_at)}</td><td>{rate(workflow.success_rate)}</td><td>{workflow.open_incident_count}</td></tr>)}</tbody></table></div>}
    </section>
  </>;
}

function WorkflowCatalog() {
  const {workspaceId, instanceId, instances} = useConnectionScope();
  const selected = instances.find(item => item.id === instanceId);
  if (!selected) return <StatePanel title="Select an n8n instance" message="Use the instance selector above to browse live workflows. Persisted workflow health across all instances appears below." />;
  if (!selected.enabled) return <StatePanel title="This instance is disabled" message="An admin can add a key and enable the connection in Settings. Persisted history remains available below." />;
  return <EnabledWorkflows key={`${workspaceId}:${selected.id}:${selected.revision}`} id={selected.id} baseUrl={selected.base_url} />;
}
function EnabledWorkflows({id, baseUrl}: {id: string; baseUrl: string | null}) {
  const {workspaceId} = useConnectionScope();
  const query = useApi(() => api.workflows(id), `${workspaceId}:${id}:catalog`);
  if (query.loading) return <p role="status">Loading live workflows…</p>;
  if (query.error) return <StatePanel title="Live workflows unavailable" message={query.error.message} action={<button className="button button-secondary" onClick={() => void query.refresh()}>Try again</button>} />;
  return <section className="panel setting-card"><h2>Live workflows</h2>{query.data?.data.length === 0 && <p>No workflows found.</p>}<ul className="connection-list">{query.data?.data.map(item => <li key={`${item.instance_id}:${item.id}`}>{workflowLink(baseUrl, item.id) ? <a className="table-link" href={workflowLink(baseUrl, item.id)!} target="_blank" rel="noopener noreferrer">{item.name} ↗</a> : item.name}<span className="subtle">{item.instance_id.slice(0, 8)} · {item.id} · {item.active ? "Active" : "Inactive"}</span></li>)}</ul></section>;
}
function workflowLink(base: string | null, id: string): string | null {
  if (!base) return null;
  try {
    const url = new URL(base);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash) return null;
    url.pathname = url.pathname.replace(/\/api\/v1\/?$/, "").replace(/\/$/, "") + "/workflow/" + encodeURIComponent(id);
    return url.toString();
  } catch { return null; }
}

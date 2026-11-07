"use client";

import { PageHeader } from "@/components/page-header";
import { StatePanel } from "@/components/state-panel";
import { api } from "@/lib/api";
import { formatTimestamp, healthTone } from "@/lib/format";
import { useApi } from "@/lib/use-api";

function rate(value: number | null): string {
  return value === null ? "Not enough data" : Math.round(value * 100) + "%";
}

export default function WorkflowsPage() {
  const metrics = useApi(() => api.workflowMetrics());
  return <><PageHeader title="Workflows" description="Deterministic health is calculated from persisted, sanitized execution history. Unknown means there is not enough classified history yet." />
    <section className="panel overflow-hidden">
      <div className="panel-header"><div><h2>Monitored workflows</h2><p>Latest 20 classified executions per workflow; n8n remains read-only.</p></div></div>
      {metrics.loading && <StatePanel title="Loading workflow health" message="Requesting persisted monitoring history." />}
      {metrics.error && <StatePanel title="Workflow health unavailable" message={metrics.error.message} action={<button className="button button-secondary" onClick={() => void metrics.refresh()}>Try again</button>} />}
      {!metrics.loading && !metrics.error && metrics.data?.data.length === 0 && <StatePanel title="Not enough data yet" message="Connect n8n and wait for monitoring to observe executions. No workflow health is inferred without persisted history." />}
      {!metrics.loading && !metrics.error && (metrics.data?.data.length ?? 0) > 0 && <div className="data-table-wrap"><table className="data-table"><thead><tr><th>Workflow</th><th>Health</th><th>Last checked</th><th>Last success</th><th>Last failure</th><th>Success rate</th><th>Open incidents</th></tr></thead><tbody>{metrics.data?.data.map((workflow) => <tr key={workflow.workflow_id}><td className="font-medium text-slate-100">{workflow.workflow_name}<span className="subtle">{workflow.workflow_id} · {workflow.recent_executions_count} recent executions</span></td><td><span className={healthTone(workflow.health)}>{workflow.health}</span></td><td>{formatTimestamp(workflow.last_checked_at)}</td><td>{formatTimestamp(workflow.last_successful_execution_at)}</td><td>{formatTimestamp(workflow.last_failed_execution_at)}</td><td>{rate(workflow.success_rate)}</td><td>{workflow.open_incident_count}</td></tr>)}</tbody></table></div>}
    </section>
  </>;
}

"use client";

import { PageHeader } from "@/components/page-header";
import { StatePanel } from "@/components/state-panel";
import { Timestamp } from "@/components/timestamp";
import { api } from "@/lib/api";
import { healthTone } from "@/lib/format";
import { useApi } from "@/lib/use-api";

function SuccessRate({ value }: Readonly<{ value: number | null }>) {
  if (value === null) return <span className="muted-text">Not enough data</span>;
  const percent = Math.round(value * 100);
  return <span className="meter"><span className="meter-track" aria-hidden="true"><span className="meter-fill" style={{ width: percent + "%" }} /></span><span>{percent}%</span></span>;
}

export default function WorkflowsPage() {
  const metrics = useApi(() => api.workflowMetrics());
  return <><PageHeader eyebrow="Monitoring" title="Workflows" description="Deterministic health is calculated from persisted, sanitized execution history. Unknown means there is not enough classified history yet." />
    <section className="panel overflow-hidden">
      <div className="panel-header"><div><h2>Monitored workflows</h2><p>Latest 20 classified executions per workflow; n8n remains read-only.</p></div></div>
      {metrics.loading && <StatePanel inline tone="loading" title="Loading workflow health" message="Requesting persisted monitoring history." />}
      {metrics.error && <StatePanel inline tone="error" title="Workflow health unavailable" message={metrics.error.message} action={<button type="button" className="button button-secondary" onClick={() => void metrics.refresh()}>Try again</button>} />}
      {!metrics.loading && !metrics.error && metrics.data?.data.length === 0 && <StatePanel inline title="Not enough data yet" message="Connect n8n and wait for monitoring to observe executions. No workflow health is inferred without persisted history." />}
      {!metrics.loading && !metrics.error && (metrics.data?.data.length ?? 0) > 0 && <div className="data-table-wrap" role="region" aria-label="Monitored workflows table" tabIndex={0}><table className="data-table"><caption className="sr-only">Monitored workflows and their health</caption><thead><tr><th scope="col">Workflow</th><th scope="col">Health</th><th scope="col">Last checked</th><th scope="col">Last success</th><th scope="col">Last failure</th><th scope="col">Success rate</th><th scope="col">Open incidents</th></tr></thead><tbody>{metrics.data?.data.map((workflow) => <tr key={workflow.workflow_id}><td><span className="font-semibold text-[var(--text-strong)]">{workflow.workflow_name}</span><span className="subtle"><span className="mono">{workflow.workflow_id}</span> · {workflow.recent_executions_count} recent executions</span></td><td><span className={healthTone(workflow.health)}>{workflow.health}</span></td><td><Timestamp value={workflow.last_checked_at} /></td><td><Timestamp value={workflow.last_successful_execution_at} /></td><td><Timestamp value={workflow.last_failed_execution_at} /></td><td><SuccessRate value={workflow.success_rate} /></td><td>{workflow.open_incident_count}</td></tr>)}</tbody></table></div>}
    </section>
  </>;
}

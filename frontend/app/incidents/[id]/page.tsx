"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { DiagnosisPanel } from "@/components/diagnosis-panel";
import { PageHeader } from "@/components/page-header";
import { StatePanel } from "@/components/state-panel";
import { api } from "@/lib/api";
import { badgeTone, formatTimestamp } from "@/lib/format";
import type { Incident } from "@/lib/types";
import { useApi } from "@/lib/use-api";

export default function IncidentDetailPage() {
  const params = useParams<{ id: string }>();
  const incident = useApi(() => api.incident(params.id), params.id);

  const [resolving, setResolving] = useState(false);
  const [resolveError, setResolveError] = useState<string | null>(null);

  async function resolve(current: Incident) {
    setResolving(true);
    setResolveError(null);
    try {
      await api.resolveIncident(current.id);
      await incident.refresh();
    } catch (reason) {
      setResolveError(reason instanceof Error ? reason.message : "Incident could not be resolved.");
    } finally {
      setResolving(false);
    }
  }

  async function diagnose(current: Incident): Promise<Incident> {
    const updated = await api.diagnose(current.id);
    await incident.refresh();
    return updated;
  }

  if (incident.loading) return <><PageHeader title="Incident detail" description="Loading the persisted incident." /><StatePanel title="Loading incident" message="Requesting sanitized incident details from FlowMedic." /></>;
  if (incident.error || !incident.data) return <><PageHeader title="Incident detail" description="Review failed workflow execution details." /><StatePanel title="Incident unavailable" message={incident.error?.message ?? "This incident could not be found."} action={<Link className="button button-secondary" href="/incidents">Back to incidents</Link>} /></>;
  const data = incident.data;
  return <><PageHeader title={data.workflow_name} description={`Incident ${data.id}`} action={<Link className="button button-secondary" href="/incidents">Back to incidents</Link>} />
    <div className="detail-grid"><section className="panel p-5 sm:p-6"><h2>Failure context</h2><dl className="detail-list mt-3"><div><dt>Incident status</dt><dd><span className={badgeTone(data.status)}>{data.status}</span></dd></div><div><dt>Severity</dt><dd><span className={badgeTone(data.severity)}>{data.severity}</span></dd></div><div><dt>Workflow name</dt><dd>{data.workflow_name}</dd></div><div><dt>Workflow ID</dt><dd>{data.workflow_id}</dd></div><div><dt>Execution ID</dt><dd>{data.execution_id}</dd></div><div><dt>Failed node</dt><dd>{data.failed_node ?? "Not available yet"}</dd></div><div><dt>Error type</dt><dd>{data.error_type}</dd></div><div><dt>Timestamp</dt><dd>{formatTimestamp(data.execution_timestamp)}</dd></div></dl>{data.status === "resolved" ? <p className="notice mt-4">Resolved {formatTimestamp(data.resolved_at)}</p> : <button className="button button-secondary mt-4" onClick={() => resolve(data)} disabled={resolving}>{resolving ? "Resolving…" : "Mark resolved"}</button>}{resolveError && <p className="mt-3 text-sm text-rose-100" role="alert">{resolveError}</p>}<div className="mt-5"><p className="text-xs font-bold uppercase tracking-wider text-slate-400">Sanitized error message</p><p className="message-box">{data.error_message}</p></div></section><DiagnosisPanel incident={data} onDiagnosed={diagnose} /></div>
  </>;
}

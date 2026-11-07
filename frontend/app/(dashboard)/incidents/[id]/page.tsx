"use client";

import { useConnectionScope } from "@/components/connection-scope";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { useAccount } from "@/components/account-provider";
import { DiagnosisPanel } from "@/components/diagnosis-panel";
import { PageHeader } from "@/components/page-header";
import { StatePanel } from "@/components/state-panel";
import { Timestamp } from "@/components/timestamp";
import { api } from "@/lib/api";
import { badgeTone } from "@/lib/format";
import type { Incident } from "@/lib/types";
import { useApi } from "@/lib/use-api";

export default function IncidentDetailPage() {
  const account = useAccount();
  const {workspaceId, instances} = useConnectionScope();
  const params = useParams<{ id: string }>();
  const incident = useApi(() => api.incident(params.id), `${workspaceId}:incident:${params.id}`);

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
    // "Run again" is the explicit opt-in to repeat (paid) diagnosis.
    const updated = await api.diagnose(current.id, current.diagnosis !== null);
    await incident.refresh();
    return updated;
  }

  if (incident.loading) return <><PageHeader eyebrow="Incident" title="Incident detail" description="Loading the persisted incident." /><StatePanel tone="loading" title="Loading incident" message="Requesting sanitized incident details from FlowMedic." /></>;
  if (incident.error || !incident.data) return <><PageHeader eyebrow="Incident" title="Incident detail" description="Review failed workflow execution details." /><StatePanel tone="error" title="Incident unavailable" message={incident.error?.message ?? "This incident could not be found."} action={<Link className="button button-secondary" href="/incidents">Back to incidents</Link>} /></>;
  const data = incident.data;
  return <><PageHeader eyebrow="Incident" title={data.workflow_name} description={`Instance ${instances.find(item => item.id === data.instance_id)?.name ?? data.instance_id} · Incident ${data.id}`} action={<Link className="button button-secondary" href="/incidents">Back to incidents</Link>} />
    <div className="detail-grid"><section className="panel p-5 sm:p-6"><h2>Failure context</h2><dl className="detail-list mt-3"><div><dt>Incident status</dt><dd><span className={badgeTone(data.status)}>{data.status}</span></dd></div><div><dt>Severity</dt><dd><span className={badgeTone(data.severity)}>{data.severity}</span></dd></div><div><dt>Workflow name</dt><dd>{data.workflow_name}</dd></div><div><dt>Workflow ID</dt><dd className="mono">{data.workflow_id}</dd></div><div><dt>Execution ID</dt><dd className="mono">{data.execution_id}</dd></div><div><dt>Failed node</dt><dd>{data.failed_node ?? <span className="muted-text">Not available yet</span>}</dd></div><div><dt>Error type</dt><dd>{data.error_type}</dd></div><div><dt>Timestamp</dt><dd><Timestamp value={data.execution_timestamp} /></dd></div></dl>{data.status === "resolved" ? <p className="notice mt-4">Resolved <Timestamp value={data.resolved_at} /></p> : <button type="button" className="button button-secondary mt-4" onClick={() => resolve(data)} disabled={resolving}>{resolving ? "Resolving…" : "Mark resolved"}</button>}{resolveError && <p className="mt-3 text-sm text-rose-100" role="alert">{resolveError}</p>}<div className="mt-5"><h3 className="label">Sanitized error message</h3><p className="message-box">{data.error_message}</p></div></section><DiagnosisPanel canDiagnose={account?.workspace.role === "admin"} incident={data} onDiagnosed={diagnose} /></div>
  </>;
}

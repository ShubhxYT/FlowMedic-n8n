"use client";

import { useConnectionScope } from "@/components/connection-scope";
import Link from "next/link";

import { Timestamp } from "@/components/timestamp";
import { badgeTone } from "@/lib/format";
import type { Incident } from "@/lib/types";

export function IncidentTable({ incidents, compact = false }: Readonly<{ incidents: Incident[]; compact?: boolean }>) {
  const {instances} = useConnectionScope();
  return (
    <div className="data-table-wrap" role="region" aria-label="Incidents table" tabIndex={0}>
      <table className="data-table">
        <caption className="sr-only">Persisted workflow incidents</caption>
        <thead><tr><th scope="col">Incident</th><th scope="col">Status</th><th scope="col">Severity</th><th scope="col">Failed node</th><th scope="col">Detected</th>{!compact && <th scope="col">Diagnosis</th>}</tr></thead>
        <tbody>
          {incidents.map((incident) => <tr key={incident.id}>
            <td><Link className="table-link" href={`/incidents/${incident.id}`}>{incident.workflow_name}</Link><span className="subtle">{instances.find(item => item.id === incident.instance_id)?.name ?? incident.instance_id.slice(0, 8)} · {incident.error_type} · <span className="mono">{incident.execution_id}</span></span></td>
            <td><span className={badgeTone(incident.status)}>{incident.status}</span></td>
            <td><span className={badgeTone(incident.severity)}>{incident.severity}</span></td>
            <td>{incident.failed_node ?? <span className="muted-text">Not available yet</span>}</td>
            <td><Timestamp value={incident.execution_timestamp} /></td>
            {!compact && <td>{incident.diagnosis ? <span className="badge badge-success">Available</span> : <span className="badge badge-muted">Not run</span>}</td>}
          </tr>)}
        </tbody>
      </table>
    </div>
  );
}

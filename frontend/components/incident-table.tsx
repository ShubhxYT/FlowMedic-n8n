import Link from "next/link";

import { badgeTone, formatTimestamp } from "@/lib/format";
import type { Incident } from "@/lib/types";

export function IncidentTable({ incidents, compact = false }: Readonly<{ incidents: Incident[]; compact?: boolean }>) {
  return (
    <div className="data-table-wrap">
      <table className="data-table">
        <thead><tr><th>Incident</th><th>Status</th><th>Severity</th><th>Failed node</th><th>Detected</th>{!compact && <th>Diagnosis</th>}</tr></thead>
        <tbody>
          {incidents.map((incident) => <tr key={incident.id}>
            <td><Link className="table-link" href={`/incidents/${incident.id}`}>{incident.workflow_name}</Link><span className="subtle">{incident.error_type} · {incident.execution_id}</span></td>
            <td><span className={badgeTone(incident.status)}>{incident.status}</span></td>
            <td><span className={badgeTone(incident.severity)}>{incident.severity}</span></td>
            <td>{incident.failed_node ?? "Not available yet"}</td>
            <td>{formatTimestamp(incident.execution_timestamp)}</td>
            {!compact && <td>{incident.diagnosis ? <span className="badge badge-success">Available</span> : <span className="badge badge-muted">Not run</span>}</td>}
          </tr>)}
        </tbody>
      </table>
    </div>
  );
}

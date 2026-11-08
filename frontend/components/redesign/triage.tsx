import { ArrowUpRight, FileText, ScanLine, TriangleAlert } from "lucide-react";
import { useCallback, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import type { Incident, ManagedConnection } from "@/lib/types";
import {
  Badge, Detail, Heading, Meta, Notice, PageFooter, Panel, SearchInput, Timestamp,
  dateNumber, time, usePages, useResource,
} from "./ui";
import type { PageProps } from "./ui";

export function prioritize(items: Incident[]) {
  return [...items].sort((a, b) => Number(b.severity === "high") - Number(a.severity === "high") ||
    dateNumber(b.execution_timestamp) - dateNumber(a.execution_timestamp));
}

export function IncidentDetail({ incident, connections, onClose }: {
  incident: Incident; connections: ManagedConnection[]; onClose: () => void;
}) {
  const load = useCallback(() => api.incident(incident.id), [incident.id]);
  const { data, loading, error } = useResource(load);
  const item = data ?? incident;
  const diagnosis = item.diagnosis;
  const instance = connections.find((connection) => connection.id === item.instance_id);
  return <Detail title={item.workflow_name} subtitle="Incident details" onClose={onClose}>
    <div className="detail-body">
      <div className="detail-badges"><Badge value={item.status} /><Badge value={item.severity} label={item.severity === "high" ? "High severity" : "Medium severity"} /></div>
      {error && <Notice kind="error" title="Could not refresh this incident">{error}</Notice>}
      {loading && <p className="subtle" role="status">Checking for updated details…</p>}
      <Link className="text-link" href={"/incidents/" + encodeURIComponent(item.id)}>Open incident <ArrowUpRight size={14} aria-hidden="true" /></Link>
      <h3><TriangleAlert size={17} aria-hidden="true" /> Failure context</h3>
      <dl className="facts">
        <div><dt>Failed node</dt><dd>{item.failed_node ?? "Unknown"}</dd></div>
        <div><dt>Error type</dt><dd>{item.error_type}</dd></div>
        <div><dt>Instance</dt><dd>{instance?.name ?? item.instance_id}</dd></div>
        <div><dt>Execution</dt><dd>{item.execution_id}</dd></div>
        <div><dt>Execution time</dt><dd>{time(item.execution_timestamp)}</dd></div>
        {item.resolved_at && <div><dt>Resolved</dt><dd>{time(item.resolved_at)}</dd></div>}
      </dl>
      <div className="error-box"><strong>Sanitized error</strong><p>{item.error_message || "No error message available."}</p></div>
      <h3><ScanLine size={18} aria-hidden="true" /> Saved AI diagnosis</h3>
      {diagnosis ? <>
        <div className="diagnosis-summary"><p>{diagnosis.summary}</p>
          <div className="detail-badges"><span className="pill">{Math.round(diagnosis.confidence * 100)}% confidence</span>
            <Badge value={diagnosis.risk_level} label={diagnosis.risk_level + " risk"} /></div>
        </div>
        <dl className="diagnosis-fields">
          <div><dt>Probable root cause</dt><dd>{diagnosis.probable_root_cause}</dd></div>
          <div><dt>Affected component</dt><dd>{diagnosis.affected_component}</dd></div>
          <div><dt>Recommended fix</dt><dd>{diagnosis.recommended_fix}</dd></div>
        </dl>
        <p className="subtle">Advisory diagnosis{item.diagnosis_provider ? " from " + item.diagnosis_provider : ""}. Review before changing workflows.</p>
      </> : <Notice title="No saved diagnosis">A diagnosis will appear here when one is available in FlowMedic.</Notice>}
    </div>
  </Detail>;
}

const incidentStates = ["unresolved", "open", "diagnosed", "resolved", "all"];

export function Incidents({ instance, connections, filter }: PageProps & { filter: string | null }) {
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState(filter && incidentStates.includes(filter) ? filter : "unresolved");
  const [severity, setSeverity] = useState("all");
  const [selected, setSelected] = useState<Incident | null>(null);
  const load = useCallback(async (offset: number) => (await api.incidents(instance, 50, offset)).data, [instance]);
  const page = usePages(load, 50);
  const term = search.trim().toLocaleLowerCase();
  const instanceName = (id: string) => connections.find((item) => item.id === id)?.name ?? id.slice(0, 8);
  const visible = prioritize(page.rows.filter((item) =>
    (status === "all" || (status === "unresolved" ? item.status !== "resolved" : item.status === status)) &&
    (severity === "all" || item.severity === severity) &&
    (!term || [item.workflow_name, item.failed_node, item.error_type, item.execution_id, instanceName(item.instance_id)]
      .some((value) => value?.toLocaleLowerCase().includes(term)))));
  return <>
    <Heading kicker="Incident triage" title="Incidents" description="Investigate workflow failures and review saved diagnoses." />
    <Panel title="Incident queue" description="Search and filters apply to loaded incidents. High severity appears first.">
      <div className="filters">
        <SearchInput label="Search loaded incidents" placeholder="Search loaded incidents…" value={search} onChange={setSearch} />
        <label className="filter-label">Status<select value={status} onChange={(event) => setStatus(event.target.value)}>
          <option value="unresolved">Unresolved</option><option value="open">Open</option><option value="diagnosed">Diagnosed</option>
          <option value="resolved">Resolved</option><option value="all">All statuses</option>
        </select></label>
        <label className="filter-label">Severity<select value={severity} onChange={(event) => setSeverity(event.target.value)}>
          <option value="all">All severities</option><option value="high">High</option><option value="medium">Medium</option>
        </select></label>
      </div>
      {page.error && <Notice kind="error" title={page.error} />}
      {page.loading && !page.rows.length && <Notice kind="loading" title="Loading incidents" />}
      {!visible.length && !page.loading && !page.error && <Notice title="No matching incidents">
        {page.more ? "Load more incidents to expand your search." : "Try another status, severity, or search term."}
      </Notice>}
      {!!visible.length && <div className="table-wrap"><table className="data-table">
        <caption className="sr-only">Loaded workflow incidents</caption>
        <thead><tr><th scope="col">Workflow</th><th scope="col">Severity</th><th scope="col">Status</th>
          <th scope="col">Execution time</th><th scope="col">Diagnosis</th></tr></thead>
        <tbody>{visible.map((item) => <tr key={item.id}>
          <th scope="row"><button className="row-link" onClick={() => setSelected(item)}>{item.workflow_name}<ArrowUpRight size={14} aria-hidden="true" /></button>
            <Meta>{instanceName(item.instance_id)} · {item.failed_node ?? item.error_type}</Meta></th>
          <td data-label="Severity"><Badge value={item.severity} /></td>
          <td data-label="Status"><Badge value={item.status} /></td>
          <td data-label="Execution time"><Timestamp value={item.execution_timestamp} /></td>
          <td data-label="Diagnosis">{item.diagnosis ? <span className="inline"><FileText size={15} aria-hidden="true" />Available</span> : <span className="subtle">Unavailable</span>}</td>
        </tr>)}</tbody>
      </table></div>}
      <PageFooter count={page.rows.length} more={page.more} loading={page.loading} loadMore={() => void page.loadMore()} />
    </Panel>
    {selected && <IncidentDetail incident={selected} connections={connections} onClose={() => setSelected(null)} />}
  </>;
}

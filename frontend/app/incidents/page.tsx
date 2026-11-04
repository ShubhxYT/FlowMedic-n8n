"use client";

import { useMemo, useState } from "react";

import { IncidentTable } from "@/components/incident-table";
import { PageHeader } from "@/components/page-header";
import { StatePanel } from "@/components/state-panel";
import { api } from "@/lib/api";
import type { IncidentStatus, Severity } from "@/lib/types";
import { useApi } from "@/lib/use-api";

const pageSize = 20;

export default function IncidentsPage() {
  const [page, setPage] = useState(0);
  const [severity, setSeverity] = useState<Severity | "all">("all");
  const [status, setStatus] = useState<IncidentStatus | "all">("all");
  const [query, setQuery] = useState("");
  const incidents = useApi(() => api.incidents(pageSize, page * pageSize), `page-${page}`);

  const rows = useMemo(() => (incidents.data?.data ?? []).filter((incident) => {
    const matchesSeverity = severity === "all" || incident.severity === severity;
    const matchesStatus = status === "all" || incident.status === status;
    const text = `${incident.workflow_name} ${incident.error_type} ${incident.failed_node ?? ""}`.toLowerCase();
    return matchesSeverity && matchesStatus && text.includes(query.toLowerCase());
  }), [incidents.data, query, severity, status]);

  return <><PageHeader title="Incidents" description="Search and review persisted workflow failures. Filters and search apply to the current backend page because the Phase 1 API does not provide server-side filtering." />
    <section className="panel overflow-hidden">
      <div className="filters"><label className="sr-only" htmlFor="incident-search">Search current incident page</label><input id="incident-search" className="field search" placeholder="Search current page" value={query} onChange={(event) => setQuery(event.target.value)} /><label className="sr-only" htmlFor="severity-filter">Severity</label><select id="severity-filter" className="field" value={severity} onChange={(event) => setSeverity(event.target.value as Severity | "all")}><option value="all">All severities</option><option value="high">High</option><option value="medium">Medium</option></select><label className="sr-only" htmlFor="status-filter">Status</label><select id="status-filter" className="field" value={status} onChange={(event) => setStatus(event.target.value as IncidentStatus | "all")}><option value="all">All states</option><option value="open">Open</option><option value="diagnosed">Diagnosed</option></select></div>
      {incidents.loading && <StatePanel title="Loading incidents" message="Requesting this page from the FlowMedic backend." />}
      {incidents.error && <StatePanel title="Incidents unavailable" message={incidents.error.message} action={<button className="button button-secondary" onClick={() => void incidents.refresh()}>Try again</button>} />}
      {!incidents.loading && !incidents.error && rows.length === 0 && <StatePanel title={incidents.data?.data.length ? "No incidents match these filters" : "No incidents yet"} message={incidents.data?.data.length ? "Try changing filters or search terms." : "Connect n8n and sync failed executions to populate this dashboard."} />}
      {!incidents.loading && !incidents.error && rows.length > 0 && <IncidentTable incidents={rows} />}
      <div className="pagination"><span>Page {page + 1} · up to {pageSize} incidents</span><div className="flex gap-2"><button className="button button-secondary" disabled={page === 0 || incidents.loading} onClick={() => setPage((value) => Math.max(0, value - 1))}>Previous</button><button className="button button-secondary" disabled={(incidents.data?.data.length ?? 0) < pageSize || incidents.loading} onClick={() => setPage((value) => value + 1)}>Next</button></div></div>
    </section>
  </>;
}

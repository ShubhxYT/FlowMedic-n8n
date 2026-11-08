import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { ManagedConnection, Workflow, WorkflowHealth } from "@/lib/types";
import { Badge, Heading, Meta, Notice, Outbound, Panel, SearchInput, time, useResource } from "./ui";
import type { PageProps } from "./ui";

function n8nWorkflowUrl(base: string | null, id: string): string | null {
  if (!base || !/^[a-zA-Z0-9_-]{1,255}$/.test(id)) return null;
  try {
    const url = new URL(base);
    if (!["https:", "http:"].includes(url.protocol) || url.username || url.password || url.search || url.hash) return null;
    url.pathname = url.pathname.replace(/\/api\/v1\/?$/, "").replace(/\/$/, "") + "/workflow/" + encodeURIComponent(id);
    return url.href;
  } catch {
    return null;
  }
}

interface Catalog {
  connection: ManagedConnection;
  rows: Workflow[];
  next?: string | null;
  error?: string;
}

const workflowKey = (instance: string, workflow: string) => JSON.stringify([instance, workflow]);

function useCatalogs({ connections, instance }: PageProps) {
  const [catalogs, setCatalogs] = useState<Catalog[]>([]);
  const [loading, setLoading] = useState(true);
  const generation = useRef(0);
  const fetchPages = useCallback(async (targets: Catalog[]) => {
    const results: Catalog[] = [];
    for (let index = 0; index < targets.length; index += 4) {
      const chunk = targets.slice(index, index + 4);
      const pages = await Promise.allSettled(chunk.map(({ connection, next }) =>
        api.workflows(connection.id, 50, next ?? undefined)));
      pages.forEach((page, pageIndex) => {
        const target = chunk[pageIndex];
        if (page.status === "rejected") {
          results.push({ ...target, error: "Catalog unavailable" });
        } else {
          results.push({
            connection: target.connection,
            rows: [...new Map([...target.rows, ...page.value.data].map((row) => [row.id, row])).values()],
            next: page.value.next_cursor === target.next ? null : page.value.next_cursor,
          });
        }
      });
    }
    return results;
  }, []);

  useEffect(() => {
    const token = ++generation.current;
    const targets = connections.filter((entry) => entry.enabled && (!instance || entry.id === instance))
      .map((connection) => ({ connection, rows: [] }));
    void Promise.resolve().then(() => {
      if (generation.current !== token) return [];
      setCatalogs([]);
      setLoading(true);
      return fetchPages(targets);
    }).then((result) => {
      if (generation.current === token) {
        setCatalogs(result);
        setLoading(false);
      }
    });
    return () => { generation.current = token + 1; };
  }, [fetchPages, connections, instance]);

  async function loadMore() {
    const targets = catalogs.filter((catalog) => catalog.next);
    if (loading || !targets.length) return;
    const token = generation.current;
    setLoading(true);
    const updates = new Map((await fetchPages(targets)).map((item) => [item.connection.id, item]));
    if (generation.current !== token) return;
    setCatalogs((previous) => previous.map((item) => updates.get(item.connection.id) ?? item));
    setLoading(false);
  }
  return { catalogs, loading, loadMore };
}

interface Row {
  key: string;
  name: string;
  instanceId: string;
  workflowId: string;
  health?: WorkflowHealth;
  live?: Workflow;
  href?: string | null;
}
const order = { unhealthy: 0, degraded: 1, unknown: 2, healthy: 3 };
const percentage = new Intl.NumberFormat(undefined, { style: "percent", maximumFractionDigits: 1 });

export function Workflows({ filter, ...props }: PageProps & { filter: string | null }) {
  const { instance, connections } = props;
  const [search, setSearch] = useState("");
  const [health, setHealth] = useState(filter && Object.hasOwn(order, filter) ? filter : "all");
  const load = useCallback(async () => (await api.workflowMetrics(instance)).data, [instance]);
  const history = useResource(load);
  const { catalogs, loading, loadMore } = useCatalogs(props);
  const rows = new Map<string, Row>();
  for (const item of history.data ?? []) {
    const key = workflowKey(item.instance_id, item.workflow_id);
    rows.set(key, { key, name: item.workflow_name, instanceId: item.instance_id, workflowId: item.workflow_id, health: item });
  }
  for (const catalog of catalogs) {
    for (const live of catalog.rows) {
      if (live.instance_id !== catalog.connection.id) continue;
      const key = workflowKey(live.instance_id, live.id);
      rows.set(key, {
        key, name: live.name, instanceId: live.instance_id, workflowId: live.id, health: rows.get(key)?.health,
        live, href: n8nWorkflowUrl(catalog.connection.base_url, live.id),
      });
    }
  }
  const term = search.trim().toLocaleLowerCase();
  const visible = [...rows.values()].filter((row) =>
    (health === "all" || (row.health?.health ?? "unknown") === health) &&
    (!term || [row.name, row.workflowId, row.health?.workflow_name].some((value) => value?.toLocaleLowerCase().includes(term))))
    .sort((a, b) => order[a.health?.health ?? "unknown"] - order[b.health?.health ?? "unknown"] || a.name.localeCompare(b.name));
  const failures = catalogs.filter((catalog) => catalog.error);
  const hasMore = catalogs.some((catalog) => catalog.next);
  return <>
    <Heading kicker="Workflow monitoring" title="Workflows" description="Find failing automations and open the matching workflow in n8n." />
    <Panel title="Workflow inventory" description="Health comes from up to 20 classified executions per workflow.">
      <div className="filters">
        <SearchInput label="Search workflows" placeholder="Search by workflow name or ID…" value={search} onChange={setSearch} />
        <label className="filter-label">Health<select value={health} onChange={(event) => setHealth(event.target.value)}>
          <option value="all">All health states</option><option value="unhealthy">Unhealthy</option><option value="degraded">Degraded</option>
          <option value="unknown">Unknown</option><option value="healthy">Healthy</option>
        </select></label>
      </div>
      {history.error && <Notice kind="error" title="Workflow health is unavailable">{history.error}</Notice>}
      {!!failures.length && <Notice kind="error" title="Some live workflow links could not be loaded">
        {failures.map((item) => item.connection.name).join(", ")}. Persisted health is shown where available.
      </Notice>}
      {(loading || history.loading) && <Notice kind="loading" title="Loading workflow data" />}
      {!visible.length && !loading && !history.loading && <Notice title="No matching workflows">Try changing the health filter or search term.</Notice>}
      {!!visible.length && <div className="table-wrap"><table className="data-table">
        <caption className="sr-only">Workflow inventory and health</caption>
        <thead><tr><th scope="col">Workflow</th><th scope="col">Health</th><th scope="col">Success rate</th>
          <th scope="col">Last checked</th><th scope="col">Last success</th><th scope="col">Last failure</th><th scope="col">Open incidents</th><th scope="col">n8n</th></tr></thead>
        <tbody>{visible.map((row) => <tr key={row.key}>
          <th scope="row"><strong>{row.name}</strong>
            <Meta>{connections.find((item) => item.id === row.instanceId)?.name ?? row.instanceId.slice(0, 8)}
              {row.live ? " · " + (row.live.active ? "Active" : "Inactive") : ""}</Meta></th>
          <td data-label="Health"><Badge value={row.health?.health ?? "unknown"} /></td>
          <td data-label="Success rate" className="numeric">{row.health?.success_rate == null ? "—" : percentage.format(row.health.success_rate)}</td>
          <td data-label="Last checked">{time(row.health?.last_checked_at ?? null)}</td>
          <td data-label="Last success">{time(row.health?.last_successful_execution_at ?? null)}</td>
          <td data-label="Last failure">{time(row.health?.last_failed_execution_at ?? null)}</td>
          <td data-label="Open incidents" className="numeric">{row.health?.open_incident_count ?? "—"}</td>
          <td data-label="n8n">{row.href ? <Outbound href={row.href}>Open in n8n<span className="sr-only"> · {row.name}</span></Outbound>
            : <span className="subtle">Unavailable</span>}</td>
        </tr>)}</tbody>
      </table></div>}
      <div className="panel-footer">
        <span>{visible.length} shown · {rows.size} loaded</span>
        {hasMore && <button className="button" disabled={loading} onClick={() => void loadMore()}>Load more live workflows</button>}
      </div>
    </Panel>
  </>;
}

"use client";

import { useState } from "react";
import { WorkspaceMembers } from "@/components/workspace-members";
import { ConnectionSettings } from "@/components/connection-settings";
import { useConnectionScope } from "@/components/connection-scope";
import { PageHeader } from "@/components/page-header";
import { StatePanel } from "@/components/state-panel";
import { api, ApiError } from "@/lib/api";
import { formatTimestamp } from "@/lib/format";
import { useApi } from "@/lib/use-api";

export default function SettingsPage() {
  const scope = useConnectionScope();
  const revisionKey = scope.instances.map(item => `${item.id}:${item.revision}`).join(",");
  const key = `${scope.workspaceId}:${scope.instanceId ?? "all"}:${revisionKey}`;
  return <>
    <PageHeader title="Settings & connections" description="Connect your n8n instances and choose an AI provider for this workspace." />
    <div className="settings-grid connection-grid"><ConnectionSettings /></div>
    <SettingsStatus key={key} queryKey={key} />
    <WorkspaceMembers />
  </>;
}

function SettingsStatus({queryKey}: {queryKey: string}) {
  const scope = useConnectionScope();
  const health = useApi(api.health, `${queryKey}:health`);
  const system = useApi(api.systemStatus, `${queryKey}:system`);
  const monitoring = useApi(() => api.monitoringStatus(scope.instanceId), `${queryKey}:monitoring`);
  const [pending, setPending] = useState(false);
  const [feedback, setFeedback] = useState("");
  const selected = scope.instances.find(item => item.id === scope.instanceId);
  const error = health.error ?? system.error ?? monitoring.error;
  async function sync() {
    if (!selected?.enabled || pending) return;
    setPending(true);
    setFeedback("");
    try {
      await api.monitoringSync(selected.id);
      await monitoring.refresh();
      setFeedback("Sync finished. Monitoring status has been refreshed.");
    } catch (reason) {
      setFeedback(reason instanceof ApiError ? reason.message : "Sync could not be completed.");
    } finally { setPending(false); }
  }
  return <section className="settings-status" aria-label="Operational status">
    <div className="panel-header"><div><h2>Operational status</h2><p>{selected ? `Monitoring ${selected.name}` : "Monitoring across this workspace"}</p></div>
      {scope.role === "admin" && selected && <button className="button button-secondary" disabled={!selected.enabled || pending} onClick={() => void sync()}>{pending ? "Syncing…" : "Sync now"}</button>}
    </div>
    {feedback && <p role="status" className="notice">{feedback}</p>}
    {error && <StatePanel title="Status unavailable" message={error.message} action={<button className="button button-secondary" onClick={() => {void health.refresh(); void system.refresh(); void monitoring.refresh();}}>Try again</button>} />}
    {health.loading || system.loading || monitoring.loading ? <p role="status">Loading operational status…</p> : <div className="settings-grid">
      <article className="panel setting-card"><h2>Backend health</h2><strong>{health.data?.status === "ok" ? "Operational" : "Unavailable"}</strong><p>{system.data?.database_engine ?? "Unknown"} database · {system.data?.enabled_n8n_count ?? 0} enabled n8n instances</p></article>
      <article className="panel setting-card"><h2>History retention</h2><strong>{system.data?.retention_days ?? "Unavailable"} days</strong><p>Execution history is retained separately from incidents.</p></article>
      {monitoring.data?.items.map(item => <article key={item.instance_id} className="panel setting-card"><h2>{item.instance_name ?? item.instance_id}</h2><strong>{item.configuration_state === "configuration_error" ? "Configuration needs attention" : item.state}</strong><p>{item.enabled ? `Checks every ${item.poll_interval_seconds}s · Lease ${item.lease_state}` : "Disabled — enable this connection when you are ready."}</p><p>Last fresh poll: {formatTimestamp(item.last_fresh_poll_at)}</p><p>Last successful sync: {formatTimestamp(item.last_successful_sync_at)}</p><p>{item.backfill_pending ? "Historical backfill pending" : "No pending backfill"} · Last cleanup: {formatTimestamp(item.last_retention_at)}</p>{item.last_error_summary && <p role="alert">{item.last_error_summary}</p>}</article>)}
      {monitoring.data?.items.length === 0 && <article className="panel setting-card"><h2>Monitoring</h2><p>Add an n8n instance above to begin.</p></article>}
    </div>}
  </section>;
}

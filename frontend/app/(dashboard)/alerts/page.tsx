"use client";

import { useConnectionScope } from "@/components/connection-scope";

import { PageHeader } from "@/components/page-header";
import { StatePanel } from "@/components/state-panel";
import { Timestamp } from "@/components/timestamp";
import { api } from "@/lib/api";
import { useApi } from "@/lib/use-api";

const labels = {
  new_incident: "New incident",
  workflow_unhealthy: "Workflow unhealthy",
  monitoring_degraded: "Monitoring degraded",
};

const deliveryTone = {
  delivered: "badge badge-success",
  failed: "badge badge-danger",
  pending: "badge badge-warning",
};

export default function AlertsPage() {
  const {workspaceId, instanceId, instances} = useConnectionScope();
  const rules = useApi(() => api.alertRules(), `${workspaceId}:rules`);
  const events = useApi(() => api.alertEvents(instanceId), `${workspaceId}:${instanceId ?? "all"}:events`);
  const error = rules.error ?? events.error;

  return (
    <>
      <PageHeader
        eyebrow="Notifications"
        title="Alerts"
        description="Persistent alert decisions and delivery attempts. Rules are disabled by default."
      />
      {error ? (
        <StatePanel
          tone="error"
          title="Alerts unavailable"
          message={error.message}
          action={<button type="button" className="button button-secondary" onClick={() => { void rules.refresh(); void events.refresh(); }}>Try again</button>}
        />
      ) : rules.loading || events.loading ? (
        <StatePanel tone="loading" title="Loading alert history" message="Requesting persisted alert rules and delivery state." />
      ) : (
        <div className="space-y-6">
          <section className="panel overflow-hidden">
            <div className="panel-header"><div><h2>Alert rules</h2><p>Only explicitly enabled rules can create alert events.</p></div></div>
            {(rules.data?.data.length ?? 0) === 0 ? (
              <StatePanel inline title="No alert rules configured" message="Create an explicitly enabled rule through the API. Webhook delivery requires server-side configuration." />
            ) : (
              <div className="data-table-wrap" role="region" aria-label="Alert rules table" tabIndex={0}><table className="data-table"><caption className="sr-only">Configured alert rules</caption><thead><tr><th scope="col">Rule</th><th scope="col">Trigger</th><th scope="col">State</th><th scope="col">Cooldown</th><th scope="col">Provider</th></tr></thead><tbody>{rules.data?.data.map((rule) => <tr key={rule.id}><td><strong>{rule.name}</strong></td><td>{labels[rule.trigger_type]}</td><td><span className={rule.enabled ? "badge badge-success" : "badge badge-muted"}>{rule.enabled ? "Enabled" : "Disabled"}</span></td><td>{rule.cooldown_seconds}s</td><td>{rule.delivery_provider === "webhook" ? "Webhook" : "Local mock"}</td></tr>)}</tbody></table></div>
            )}
          </section>
          <section className="panel overflow-hidden">
            <div className="panel-header"><div><h2>Alert history</h2><p>Delivery state is persisted; configured webhooks receive a minimal signed payload.</p></div></div>
            {(events.data?.data.length ?? 0) === 0 ? (
              <StatePanel inline title="No alert events yet" message="Events appear after an enabled rule matches a new incident or state transition." />
            ) : (
              <div className="data-table-wrap" role="region" aria-label="Alert history table" tabIndex={0}><table className="data-table"><caption className="sr-only">Alert delivery history</caption><thead><tr><th scope="col">Trigger</th><th scope="col">Target</th><th scope="col">Delivery</th><th scope="col">Created</th><th scope="col">Completed</th></tr></thead><tbody>{events.data?.data.map((event) => <tr key={event.id}><td>{labels[event.trigger_type]}</td><td>{event.incident_id ? "Incident" : event.workflow_id ?? "Monitoring"}<span className="subtle">{instances.find(item => item.id === event.instance_id)?.name ?? event.instance_id.slice(0, 8)}</span></td><td><span className={deliveryTone[event.status]}>{event.status}</span></td><td><Timestamp value={event.created_at} /></td><td><Timestamp value={event.delivered_at} /></td></tr>)}</tbody></table></div>
            )}
          </section>
        </div>
      )}
    </>
  );
}

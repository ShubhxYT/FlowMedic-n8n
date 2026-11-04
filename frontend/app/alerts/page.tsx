"use client";

import { PageHeader } from "@/components/page-header";
import { StatePanel } from "@/components/state-panel";
import { formatTimestamp } from "@/lib/format";
import { api } from "@/lib/api";
import { useApi } from "@/lib/use-api";

const labels = {
  new_incident: "New incident",
  workflow_unhealthy: "Workflow unhealthy",
  monitoring_degraded: "Monitoring degraded",
};

export default function AlertsPage() {
  const rules = useApi(() => api.alertRules());
  const events = useApi(() => api.alertEvents());
  const error = rules.error ?? events.error;

  return (
    <>
      <PageHeader
        title="Alerts"
        description="Persistent alert decisions and delivery attempts. Rules are disabled by default."
      />
      {error ? (
        <StatePanel
          title="Alerts unavailable"
          message={error.message}
          action={<button className="button button-secondary" onClick={() => { void rules.refresh(); void events.refresh(); }}>Try again</button>}
        />
      ) : rules.loading || events.loading ? (
        <StatePanel title="Loading alert history" message="Requesting persisted alert rules and delivery state." />
      ) : (
        <div className="space-y-6">
          <section className="panel overflow-hidden">
            <div className="panel-header"><div><h2>Alert rules</h2><p>Only explicitly enabled rules can create alert events.</p></div></div>
            {(rules.data?.data.length ?? 0) === 0 ? (
              <StatePanel title="No alert rules configured" message="Create an explicitly enabled rule through the API. Webhook delivery requires server-side configuration." />
            ) : (
              <div className="data-table-wrap"><table className="data-table"><thead><tr><th>Rule</th><th>Trigger</th><th>State</th><th>Cooldown</th><th>Provider</th></tr></thead><tbody>{rules.data?.data.map((rule) => <tr key={rule.id}><td><strong>{rule.name}</strong></td><td>{labels[rule.trigger_type]}</td><td><span className={rule.enabled ? "badge badge-success" : "badge"}>{rule.enabled ? "Enabled" : "Disabled"}</span></td><td>{rule.cooldown_seconds}s</td><td>{rule.delivery_provider === "webhook" ? "Webhook" : "Local mock"}</td></tr>)}</tbody></table></div>
            )}
          </section>
          <section className="panel overflow-hidden">
            <div className="panel-header"><div><h2>Alert history</h2><p>Delivery state is persisted; configured webhooks receive a minimal signed payload.</p></div></div>
            {(events.data?.data.length ?? 0) === 0 ? (
              <StatePanel title="No alert events yet" message="Events appear after an enabled rule matches a new incident or state transition." />
            ) : (
              <div className="data-table-wrap"><table className="data-table"><thead><tr><th>Trigger</th><th>Target</th><th>Delivery</th><th>Created</th><th>Completed</th></tr></thead><tbody>{events.data?.data.map((event) => <tr key={event.id}><td>{labels[event.trigger_type]}</td><td>{event.incident_id ? "Incident" : event.workflow_id ?? "Monitoring"}</td><td><span className={event.status === "delivered" ? "badge badge-success" : event.status === "failed" ? "badge badge-danger" : "badge"}>{event.status}</span></td><td>{formatTimestamp(event.created_at)}</td><td>{formatTimestamp(event.delivered_at)}</td></tr>)}</tbody></table></div>
            )}
          </section>
        </div>
      )}
    </>
  );
}

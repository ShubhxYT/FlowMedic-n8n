import { BellRing } from "lucide-react";
import { useCallback, useState } from "react";
import { api } from "@/lib/api";
import type { AlertEvent } from "@/lib/types";
import {
  Badge, Detail, Heading, Meta, Notice, PageFooter, Panel, Timestamp, dateNumber, display, usePages, useResource,
} from "./ui";
import type { PageProps } from "./ui";

function DeliveryDetail({ event, onClose }: {
  event: AlertEvent; onClose: () => void;
}) {
  const load = useCallback(async () => (await api.alertDeliveries(event.id)).data, [event.id]);
  const { data, loading, error } = useResource(load);
  return <Detail title="Delivery attempts" subtitle="Alert details" onClose={onClose}>
    <div className="detail-body">
      <div className="detail-badges"><Badge value={event.status} /><Timestamp value={event.created_at} /></div>
      <h3><BellRing size={17} aria-hidden="true" /> {display(event.trigger_type)}</h3>
      {event.last_error_summary && <div className="error-box"><strong>Last error</strong><p>{event.last_error_summary}</p></div>}
      {loading && <Notice kind="loading" title="Loading delivery attempts" />}
      {error && <Notice kind="error" title={error} />}
      {data?.length === 0 && <Notice title="No delivery attempts recorded" />}
      {!!data?.length && <ol className="detail-list">{[...data].sort((a, b) => b.attempt_number - a.attempt_number).map((item) =>
        <li key={item.id}><div><strong>Attempt {item.attempt_number} · {display(item.provider)}</strong><Badge value={item.status} /></div>
          <Timestamp value={item.completed_at ?? item.started_at} />{item.error_summary && <p>{item.error_summary}</p>}</li>)}</ol>}
    </div>
  </Detail>;
}

export function Alerts({ instance, connections }: PageProps) {
  const [selected, setSelected] = useState<AlertEvent | null>(null);
  const loadRules = useCallback(async () => (await api.alertRules()).data, []);
  const loadEvents = useCallback(async (offset: number) => (await api.alertEvents(instance, 50, offset)).data, [instance]);
  const rules = useResource(loadRules);
  const events = usePages(loadEvents, 50);
  const ordered = [...events.rows].sort((a, b) => dateNumber(b.created_at) - dateNumber(a.created_at));
  return <>
    <Heading kicker="Notifications" title="Alerts" description="See which alerts are enabled and whether notifications reached their destination." />
    <Panel title="Alert rules" description="Notification rules for the current workspace.">
      {rules.loading && !rules.data && <Notice kind="loading" title="Loading rules" />}
      {rules.error && <Notice kind="error" title={rules.error} />}
      {rules.data?.length === 0 && <Notice title="No alert rules configured">Rules appear here when they are configured in FlowMedic.</Notice>}
      {!!rules.data?.length && <ul className="simple-list">{rules.data.map((rule) => <li key={rule.id}>
        <div><strong>{rule.name}</strong><Meta>{display(rule.trigger_type)} · {display(rule.delivery_provider)} · {rule.cooldown_seconds}s cooldown</Meta></div>
        <Badge value={rule.enabled ? "enabled" : "disabled"} />
      </li>)}</ul>}
    </Panel>
    <Panel title="Delivery history" description="Select an event to inspect its delivery attempts.">
      {events.loading && !events.rows.length && <Notice kind="loading" title="Loading alert history" />}
      {events.error && <Notice kind="error" title={events.error} />}
      {!events.loading && !events.error && !events.rows.length && <Notice title="No alert events yet">Events appear when an enabled rule matches.</Notice>}
      {!!ordered.length && <div className="table-wrap"><table className="data-table">
        <caption className="sr-only">Alert delivery history</caption>
        <thead><tr><th scope="col">Event</th><th scope="col">Status</th><th scope="col">Created</th><th scope="col">Delivered</th></tr></thead>
        <tbody>{ordered.map((event) => <tr key={event.id}>
          <th scope="row"><button className="row-link" onClick={() => setSelected(event)}>{rules.data?.find((rule) => rule.id === event.rule_id)?.name ?? display(event.trigger_type)}</button>
            <Meta>{connections.find((item) => item.id === event.instance_id)?.name ?? event.instance_id.slice(0, 8)}</Meta></th>
          <td data-label="Status"><Badge value={event.status} /></td>
          <td data-label="Created"><Timestamp value={event.created_at} /></td>
          <td data-label="Delivered">{event.delivered_at ? <Timestamp value={event.delivered_at} /> : "—"}</td>
        </tr>)}</tbody>
      </table></div>}
      <PageFooter count={events.rows.length} more={events.more} loading={events.loading} loadMore={() => void events.loadMore()} />
    </Panel>
    {selected && <DeliveryDetail event={selected} onClose={() => setSelected(null)} />}
  </>;
}

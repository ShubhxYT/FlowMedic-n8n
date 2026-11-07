"use client";

import { useConnectionScope } from "@/components/connection-scope";

export function InstanceSelector() {
  const scope = useConnectionScope();
  return (
    <label className="scope-selector">
      n8n instance
      <select
        className="field"
        aria-label="n8n instance"
        value={scope.instanceId ?? ""}
        disabled={scope.loading}
        onChange={(event) => scope.setInstance(event.target.value || null)}
      >
        <option value="">All instances</option>
        {scope.instances.map((item) => (
          <option key={item.id} value={item.id}>
            {item.name} ({item.id.slice(0, 8)}){item.enabled ? "" : " — disabled"}
          </option>
        ))}
      </select>
    </label>
  );
}

"use client";

import { useConnectionScope } from "@/components/connection-scope";
import { Alerts } from "@/components/redesign/alerts";

export default function AlertsPage() {
  const { instanceId, instances } = useConnectionScope();
  return <Alerts key={instanceId} instance={instanceId} connections={instances} />;
}

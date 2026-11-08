"use client";

import { useConnectionScope } from "@/components/connection-scope";
import { Overview } from "@/components/redesign/overview";

export default function OverviewPage() {
  const { instanceId, instances } = useConnectionScope();
  return <Overview key={instanceId} instance={instanceId} connections={instances} />;
}

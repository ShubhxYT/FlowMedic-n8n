"use client";

import { useSearchParams } from "next/navigation";
import { useConnectionScope } from "@/components/connection-scope";
import { Workflows } from "@/components/redesign/workflows";

export default function WorkflowsPage() {
  const { instanceId, instances } = useConnectionScope();
  const filter = useSearchParams().get("health");
  return <Workflows key={JSON.stringify([instanceId, filter])} instance={instanceId} connections={instances} filter={filter} />;
}

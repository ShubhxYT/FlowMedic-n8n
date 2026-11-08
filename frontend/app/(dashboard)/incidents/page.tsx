"use client";

import { useSearchParams } from "next/navigation";
import { useConnectionScope } from "@/components/connection-scope";
import { Incidents } from "@/components/redesign/triage";

export default function IncidentsPage() {
  const { instanceId, instances } = useConnectionScope();
  const filter = useSearchParams().get("status");
  return <Incidents key={JSON.stringify([instanceId, filter])} instance={instanceId} connections={instances} filter={filter} />;
}

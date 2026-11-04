"use client";

import { api } from "@/lib/api";
import { useApi } from "@/lib/use-api";

export function SystemStatus() {
  const health = useApi(() => api.health());
  const label = health.loading ? "Checking backend" : health.data?.status === "ok" ? "Backend online" : "Backend unavailable";
  const tone = health.loading ? "bg-amber-300" : health.data?.status === "ok" ? "bg-emerald-300" : "bg-rose-400";
  return <div className="flex items-center gap-3 text-sm text-slate-300"><span className={`inline-block size-2 rounded-full ${tone}`} /><span className="hidden sm:inline">System status</span><span className="font-medium text-slate-100">{label}</span></div>;
}

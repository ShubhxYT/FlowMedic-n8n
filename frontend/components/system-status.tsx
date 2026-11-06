"use client";

import { api } from "@/lib/api";
import { useApi } from "@/lib/use-api";

export function SystemStatus() {
  const health = useApi(() => api.health());
  const label = health.loading ? "Checking backend" : health.data?.status === "ok" ? "Backend online" : "Backend unavailable";
  const tone = health.loading ? "status-dot-warn" : health.data?.status === "ok" ? "status-dot-ok" : "status-dot-danger";
  return <div className="status-pill" aria-live="polite"><span className={`status-dot ${tone}`} aria-hidden="true" /><span className="hidden sm:inline">System status</span><strong>{label}</strong></div>;
}

import type { IncidentStatus, RiskLevel, Severity, WorkflowHealthState } from "@/lib/types";

export function formatTimestamp(value: string | null): string {
  if (!value) return "Not available yet";
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? "Not available yet"
    : new Intl.DateTimeFormat("en", {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(date);
}

export function badgeTone(value: Severity | IncidentStatus | RiskLevel): string {
  if (value === "high") return "badge badge-danger";
  if (value === "medium") return "badge badge-warning";
  if (value === "open") return "badge badge-info";
  if (value === "diagnosed") return "badge badge-success";
  return "badge badge-muted";
}

export function healthTone(value: WorkflowHealthState): string {
  if (value === "healthy") return "badge badge-success";
  if (value === "degraded") return "badge badge-warning";
  if (value === "unhealthy") return "badge badge-danger";
  return "badge badge-muted";
}

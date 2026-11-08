import type {
  ApiErrorBody,
  CurrentUser,
  AlertDeliveryAttemptPage,
  AlertEventPage,
  AlertRulePage,
  HealthResponse,
  Incident,
  IncidentPage,
  N8nConnectionResponse,
  MonitoringStatusPage,
  ConnectionCollection,
  ConnectionInput,
  ConnectionPage,
  ConnectionTestResult,
  ManagedConnection,
  OverviewMetrics,
  SystemStatus,
  WorkflowHealthPage,
  WorkflowPage,
} from "@/lib/types";

import { csrfToken } from "@/lib/auth";

const apiBaseUrl = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api/backend").replace(
  /\/$/,
  "",
);

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl}${path}`, {
      ...options,
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json", ...(
        options?.method && !["GET", "HEAD"].includes(options.method)
          ? { "X-CSRF-Token": csrfToken() } : {}
      ), ...options?.headers },
    });
  } catch {
    throw new ApiError("FlowMedic backend is unavailable. Check that the API is running.", 0);
  }

  if (!response.ok) {
    if (response.status === 401 && typeof window !== "undefined" && !["/api/v1/auth/login", "/api/v1/auth/signup"].includes(path)) {
      window.dispatchEvent(new Event("flowmedic:session-expired"));
    }
    const body = (await response.json().catch(() => ({}))) as ApiErrorBody;
    throw new ApiError(
      body.error?.message ?? "The request could not be completed.",
      response.status,
      body.error?.code,
    );
  }
  return (await response.json()) as T;
}

const jsonBody = (value: unknown) => ({
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value),
});

export const api = {
  csrf: () => request<{ registration_enabled: boolean }>("/api/v1/auth/csrf"),
  me: () => request<CurrentUser>("/api/v1/auth/me"),
  login: (email: string, password: string) => request("/api/v1/auth/login", jsonBody({ email, password })),
  signup: (email: string, password: string) => request("/api/v1/auth/signup", jsonBody({ email, password })),
  logout: () => request("/api/v1/auth/logout", { method: "POST" }),
  selectWorkspace: (workspace_id: string) => request("/api/v1/auth/workspace", jsonBody({ workspace_id })),
  invite: (role: "admin" | "viewer") => request<{ url: string }>("/api/v1/auth/invites", jsonBody({ role })),
  acceptInvite: (token: string) => request("/api/v1/auth/invites/accept", jsonBody({ token })),
  members: () => request<{ data: { id: string; email: string; role: "admin" | "viewer" }[] }>("/api/v1/auth/members"),
  updateMember: (id: string, role: "admin" | "viewer") => request(`/api/v1/auth/members/${id}`, { ...jsonBody({ role }), method: "PATCH" }),
  removeMember: (id: string) => request(`/api/v1/auth/members/${id}`, { method: "DELETE" }),
  health: () => request<HealthResponse>("/health"),
  systemStatus: () => request<SystemStatus>("/api/v1/system/status"),
  monitoringStatus: (id: string | null = null) => request<MonitoringStatusPage>(scopedPath("/api/v1/monitoring/status", id)),
  monitoringSync: (id: string) => request(scopedPath("/api/v1/monitoring/sync", id), { method: "POST" }),
  resolveIncident: (id: string) =>
    request<Incident>(`/api/v1/incidents/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: "resolved" }),
    }),
  alertDeliveries: (id: string) => request<AlertDeliveryAttemptPage>(`/api/v1/alerts/events/${encodeURIComponent(id)}/deliveries`),
  alertRules: () => request<AlertRulePage>("/api/v1/alerts/rules"),
  alertEvents: (id: string | null = null, limit = 50, offset = 0) =>
    request<AlertEventPage>(scopedPath("/api/v1/alerts/events", id, {limit: String(limit), offset: String(offset)})),
  overviewMetrics: (id: string | null = null) => request<OverviewMetrics>(scopedPath("/api/v1/metrics/overview", id)),
  workflowMetrics: (id: string | null = null) => request<WorkflowHealthPage>(scopedPath("/api/v1/metrics/workflows", id)),
  n8nStatus: (id: string) => request<N8nConnectionResponse>(scopedPath("/api/v1/n8n/status", id)),
  workflows: (id: string, limit = 50, cursor?: string) =>
    request<WorkflowPage>(scopedPath("/api/v1/workflows", id, {limit: String(limit), ...(cursor ? {cursor} : {})})),
  incidents: (id: string | null = null, limit = 50, offset = 0) =>
    request<IncidentPage>(scopedPath("/api/v1/incidents", id, {limit: String(limit), offset: String(offset)})),
  incident: (id: string) => request<Incident>(`/api/v1/incidents/${id}`),
  diagnose: (id: string, force = false) =>
    request<Incident>(`/api/v1/incidents/${id}/diagnose${force ? "?force=true" : ""}`, {
      method: "POST",
    }),
};

function scopedPath(path: string, instanceId: string | null, extra?: Record<string, string>): string {
  const params = new URLSearchParams(extra);
  if (instanceId) params.set("instance_id", instanceId);
  const suffix = params.toString();
  return suffix ? `${path}?${suffix}` : path;
}

export const connectionApi = {
  list: (collection: ConnectionCollection) =>
    request<ConnectionPage>(`/api/v1/settings/${collection}`),
  save: (collection: ConnectionCollection, values: ConnectionInput, saved?: ManagedConnection) =>
    request<ManagedConnection>(
      `/api/v1/settings/${collection}${saved ? `/${encodeURIComponent(saved.id)}` : ""}`,
      {
        ...jsonBody(saved ? { ...values, expected_revision: saved.revision } : values),
        method: saved ? "PATCH" : "POST",
      },
    ),
  remove: (collection: ConnectionCollection, saved: ManagedConnection) =>
    request<{ deleted: boolean }>(`/api/v1/settings/${collection}/${encodeURIComponent(saved.id)}`, {
      method: "DELETE",
      headers: { "If-Match": `"${saved.revision}"` },
    }),
  test: (
    collection: ConnectionCollection,
    candidate: ConnectionInput,
    mode: "access" | "structured_output",
    acknowledgeUsageCost: boolean,
    saved?: ManagedConnection,
  ) => request<ConnectionTestResult>(
    `/api/v1/settings/${collection}${saved ? `/${encodeURIComponent(saved.id)}` : ""}/test`,
    {
      ...jsonBody(saved ? {
        expected_revision: saved.revision,
        changes: candidate,
        mode,
        acknowledge_usage_cost: acknowledgeUsageCost,
      } : {
        candidate,
        mode,
        acknowledge_usage_cost: acknowledgeUsageCost,
      }),
    },
  ),
};

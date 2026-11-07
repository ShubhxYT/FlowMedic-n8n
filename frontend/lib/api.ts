import type {
  ApiErrorBody,
  CurrentUser,
  AlertEventPage,
  AlertRulePage,
  HealthResponse,
  Incident,
  IncidentPage,
  N8nConnectionResponse,
  MonitoringStatus,
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
  monitoringStatus: () => request<MonitoringStatus>("/api/v1/monitoring/status"),
  monitoringSync: () => request("/api/v1/monitoring/sync", { method: "POST" }),
  alertRules: () => request<AlertRulePage>("/api/v1/alerts/rules"),
  alertEvents: (limit = 50, offset = 0) =>
    request<AlertEventPage>(`/api/v1/alerts/events?limit=${limit}&offset=${offset}`),
  overviewMetrics: () => request<OverviewMetrics>("/api/v1/metrics/overview"),
  workflowMetrics: () => request<WorkflowHealthPage>("/api/v1/metrics/workflows"),
  n8nStatus: () => request<N8nConnectionResponse>("/api/v1/n8n/status"),
  workflows: (limit = 50, cursor?: string) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (cursor) params.set("cursor", cursor);
    return request<WorkflowPage>(`/api/v1/workflows?${params}`);
  },
  incidents: (limit = 50, offset = 0) =>
    request<IncidentPage>(`/api/v1/incidents?limit=${limit}&offset=${offset}`),
  incident: (id: string) => request<Incident>(`/api/v1/incidents/${id}`),
  diagnose: (id: string) => request<Incident>(`/api/v1/incidents/${id}/diagnose`, { method: "POST" }),
};

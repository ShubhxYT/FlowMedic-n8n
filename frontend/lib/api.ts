import type {
  ApiErrorBody,
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

const apiBaseUrl = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000").replace(
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
      headers: { Accept: "application/json", ...options?.headers },
    });
  } catch {
    throw new ApiError("FlowMedic backend is unavailable. Check that the API is running.", 0);
  }

  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as ApiErrorBody;
    throw new ApiError(
      body.error?.message ?? "The request could not be completed.",
      response.status,
      body.error?.code,
    );
  }
  return (await response.json()) as T;
}

export const api = {
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

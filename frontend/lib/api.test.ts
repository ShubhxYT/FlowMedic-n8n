import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";

describe("FlowMedic API client", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("returns typed incident data", async () => {
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValue(new Response(JSON.stringify({ data: [] }), { status: 200 }));
    await expect(api.incidents()).resolves.toEqual({ data: [] });
    expect(fetchMock).toHaveBeenCalledWith("http://127.0.0.1:8000/api/v1/incidents?limit=50&offset=0", expect.objectContaining({ headers: { Accept: "application/json" } }));
  });

  it("returns the backend's safe error message", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(new Response(JSON.stringify({ error: { code: "n8n_not_configured", message: "n8n is not configured" } }), { status: 503 }));
    await expect(api.workflows()).rejects.toMatchObject({
      status: 503,
      code: "n8n_not_configured",
      message: "n8n is not configured",
    });
  });

  it("loads typed persisted monitoring metrics", async () => {
    const body = { monitored_workflow_count: 0, monitoring: { state: "disabled" } };
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValue(
      new Response(JSON.stringify(body), { status: 200 }),
    );
    await expect(api.overviewMetrics()).resolves.toEqual(body);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/api/v1/metrics/overview",
      expect.anything(),
    );
  });

  it("loads alert history through the typed dashboard client", async () => {
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ data: [] }), { status: 200 }),
    );
    await expect(api.alertEvents()).resolves.toEqual({ data: [] });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/api/v1/alerts/events?limit=50&offset=0",
      expect.anything(),
    );
  });
});

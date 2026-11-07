import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";

describe("FlowMedic API client", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("returns typed incident data", async () => {
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValue(new Response(JSON.stringify({ data: [] }), { status: 200 }));
    await expect(api.incidents()).resolves.toEqual({ data: [] });
    expect(fetchMock).toHaveBeenCalledWith("/api/backend/api/v1/incidents?limit=50&offset=0", expect.objectContaining({ headers: { Accept: "application/json" } }));
  });

  it("returns the backend's safe error message", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(new Response(JSON.stringify({ error: { code: "n8n_not_configured", message: "n8n is not configured" } }), { status: 503 }));
    await expect(api.workflows("instance-1")).rejects.toMatchObject({
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
      "/api/backend/api/v1/metrics/overview",
      expect.anything(),
    );
  });

  it("loads alert history through the typed dashboard client", async () => {
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ data: [] }), { status: 200 }),
    );
    await expect(api.alertEvents()).resolves.toEqual({ data: [] });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/backend/api/v1/alerts/events?limit=50&offset=0",
      expect.anything(),
    );
  });
});

import { connectionApi } from "@/lib/api";
import { savedConnection } from "@/tests/connections";

it("sends deletion revision and parses the JSON result", async () => {
  document.cookie = "fm_csrf=csrf-test; Path=/";
  const fetch = vi.spyOn(globalThis,"fetch").mockResolvedValue(new Response('{"deleted":true}',{status:200}));
  await expect(connectionApi.remove("n8n-instances",savedConnection)).resolves.toEqual({deleted:true});
  expect(fetch).toHaveBeenCalledWith(`/api/backend/api/v1/settings/n8n-instances/${savedConnection.id}`,expect.objectContaining({method:"DELETE",cache:"no-store",credentials:"same-origin",headers:expect.objectContaining({"If-Match":'"3"',"X-CSRF-Token":"csrf-test"})}));
});
it("places transient secrets only in JSON and preserves saved-test revision", async()=>{
  const fetch=vi.spyOn(globalThis,"fetch").mockResolvedValue(new Response('{"ok":true}',{status:200}));
  await connectionApi.test("n8n-instances",{api_key:"transient-api-test-key"},"access",false,savedConnection);
  const [url, options]=fetch.mock.calls[0];
  expect(url).not.toContain("transient-api-test-key");
  expect(JSON.parse(options!.body as string)).toEqual({expected_revision:3,changes:{api_key:"transient-api-test-key"},mode:"access",acknowledge_usage_cost:false});
  expect(localStorage.length).toBe(0);
});
it("scopes required upstream requests and optional aggregate lists",async()=>{
  const fetch=vi.spyOn(globalThis,"fetch").mockImplementation(async()=>new Response('{"data":[]}',{status:200}));
  await api.workflows("first-instance");await api.incidents("second-instance");await api.monitoringStatus(null);
  expect(fetch.mock.calls.map(([url])=>url)).toEqual(["/api/backend/api/v1/workflows?limit=50&instance_id=first-instance","/api/backend/api/v1/incidents?limit=50&offset=0&instance_id=second-instance","/api/backend/api/v1/monitoring/status"]);
});

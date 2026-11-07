import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

import { GET, POST } from "@/app/api/backend/[...path]/route";
import { api } from "@/lib/api";
import { selectedCookies } from "@/lib/auth";

const context = { params: Promise.resolve({ path: ["api", "v1", "auth", "login"] }) };

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllEnvs(); });

describe("session proxy", () => {
  it("forwards only authentication cookies and required headers and relays individual cookies", async () => {
    vi.stubEnv("FLOWMEDIC_API_KEY", "internal-token");
    const upstream = new Response('{"ok":true}', { headers: { "Content-Type": "application/json" } });
    upstream.headers.append("Set-Cookie", "fm_session=new; Path=/; HttpOnly; Secure; SameSite=Lax");
    upstream.headers.append("Set-Cookie", "fm_csrf=csrf; Path=/; Secure; SameSite=Lax");
    upstream.headers.append("Set-Cookie", "unrelated=value; Path=/");
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValue(upstream);
    const request = new NextRequest("https://flow.example/api/backend/api/v1/auth/login", {
      method: "POST", body: "{}", headers: { Cookie: "fm_session=old; fm_csrf=csrf; private=secret",
        Origin: "https://flow.example", "X-CSRF-Token": "csrf", Authorization: "Bearer attacker" },
    });
    const response = await POST(request, context);
    const options = fetchMock.mock.calls[0][1]!;
    const headers = new Headers(options.headers);
    expect(headers.get("cookie")).toBe("fm_session=old; fm_csrf=csrf");
    expect(headers.get("authorization")).toBe("Bearer internal-token");
    expect(headers.get("origin")).toBe("https://flow.example");
    expect(headers.get("x-csrf-token")).toBe("csrf");
    expect(response.headers.getSetCookie()).toHaveLength(2);
    expect(response.headers.getSetCookie()[0]).toContain("HttpOnly");
    expect(response.headers.get("cache-control")).toBe("no-store");
  });
  it("preserves separate logout expiry cookies", async () => {
    const upstream = new Response('{"ok":true}');
    upstream.headers.append("Set-Cookie", "fm_session=; Path=/; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT; HttpOnly");
    upstream.headers.append("Set-Cookie", "fm_csrf=; Path=/; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT");
    vi.spyOn(global, "fetch").mockResolvedValue(upstream);
    const response = await POST(new NextRequest("https://flow.example/api/backend/api/v1/auth/logout", {
      method: "POST", body: "{}",
    }), { params: Promise.resolve({ path: ["api", "v1", "auth", "logout"] }) });
    expect(response.headers.getSetCookie()).toHaveLength(2);
    expect(response.headers.getSetCookie().every((value) => value.includes("Max-Age=0"))).toBe(true);
  });
  it("does not share responses across users", async () => {
    vi.spyOn(global, "fetch").mockImplementation(async (_url, options) =>
      new Response(JSON.stringify({ owner: new Headers(options?.headers).get("cookie") })));
    for (const user of ["alice", "bob"]) {
      const response = await GET(new NextRequest("https://flow.example/api/backend/api/v1/incidents", {
        headers: { cookie: `fm_session=${user}` },
      }), { params: Promise.resolve({ path: ["api", "v1", "incidents"] }) });
      expect(await response.json()).toEqual({ owner: `fm_session=${user}` });
    }
  });
  it("rejects path traversal and filters other cookies", async () => {
    expect(selectedCookies("other=a; fm_csrf=b")).toBe("fm_csrf=b");
    const response = await GET(new NextRequest("https://flow.example/api/backend/x"), {
      params: Promise.resolve({ path: ["..", "docs"] }),
    });
    expect(response.status).toBe(404);
  });
  it("forwards logout expiry cookies and includes CSRF on mutations", async () => {
    document.cookie = "fm_csrf=csrf-value; Path=/";
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValue(new Response('{"ok":true}'));
    await api.logout();
    expect(new Headers(fetchMock.mock.calls[0][1]?.headers).get("x-csrf-token")).toBe("csrf-value");
  });
});

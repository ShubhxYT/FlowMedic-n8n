import { beforeEach, expect, it, vi } from "vitest";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

vi.mock("next/headers", () => ({ cookies: vi.fn().mockResolvedValue({
  getAll: () => [{ name: "fm_session", value: "user" }, { name: "private", value: "secret" }],
}) }));
vi.mock("next/navigation", () => ({ redirect: vi.fn(() => { throw new Error("redirect:/login"); }) }));

import { requireCurrentUser } from "@/lib/auth-server";

beforeEach(() => {
  vi.restoreAllMocks();
  vi.mocked(cookies).mockResolvedValue({ getAll: () => [{ name: "fm_session", value: "user" }] } as Awaited<ReturnType<typeof cookies>>);
  vi.mocked(redirect).mockImplementation(() => { throw new Error("redirect:/login"); });
});

it("redirects anonymous or expired sessions", async () => {
  vi.spyOn(global, "fetch").mockResolvedValue(new Response("{}", { status: 401 }));
  await expect(requireCurrentUser()).rejects.toThrow("redirect:/login");
});

it("keeps backend outages distinct from anonymous access", async () => {
  vi.spyOn(global, "fetch").mockResolvedValue(new Response("{}", { status: 503 }));
  await expect(requireCurrentUser()).rejects.toThrow("account service is unavailable");
});

it("forwards session cookies using no-store", async () => {
  const fetchMock = vi.spyOn(global, "fetch").mockResolvedValue(new Response('{"demo":false}'));
  expect(await requireCurrentUser()).toEqual({ demo: false });
  const options = fetchMock.mock.calls[0][1]!;
  expect(new Headers(options.headers).get("cookie")).toBe("fm_session=user");
  expect(options.cache).toBe("no-store");
});

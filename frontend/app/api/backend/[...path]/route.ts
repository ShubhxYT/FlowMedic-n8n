import { NextRequest, NextResponse } from "next/server";

import { cookieNames, selectedCookies } from "@/lib/auth";

export const dynamic = "force-dynamic";
type Context = { params: Promise<{ path: string[] }> };

async function forward(request: NextRequest, context: Context): Promise<NextResponse> {
  const { path } = await context.params;
  if (path.some((part) => !part || part === "." || part === ".." || part.includes("/")) ||
      !(path.length === 1 && path[0] === "health" || path[0] === "api" && path[1] === "v1")) {
    return NextResponse.json({ error: { code: "not_found", message: "Route was not found" } },
      { status: 404, headers: { "Cache-Control": "no-store" } });
  }
  const base = (process.env.BACKEND_INTERNAL_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
  const target = new URL(`${base}/${path.map(encodeURIComponent).join("/")}`);
  target.search = request.nextUrl.search;
  const headers = new Headers({ Accept: "application/json" });
  for (const name of ["content-type", "origin", "x-csrf-token"]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const cookies = selectedCookies(request.headers.get("cookie") ?? "");
  if (cookies) headers.set("cookie", cookies);
  const key = process.env.FLOWMEDIC_API_KEY;
  if (key) headers.set("authorization", `Bearer ${key}`);
  // Enable only behind an ingress that overwrites X-Forwarded-For and blocks direct access.
  if (key && process.env.TRUST_PROXY_IP === "true") {
    const address = request.headers.get("x-forwarded-for")?.split(",")[0]?.trim();
    if (address && /^[0-9a-fA-F:.]{3,64}$/.test(address)) headers.set("x-flowmedic-client-ip", address);
  }
  try {
    const upstream = await fetch(target, {
      method: request.method, headers,
      body: ["GET", "HEAD"].includes(request.method) ? undefined : await request.text(),
      cache: "no-store", redirect: "manual", signal: AbortSignal.timeout(120000),
    });
    const response = new NextResponse(
      request.method === "HEAD" || upstream.status === 204 ? null : await upstream.text(), {
        status: upstream.status,
        headers: { "Content-Type": upstream.headers.get("content-type") ?? "application/json",
          "Cache-Control": "no-store", "Vary": "Cookie", "Pragma": "no-cache" },
      },
    );
    for (const cookie of upstream.headers.getSetCookie()) {
      if (cookieNames.has(cookie.slice(0, cookie.indexOf("=")))) {
        response.headers.append("Set-Cookie", cookie);
      }
    }
    return response;
  } catch {
    return NextResponse.json(
      { error: { code: "backend_unavailable", message: "The FlowMedic API is unavailable." } },
      { status: 502, headers: { "Cache-Control": "no-store" } },
    );
  }
}

export { forward as GET, forward as HEAD, forward as PATCH, forward as POST, forward as DELETE };

import { NextRequest, NextResponse } from "next/server";

// Same-origin proxy for the FastAPI service.
//
// The browser never talks to the API host directly: it calls `/api/backend/*`
// on the dashboard origin, and this handler forwards the request server-side
// with the bearer token attached. That keeps `FLOWMEDIC_API_KEY` out of the
// browser bundle and removes the per-customer CORS configuration from the
// deployment.
export const dynamic = "force-dynamic";

const backendBase = (process.env.BACKEND_INTERNAL_URL ?? "http://127.0.0.1:8000").replace(
  /\/$/,
  "",
);
const apiKey = process.env.FLOWMEDIC_API_KEY ?? "";

type Context = { params: Promise<{ path: string[] }> };

async function forward(request: NextRequest, context: Context): Promise<NextResponse> {
  const { path } = await context.params;
  const target = new URL(`${backendBase}/${path.map(encodeURIComponent).join("/")}`);
  target.search = request.nextUrl.search;

  const headers = new Headers({ Accept: "application/json" });
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("Content-Type", contentType);
  if (apiKey) headers.set("Authorization", `Bearer ${apiKey}`);

  const hasBody = request.method !== "GET" && request.method !== "HEAD";

  try {
    const response = await fetch(target, {
      method: request.method,
      headers,
      body: hasBody ? await request.text() : undefined,
      cache: "no-store",
      redirect: "manual",
    });

    return new NextResponse(await response.text(), {
      status: response.status,
      headers: {
        "Content-Type": response.headers.get("content-type") ?? "application/json",
        "Cache-Control": "no-store",
      },
    });
  } catch {
    return NextResponse.json(
      { error: { code: "backend_unavailable", message: "The FlowMedic API is unavailable." } },
      { status: 502 },
    );
  }
}

export { forward as GET, forward as PATCH, forward as POST };

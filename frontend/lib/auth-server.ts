import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { cookieNames } from "@/lib/auth";
import type { CurrentUser } from "@/lib/types";

export async function requireCurrentUser(): Promise<CurrentUser> {
  const jar = await cookies();
  const headers = new Headers({ Accept: "application/json" });
  headers.set("cookie", jar.getAll().filter((cookie) => cookieNames.has(cookie.name))
    .map((cookie) => `${cookie.name}=${cookie.value}`).join("; "));
  if (process.env.FLOWMEDIC_API_KEY) {
    headers.set("authorization", `Bearer ${process.env.FLOWMEDIC_API_KEY}`);
  }
  const base = (process.env.BACKEND_INTERNAL_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
  const response = await fetch(`${base}/api/v1/auth/me`, {
    headers, cache: "no-store", redirect: "manual", signal: AbortSignal.timeout(15000),
  });
  if (response.status === 401) redirect("/login");
  if (!response.ok) throw new Error("FlowMedic account service is unavailable");
  return await response.json() as CurrentUser;
}

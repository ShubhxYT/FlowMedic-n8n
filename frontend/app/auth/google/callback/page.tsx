"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";

export default function GoogleCallbackPage() {
  const [error, setError] = useState("");
  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const params = new URLSearchParams(window.location.search);
    const code = params.get("code");
    const state = params.get("state");
    const failed = Boolean(params.get("error")) || !code || !state;
    // Drop the one-time code from the address bar and history.
    if (!failed) window.history.replaceState(null, "", "/auth/google/callback");
    void Promise.resolve()
      .then(() => {
        if (failed) throw new Error("Google sign-in was cancelled or failed.");
        return api.csrf();
      })
      .then(() => api.googleCallback(code!, state!))
      .then(() => window.location.replace("/"))
      .catch((reason) => setError(reason instanceof Error ? reason.message : "Google sign-in failed"));
  }, []);
  return <main className="mx-auto max-w-md p-8">
    <h1 className="text-2xl font-bold">Continue with Google</h1>
    {error
      ? <><p role="alert" className="my-4">{error}</p><p><Link href="/login">Back to log in</Link></p></>
      : <p role="status" className="my-4">Signing you in…</p>}
  </main>;
}

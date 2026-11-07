"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { api } from "@/lib/api";

export function AuthForm({ mode, onSuccess }: {
  mode: "login" | "signup"; onSuccess?: () => Promise<void>;
}) {
  const [ready, setReady] = useState(false);
  const [registration, setRegistration] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    void api.csrf().then((value) => {
      if (active) { setRegistration(value.registration_enabled); setReady(true); }
    }).catch(() => { if (active) setError("Account service is unavailable. Reload to try again."); });
    return () => { active = false; };
  }, []);
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true); setError("");
    const form = event.currentTarget;
    const values = new FormData(form);
    try {
      await api[mode](String(values.get("email")), String(values.get("password")));
      form.reset();
      if (onSuccess) await onSuccess();
      else window.location.assign(new URL("/", window.location.origin).href);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Sign in failed");
    } finally { setBusy(false); }
  }
  return <main className="mx-auto max-w-md p-8">
    <h1 className="text-2xl font-bold">{mode === "login" ? "Log in to FlowMedic" : "Create an account"}</h1>
    <p className="my-4">{mode === "signup" ? "Your account starts with a private workspace." : "Use your email and password to continue."}</p>
    {error && <p role="alert" className="my-4">{error}</p>}
    {mode === "signup" && ready && !registration && <p role="status">Registration is closed. Contact the operator.</p>}
    <form onSubmit={(event) => void submit(event)} className="grid gap-4">
      <label>Email<input className="block w-full rounded border p-2" name="email" type="email" required maxLength={254} autoComplete="username" /></label>
      <label>Password<input className="block w-full rounded border p-2" name="password" type="password" required minLength={12} maxLength={128} autoComplete={mode === "login" ? "current-password" : "new-password"} /></label>
      <button className="button" disabled={!ready || busy || mode === "signup" && !registration}>
        {busy ? "Please wait…" : mode === "login" ? "Log in" : "Create account"}
      </button>
    </form>
    <p className="mt-4"><Link href={mode === "login" ? "/signup" : "/login"}>
      {mode === "login" ? "Create an account" : "Already have an account? Log in"}
    </Link></p>
  </main>;
}

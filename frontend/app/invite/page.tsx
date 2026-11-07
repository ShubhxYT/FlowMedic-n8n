"use client";

import { useEffect, useState } from "react";

import { AuthForm } from "@/components/auth-form";
import { api } from "@/lib/api";

export default function InvitePage() {
  const [state, setState] = useState<"loading" | "login" | "signup" | "ready">("loading");
  const [error, setError] = useState("");
  useEffect(() => { void api.me().then(() => setState("ready")).catch(() => setState("login")); }, []);
  async function accept() {
    const token = new URLSearchParams(window.location.hash.slice(1)).get("token");
    if (!token) { setError("Invitation token is missing"); return; }
    try { await api.acceptInvite(token); window.location.replace("/"); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Invitation failed"); }
  }
  return <>
    {error && <p role="alert">{error}</p>}
    {state === "loading" && <p role="status">Checking account…</p>}
    {(state === "login" || state === "signup") && <>
      <AuthForm mode={state} onSuccess={accept} />
      <button className="button" onClick={() => setState(state === "login" ? "signup" : "login")}>
        {state === "login" ? "Sign up here to keep your invitation" : "Log in here to keep your invitation"}
      </button>
    </>}
    {state === "ready" && <main className="p-8"><h1>Join workspace</h1>
      <button className="button" onClick={() => void accept()}>Accept invitation</button></main>}
  </>;
}

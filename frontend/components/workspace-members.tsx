"use client";

import { useState } from "react";

import { useAccount } from "@/components/account-provider";
import { api } from "@/lib/api";
import { useApi } from "@/lib/use-api";

export function WorkspaceMembers() {
  const account = useAccount();
  if (!account || account.demo || account.workspace.role !== "admin") return null;
  return <MemberControls />;
}

function MemberControls() {
  const members = useApi(api.members);
  const [url, setUrl] = useState("");
  const [role, setRole] = useState<"admin" | "viewer">("viewer");
  const [error, setError] = useState("");
  async function act(action: () => Promise<unknown>) {
    setError("");
    try { await action(); await members.refresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Member update failed"); }
  }
  return <section className="panel p-6 my-6"><h2>Workspace members</h2>
    <label>Invitation role <select value={role} onChange={(event) => setRole(event.target.value as "admin" | "viewer")}>
      <option value="viewer">Viewer</option><option value="admin">Admin</option>
    </select></label>
    <button className="button" onClick={() => void act(async () => setUrl((await api.invite(role)).url))}>Create invitation link</button>
    {url && <label className="block">Share this expiring link<input readOnly value={url} className="block w-full" onFocus={(event) => event.target.select()} /></label>}
    {(error || members.error) && <p role="alert">{error || members.error?.message}</p>}
    <ul>{members.data?.data.map((member) => <li key={member.id} className="my-4 flex gap-3">
      <span>{member.email}</span>
      <label>Role for {member.email}<select value={member.role} onChange={(event) => void act(() => api.updateMember(member.id, event.target.value as "admin" | "viewer"))}>
        <option value="viewer">Viewer</option><option value="admin">Admin</option>
      </select></label>
      <button className="button button-secondary" onClick={() => void act(() => api.removeMember(member.id))}>Remove {member.email}</button>
    </li>)}</ul>
  </section>;
}

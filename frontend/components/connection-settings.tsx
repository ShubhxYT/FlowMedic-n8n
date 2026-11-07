"use client";

import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

import { ConnectionForm } from "@/components/connection-form";
import { useConnectionScope } from "@/components/connection-scope";
import { ApiError, connectionApi } from "@/lib/api";
import type { ConnectionCollection, ManagedConnection } from "@/lib/types";
import { useApi } from "@/lib/use-api";

function Dialog({
  title, pending, onClose, children,
}: { title: string; pending: boolean; onClose: () => void; children: ReactNode }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => { element?.close(); };
  }, []);
  return <dialog className="connection-dialog" ref={dialog} aria-label={title} onCancel={(event) => {
    event.preventDefault();
    if (!pending) onClose();
  }}>
    <h2>{title}</h2>
    {children}
  </dialog>;
}

function ConnectionSection({ collection }: { collection: ConnectionCollection }) {
  const scope = useConnectionScope();
  const query = useApi(() => connectionApi.list(collection), `${scope.workspaceId}:${collection}`);
  const [editor, setEditor] = useState<{ mode: "create"; saved?: undefined } | { mode: "edit"; saved: ManagedConnection } | null>(null);
  const [deleting, setDeleting] = useState<ManagedConnection | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const busy = useRef(false);
  const mounted = useRef(false);
  const family = collection === "n8n-instances" ? "n8n" : "ai";
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  async function run(work: () => Promise<void>): Promise<boolean> {
    if (busy.current) return false;
    busy.current = true;
    setPending(true);
    setError(null);
    setConflict(false);
    setFeedback(null);
    try {
      await work();
      return mounted.current;
    } catch (reason) {
      if (mounted.current) {
        const changed = reason instanceof ApiError && reason.status === 409;
        setConflict(changed);
        setError(changed ? "This connection changed. Reload connections before trying again." : reason instanceof ApiError ? reason.message : "Connection action failed.");
      }
      return false;
    } finally {
      busy.current = false;
      if (mounted.current) setPending(false);
    }
  }

  async function refresh() {
    await Promise.all([query.refresh(), scope.refresh()]);
  }

  function close() {
    if (!busy.current) {
      setEditor(null);
      setDeleting(null);
      setError(null);
      setFeedback(null);
    }
  }

  function toggle(saved: ManagedConnection) {
    void run(async () => {
      await connectionApi.save(collection, { enabled: !saved.enabled }, saved);
      await refresh();
    });
  }

  return <section className="panel setting-card connection-section" aria-label={family === "n8n" ? "n8n instances" : "AI providers"}>
    <h2>{family === "n8n" ? "n8n instances" : "AI providers"}</h2>
    <p>{family === "n8n" ? "Connect a server to monitor its workflows and failed executions." : "Choose a workspace default for advisory incident diagnosis."}</p>
    {(error || query.error) && <p role="alert">{error ?? query.error?.message}</p>}
    {feedback && <p role="status">{feedback}</p>}
    {conflict && !editor && <p>Reload connections before another action.</p>}
    {query.loading && <p>Loading connections…</p>}
    <button className="button button-secondary" type="button" disabled={pending} onClick={() => {
      setEditor(null);
      setDeleting(null);
      setError(null);
      setConflict(false);
      void refresh();
    }}>Reload connections</button>
    {scope.role === "admin" && <button className="button button-secondary" type="button" disabled={pending} onClick={() => {
      setError(null);
      setFeedback(null);
      setEditor({ mode: "create" });
    }}>Add {family === "n8n" ? "instance" : "provider"}</button>}
    {query.data?.items.length === 0 && <p>No connections configured.</p>}
    <ul className="connection-list">
      {query.data?.items.map((saved) => <li key={saved.id}>
        <strong>{saved.name}</strong> ({saved.id.slice(0, 8)})
        <p>{saved.base_url ?? "Local deterministic mock"}</p>
        <p>{saved.model ?? saved.kind} · {saved.enabled ? "Enabled" : "Disabled"}
          {saved.is_default ? " · Workspace default" : ""}
          {saved.has_api_key ? " · Key stored" : " · No key"} · Revision {saved.revision}</p>
        {scope.role === "admin" && <>
          <button className="button button-secondary" type="button" disabled={pending} onClick={() => {
            setError(null);
            setFeedback(null);
            setEditor({ mode: "edit", saved });
          }}>Edit / test</button>
          <button className="button button-secondary" type="button" disabled={pending || conflict || (!saved.enabled && saved.kind !== "mock" && !saved.has_api_key)} onClick={() => toggle(saved)}>
            {saved.enabled ? "Disable" : "Enable"}
          </button>
          {family === "ai" && saved.enabled && !saved.is_default && <button
            className="button button-secondary" type="button" disabled={pending || conflict} onClick={() => {
              void run(async () => {
                await connectionApi.save(collection, {
                  make_default: true, expected_state_revision: query.data!.state_revision,
                }, saved);
                await refresh();
              });
            }}>Make default</button>}
          <button className="button button-secondary" type="button" disabled={pending} onClick={() => setDeleting(saved)}>Delete</button>
        </>}
      </li>)}
    </ul>
    {editor && scope.role === "admin" && <Dialog
      title={editor.saved ? "Edit connection" : "Add connection"} pending={pending} onClose={close}
    >
      {error && <p role="alert">{error}</p>}
      {conflict && <button className="button button-secondary" type="button" disabled={pending} onClick={() => {close(); setConflict(false); void refresh();}}>Reload connections</button>}
      {feedback && <p role="status">{feedback}</p>}
      <ConnectionForm key={`${scope.workspaceId}:${editor.saved ? `${editor.saved.id}:${editor.saved.revision}` : "new"}`}
        family={family} saved={editor.saved} stateRevision={query.data?.state_revision ?? 1}
        pending={pending} onClose={close}
        onSave={async (candidate) => {
          const success = await run(async () => {
            await connectionApi.save(collection, candidate, editor.saved);
            await refresh();
          });
          if (success) setEditor(null);
          return success;
        }}
        onTest={async (candidate, mode, acknowledged) => {
          await run(async () => {
            const result = await connectionApi.test(
              collection, candidate, mode, acknowledged, editor.saved,
            );
            if (mounted.current) setFeedback(`${result.capability}: ${result.message}`);
          });
        }} />
    </Dialog>}
    {deleting && <Dialog title="Delete connection" pending={pending} onClose={close}>
      <p>Delete {deleting.name} ({deleting.id.slice(0, 8)})? History will be retained.</p>
      {error && <p role="alert">{error}</p>}
      <button className="button button-secondary" type="button" disabled={pending} onClick={() => {
        void run(async () => {
          await connectionApi.remove(collection, deleting);
          await refresh();
          if (mounted.current) {
            setDeleting(null);
            if (editor?.saved?.id === deleting.id) setEditor(null);
          }
        });
      }}>Delete connection</button>
      <button className="button button-secondary" type="button" disabled={pending} onClick={close}>Cancel</button>
    </Dialog>}
  </section>;
}

export function ConnectionSettings() {
  return <>
    <ConnectionSection collection="n8n-instances" />
    <ConnectionSection collection="ai-providers" />
  </>;
}

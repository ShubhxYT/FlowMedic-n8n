"use client";

import { useId, useRef, useState } from "react";
import type { FormEvent } from "react";

import type { ConnectionInput, ManagedConnection } from "@/lib/types";

interface Props {
  family: "n8n" | "ai";
  saved?: ManagedConnection;
  stateRevision: number;
  pending: boolean;
  onSave: (value: ConnectionInput) => Promise<boolean>;
  onTest: (
    value: ConnectionInput, mode: "access" | "structured_output", acknowledged: boolean,
  ) => Promise<void>;
  onClose: () => void;
}

export function ConnectionForm({
  family, saved, stateRevision, pending, onSave, onTest, onClose,
}: Props) {
  const id = useId();
  const form = useRef<HTMLFormElement>(null);
  const [name, setName] = useState(saved?.name ?? "");
  const [url, setUrl] = useState(saved?.base_url ?? "");
  const [kind, setKind] = useState<"openai-compatible" | "mock">(
    saved?.kind === "mock" ? "mock" : "openai-compatible",
  );
  const [model, setModel] = useState(saved?.model ?? "");
  const [secret, setSecret] = useState("");
  const [keyAction, setKeyAction] = useState<"keep" | "replace" | "remove">(
    saved ? "keep" : "replace",
  );
  const [enabled, setEnabled] = useState(saved?.enabled ?? false);
  const [makeDefault, setMakeDefault] = useState(false);
  const [sameServer, setSameServer] = useState(false);
  const [structured, setStructured] = useState(false);
  const live = family === "n8n" || kind !== "mock";
  const urlChanged = Boolean(saved && url !== saved.base_url);

  function candidate(): ConnectionInput {
    const value: ConnectionInput = { name, enabled };
    if (live) value.base_url = url;
    if (live && keyAction === "replace") value.api_key = secret;
    if (live && keyAction === "remove") value.remove_api_key = true;
    if (family === "ai") {
      value.kind = kind;
      if (live) value.model = model;

      if (makeDefault) {
        value.make_default = true;
        value.expected_state_revision = stateRevision;
      }
    }
    if (live && urlChanged) value.confirm_same_server = sameServer;
    return value;
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (await onSave(candidate())) setSecret("");
  }

  return (
    <form className="connection-form" ref={form} onSubmit={(event) => { void save(event); }} aria-label="Connection settings">
      <fieldset disabled={pending}>
        <legend>{saved ? "Edit connection" : "Add connection"}</legend>
        <label htmlFor={`${id}-name`}>Display name</label>
        <input id={`${id}-name`} value={name} maxLength={120} required
          onChange={(event) => setName(event.target.value)} />
        {family === "ai" && <>
          <label htmlFor={`${id}-kind`}>Provider type</label>
          <select id={`${id}-kind`} value={kind} onChange={(event) => {
            setKind(event.target.value as "openai-compatible" | "mock");
            setSecret("");
            setStructured(false);
            setSameServer(false);
          }}>
            <option value="openai-compatible">OpenAI compatible</option>
            <option value="mock">Deterministic mock</option>
          </select>
        </>}
        {live && <>
          <label htmlFor={`${id}-url`}>Base URL</label>
          <input id={`${id}-url`} type="url" value={url} maxLength={2048} required
            onChange={(event) => setUrl(event.target.value)} />
          <p>{saved?.has_api_key ? "A key is stored. Its value is never shown." : "No key stored."}</p>
          <label htmlFor={`${id}-action`}>API key action</label>
          <select id={`${id}-action`} value={keyAction} onChange={(event) => {
            setKeyAction(event.target.value as "keep" | "replace" | "remove");
            setSecret("");
            if (event.target.value === "remove") {
              setEnabled(false);
              setMakeDefault(false);
            }
          }}>
            {saved && <option value="keep">Keep stored key</option>}
            <option value="replace">{saved ? "Replace key" : "Set key"}</option>
            {saved && <option value="remove">Remove key and disable</option>}
          </select>
          {keyAction === "replace" && <>
            <label htmlFor={`${id}-key`}>New API key</label>
            <input id={`${id}-key`} type="password" autoComplete="new-password"
              value={secret} maxLength={8192} required
              onChange={(event) => setSecret(event.target.value)} />
          </>}
          {family === "ai" && <>
            <label htmlFor={`${id}-model`}>Model</label>
            <input id={`${id}-model`} value={model} maxLength={200} required
              onChange={(event) => setModel(event.target.value)} />
          </>}
        </>}
        {urlChanged && <label>
          <input type="checkbox" checked={sameServer}
            onChange={(event) => setSameServer(event.target.checked)} />
          This URL still identifies the same server. For another server, add a new connection.
        </label>}
        <label>
          <input type="checkbox" checked={enabled}
            disabled={!saved || (live && keyAction === "remove")}
            onChange={(event) => {
              setEnabled(event.target.checked);
              if (!event.target.checked) setMakeDefault(false);
            }} />
          Enabled{!saved ? " (save disabled first, then explicitly enable)" : ""}
        </label>
        {family === "ai" && <label>
          <input type="checkbox" checked={makeDefault} disabled={!enabled}
            onChange={(event) => setMakeDefault(event.target.checked)} />
          Use as workspace default
        </label>}
        {family === "ai" && live && <label>
          <input type="checkbox" checked={structured}
            onChange={(event) => setStructured(event.target.checked)} />
          Test structured output with a small request that may incur usage cost
        </label>}
        <button type="button" className="button button-secondary" onClick={() => {
          if (form.current?.reportValidity()) {
            void onTest(candidate(), structured ? "structured_output" : "access", structured);
          }
        }}>Test connection</button>
        <button type="submit" className="button button-primary">Save connection</button>
        <button type="button" className="button button-secondary" onClick={() => {
          setSecret("");
          onClose();
        }}>Cancel</button>
      </fieldset>
    </form>
  );
}

"use client";

import {
  createContext, useCallback, useContext, useEffect, useMemo, useSyncExternalStore,
} from "react";
import type { ReactNode } from "react";

import { connectionApi } from "@/lib/api";
import type { ManagedConnection } from "@/lib/types";
import { useApi } from "@/lib/use-api";

interface Scope {
  workspaceId: string;
  role: "admin" | "viewer";
  instanceId: string | null;
  instances: ManagedConnection[];
  loading: boolean;
  setInstance: (id: string | null) => void;
  refresh: () => Promise<void>;
}

const Context = createContext<Scope | null>(null);
const changeEvent = "flowmedic-instance-selection";
const emptySnapshot = () => "";

function subscribe(listener: () => void) {
  window.addEventListener("storage", listener);
  window.addEventListener(changeEvent, listener);
  return () => {
    window.removeEventListener("storage", listener);
    window.removeEventListener(changeEvent, listener);
  };
}

export function ConnectionScopeProvider({
  workspaceId, role, children,
}: {
  workspaceId: string;
  role: "admin" | "viewer";
  children: ReactNode;
}) {
  const storageKey = `flowmedic:instance:${workspaceId}`;
  const query = useApi(() => connectionApi.list("n8n-instances"), `${workspaceId}:instances`);
  const read = useCallback(() => {
    try { return localStorage.getItem(storageKey) ?? ""; } catch { return ""; }
  }, [storageKey]);
  const savedId = useSyncExternalStore(subscribe, read, emptySnapshot);
  const setInstance = useCallback((id: string | null) => {
    try {
      if (id) localStorage.setItem(storageKey, id);
      else localStorage.removeItem(storageKey);
    } catch {
      // Storage can be unavailable; aggregate views remain usable.
    }
    window.dispatchEvent(new Event(changeEvent));
  }, [storageKey]);
  const instances = query.data?.items;
  const instanceId = instances?.some((item) => item.id === savedId) ? savedId : null;
  useEffect(() => {
    if (savedId && instances && !instances.some((item) => item.id === savedId)) {
      setInstance(null);
    }
  }, [instances, savedId, setInstance]);
  const value = useMemo<Scope>(() => ({
    workspaceId, role, instanceId, instances: instances ?? [],
    loading: query.loading, setInstance, refresh: query.refresh,
  }), [workspaceId, role, instanceId, instances, query.loading, query.refresh, setInstance]);
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useConnectionScope(): Scope {
  const scope = useContext(Context);
  if (!scope) throw new Error("Connection scope requires an authenticated workspace");
  return scope;
}

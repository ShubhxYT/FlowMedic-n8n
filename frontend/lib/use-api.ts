"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "@/lib/api";

export interface QueryState<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  lastUpdated: Date | null;
  refresh: () => Promise<void>;
}

interface UseApiOptions {
  pollIntervalMs?: number;
}

export function useApi<T>(
  load: () => Promise<T>,
  key = "initial",
  options: UseApiOptions = {},
): QueryState<T> {
  const [state, setState] = useState<{
    key: string;
    data: T | null;
    error: ApiError | null;
    loading: boolean;
    lastUpdated: Date | null;
  }>({
    key,
    data: null,
    error: null,
    loading: true,
    lastUpdated: null,
  });

  const sequence = useRef(0);
  const mounted = useRef(false);
  const loadRef = useRef(load);

  useEffect(() => {
    loadRef.current = load;
  }, [load]);

  const refresh = useCallback(async () => {
    if (!mounted.current) return;

    const requestId = ++sequence.current;

    setState((previous) => ({
      key,
      data: previous.key === key ? previous.data : null,
      error: null,
      loading: true,
      lastUpdated: previous.key === key ? previous.lastUpdated : null,
    }));

    try {
      const data = await loadRef.current();

      if (mounted.current && requestId === sequence.current) {
        setState({
          key,
          data,
          error: null,
          loading: false,
          lastUpdated: new Date(),
        });
      }
    } catch (reason) {
      if (mounted.current && requestId === sequence.current) {
        setState((previous) => ({
          key,
          data: previous.key === key ? previous.data : null,
          loading: false,
          lastUpdated: previous.key === key ? previous.lastUpdated : null,
          error: reason instanceof ApiError
            ? reason
            : new ApiError("The dashboard could not load this data.", 0),
        }));
      }
    }
  }, [key]);

  useEffect(() => {
    mounted.current = true;

    let active = true;

    void Promise.resolve().then(() => {
      if (active) return refresh();
    });

    return () => {
      active = false;
      mounted.current = false;
      sequence.current += 1;
    };
  }, [refresh]);

  useEffect(() => {
    const interval = options.pollIntervalMs;

    if (!interval || interval <= 0) return;

    const poll = () => {
      if (document.visibilityState === "visible") {
        void refresh();
      }
    };

    const timer = window.setInterval(poll, interval);

    document.addEventListener("visibilitychange", poll);

    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", poll);
    };
  }, [options.pollIntervalMs, refresh]);

  if (state.key !== key) {
    return {
      data: null,
      error: null,
      loading: true,
      lastUpdated: null,
      refresh,
    };
  }

  return {
    data: state.data,
    error: state.error,
    loading: state.loading,
    lastUpdated: state.lastUpdated,
    refresh,
  };
}
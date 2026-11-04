"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "@/lib/api";

export interface QueryState<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  refresh: () => Promise<void>;
}

export function useApi<T>(load: () => Promise<T>, key = "initial"): QueryState<T> {
  const [state, setState] = useState<{
    key: string;
    data: T | null;
    error: ApiError | null;
    loading: boolean;
  }>({ key, data: null, error: null, loading: true });
  const sequence = useRef(0);
  const mounted = useRef(false);

  const loadRef = useRef(load);
  useEffect(() => {
    loadRef.current = load;
  }, [load]);

  const refresh = useCallback(async () => {
    if (!mounted.current) return;
    const requestId = ++sequence.current;
    setState({ key, data: null, error: null, loading: true });
    try {
      const data = await loadRef.current();
      if (mounted.current && requestId === sequence.current) {
        setState({ key, data, error: null, loading: false });
      }
    } catch (reason) {
      if (mounted.current && requestId === sequence.current) {
        setState({
          key,
          data: null,
          loading: false,
          error: reason instanceof ApiError
            ? reason
            : new ApiError("The dashboard could not load this data.", 0),
        });
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

  if (state.key !== key) return { data: null, error: null, loading: true, refresh };
  return { data: state.data, error: state.error, loading: state.loading, refresh };
}

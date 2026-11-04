import { useCallback, useEffect, useRef, useState } from "react";
import { describeError } from "../api/client";

export interface AsyncState<T> {
  data: T | undefined;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

interface Settled<T> {
  key: string;
  data: T | undefined;
  error: string | null;
}

/**
 * Run an async loader whenever the JSON value of `deps` changes, optionally re-running
 * every `pollMs`. Keeps the last good data while a refresh is in flight so lists do not flicker.
 */
export function useAsync<T>(loader: () => Promise<T>, deps: unknown[], pollMs?: number): AsyncState<T> {
  const [tick, setTick] = useState(0);
  const [settled, setSettled] = useState<Settled<T>>({ key: "", data: undefined, error: null });
  const loaderRef = useRef(loader);
  useEffect(() => {
    loaderRef.current = loader;
  });

  const key = JSON.stringify([deps, tick]);
  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    let cancelled = false;
    loaderRef.current().then(
      (data) => !cancelled && setSettled({ key, data, error: null }),
      (e: unknown) => !cancelled && setSettled((s) => ({ key, data: s.data, error: describeError(e) })),
    );
    return () => {
      cancelled = true;
    };
  }, [key]);

  useEffect(() => {
    if (!pollMs) return;
    const id = setInterval(reload, pollMs);
    return () => clearInterval(id);
  }, [pollMs, reload]);

  return { data: settled.data, error: settled.error, loading: settled.key !== key, reload };
}

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
 * Run an async loader whenever the JSON value of `deps` changes. With `pollMs`, the next
 * load is scheduled `pollMs` after the previous one settles, so a slow request is never
 * cancelled by the poll timer. Keeps the last good data while a refresh is in flight.
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
    let timer: ReturnType<typeof setTimeout> | undefined;
    const scheduleNext = () => {
      if (!cancelled && pollMs) timer = setTimeout(reload, pollMs);
    };
    loaderRef
      .current()
      .then(
        (data) => !cancelled && setSettled({ key, data, error: null }),
        (e: unknown) => !cancelled && setSettled((s) => ({ key, data: s.data, error: describeError(e) })),
      )
      .finally(scheduleNext);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [key, pollMs, reload]);

  return { data: settled.data, error: settled.error, loading: settled.key !== key, reload };
}

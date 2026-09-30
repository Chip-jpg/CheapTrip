/** Load data for a screen; reload() fetches again (e.g. on an engine event). */
import { useCallback, useEffect, useRef, useState } from "react";

export interface Loaded<T> {
  data: T | undefined;
  error: string | undefined;
  loading: boolean;
  reload: () => void;
}

export function useLoad<T>(load: () => Promise<T>, deps: unknown[]): Loaded<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<string>();
  const [loading, setLoading] = useState(true);
  const latest = useRef(0);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const run = useCallback(load, deps);

  const reload = useCallback(() => {
    const call = ++latest.current;
    setLoading(true);
    run()
      .then((value) => call === latest.current && (setData(value), setError(undefined)))
      .catch((err: Error) => call === latest.current && setError(err.message))
      .finally(() => call === latest.current && setLoading(false));
  }, [run]);

  useEffect(() => { reload(); }, [reload]);
  return { data, error, loading, reload };
}

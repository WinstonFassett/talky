// Claude Code / Hermes tabs of "In tools": that tool's own saved sessions, loaded when the tab is shown.
import { useCallback, useEffect, useState } from 'react';

import { nativeApi, type NativeTool, type T3Thread } from './api';

export function useNativeSessions(tool: NativeTool | null) {
  const [sessions, setSessions] = useState<T3Thread[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    if (!tool) return;
    setLoading(true);
    try {
      setSessions((await nativeApi.list(tool)).sessions);
      setError(null);
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setLoading(false);
    }
  }, [tool]);

  useEffect(() => {
    setSessions(null);
    void refresh();
  }, [refresh]);

  return { sessions, error, setError, loading, refresh };
}

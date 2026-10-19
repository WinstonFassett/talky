// T3 "In tools" data (WIN-577): connection state, projects (filter chips + running count), and one flat,
// paged thread list. Projects poll every 10 s with If-None-Match while the page is visible and T3 is connected;
// a changed snapshot reloads the loaded threads. Not connected: no timer, only Retry / tab open retries.
import { useCallback, useEffect, useRef, useState } from 'react';

import { useDaemonEvent } from '../lib/daemonEvents';
import { ApiError, t3Api, type T3Project, type T3Status, type T3Thread } from './api';

const POLL_MS = 10_000;
const PAGE = 20;

export function useT3Sessions() {
  const [status, setStatus] = useState<T3Status | null>(null);
  const [projects, setProjects] = useState<T3Project[]>([]);
  const [threads, setThreads] = useState<T3Thread[] | null>(null);
  const [total, setTotal] = useState(0);
  const [recent, setRecent] = useState<T3Thread[]>([]);
  const [query, setQueryState] = useState('');
  const queryRef = useRef('');
  const [cursor, setCursor] = useState<string | null>(null);
  const [filter, setFilterState] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const etag = useRef<string | undefined>(undefined);
  const filterRef = useRef<string | null>(null);
  const loadedRef = useRef(PAGE);

  const down = useCallback((e: ApiError) => {
    setStatus((prev) => ({
      state: 'needs_connect',
      code: (e.code as T3Status['code']) ?? null,
      detail: e.message,
      session: null,
      expires_at: null,
      scopes: [],
      base_url: prev?.base_url ?? '',
    }));
    setProjects([]);
    setThreads(null);
    etag.current = undefined;
  }, []);

  const loadThreads = useCallback(
    async (project: string | null, limit: number) => {
      try {
        const out = await t3Api.threads({ project: project ?? undefined, limit, q: queryRef.current });
        setThreads(out.threads);
        setRecent(out.recent ?? []);
        setTotal(out.total);
        setCursor(out.next_cursor);
        loadedRef.current = Math.max(PAGE, out.threads.length);
        setError(null);
      } catch (e) {
        if (e instanceof ApiError && e.status === 503) down(e);
        else setError(String(e instanceof Error ? e.message : e));
      }
    },
    [down],
  );

  // One poll step: projects (cheap, ETag) → if the snapshot moved (or first load), reload the threads.
  const refresh = useCallback(
    async (force = false) => {
      setLoading(true);
      try {
        const out = await t3Api.projects(force ? undefined : etag.current);
        setStatus((s) => (s?.state === 'connected' ? s : { state: 'connected', code: null, detail: '', session: null, expires_at: null, scopes: [], base_url: s?.base_url ?? '' }));
        if (out.notModified) return;
        etag.current = out.etag ?? undefined;
        setProjects(out.projects);
        if (filterRef.current && !out.projects.some((p) => p.id === filterRef.current && p.thread_count > 0)) {
          filterRef.current = null;
          setFilterState(null);
        }
        await loadThreads(filterRef.current, loadedRef.current);
      } catch (e) {
        if (e instanceof ApiError && e.status === 503) down(e);
        else setError(String(e instanceof Error ? e.message : e));
      } finally {
        setLoading(false);
      }
    },
    [down, loadThreads],
  );

  useEffect(() => {
    void refresh(true);
  }, [refresh]);

  const connected = status?.state === 'connected';
  useEffect(() => {
    if (!connected) return;
    const tick = () => document.visibilityState === 'visible' && void refresh();
    const t = setInterval(tick, POLL_MS);
    document.addEventListener('visibilitychange', tick);
    return () => {
      clearInterval(t);
      document.removeEventListener('visibilitychange', tick);
    };
  }, [connected, refresh]);

  // A link change (attach / new Chat) moves `convo_id` on rows; the snapshot ETag doesn't change, so force it.
  const debounce = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useDaemonEvent('convoChanged', (d: { reason?: string }) => {
    if (!connected || (d.reason !== 'created' && d.reason !== 'deleted' && d.reason !== 'updated')) return;
    clearTimeout(debounce.current);
    debounce.current = setTimeout(() => void refresh(true), 400);
  });

  const setFilter = (id: string | null) => {
    filterRef.current = id;
    loadedRef.current = PAGE;
    setFilterState(id);
    setThreads(null);
    void loadThreads(id, PAGE);
  };

  const searchTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const setQuery = (q: string) => {
    setQueryState(q);
    queryRef.current = q;
    loadedRef.current = PAGE;
    clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(() => void loadThreads(filterRef.current, PAGE), 250);
  };

  const loadMore = async () => {
    if (!cursor) return;
    try {
      const out = await t3Api.threads({ project: filterRef.current ?? undefined, cursor, q: queryRef.current });
      setThreads((prev) => [...(prev ?? []), ...out.threads]);
      setCursor(out.next_cursor);
      loadedRef.current += out.threads.length;
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    }
  };

  const onStatus = (st: T3Status) => {
    setStatus(st);
    if (st.state === 'connected') void refresh(true);
  };

  const disconnect = async () => {
    try {
      setStatus(await t3Api.disconnect());
      setProjects([]);
      setThreads(null);
      etag.current = undefined;
    } catch (e) {
      setError(`Couldn't disconnect: ${e instanceof Error ? e.message : e}`);
    }
  };

  const running = projects.reduce((n, p) => n + p.running, 0);
  return { status, projects, threads, total, recent, query, setQuery, cursor, filter, setFilter, error, setError, loading, running, refresh, loadMore, onStatus, disconnect };
}

export type T3Sessions = ReturnType<typeof useT3Sessions>;

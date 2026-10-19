// Sessions (WIN-535): the daemon's saved convos, the one open in the
// transcript, and its history.
//
// voice-ui-kit clears its conversation store on every RTVI `Connected`, so old
// turns can't live there (injectMessage would be wiped when a call starts).
// Instead the transcript is `history` (turns read from disk) followed by the
// live voice-ui-kit messages after `liveMark` — the live messages that were
// already on screen before the session was opened belong to another session.
//
// The open session is in the address (`?session=<id>`, WIN-536), so a reload
// or a shared link opens it, and a loading state covers the gap until its
// turns arrive — the empty state never shows for a session that has messages.
//
// Opening a row binds it on the daemon right away (POST /api/convos/resume +
// a profile switch if needed). No LLM call happens; the next typed or spoken
// turn continues it. That keeps the daemon's "current" convo equal to what's on
// screen, which is what makes a reload reopen it.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import type { AggregationMetadata } from '@pipecat-ai/voice-ui-kit';
import { usePipecatConversation } from '@pipecat-ai/voice-ui-kit';
import { usePipecatClientTransportState, useRTVIClientEvent } from '@pipecat-ai/client-react';
import { RTVIEvent } from '@pipecat-ai/client-js';

import { adaptMessages } from '../messages/adapter';
import { useDaemonEvent } from '../lib/daemonEvents';
import type { TalkyMessage } from '../messages/types';
import {
  convosApi,
  type ConvoChangedEvent,
  type ConvoRow,
  type ConvoTurn,
  type CurrentConvo,
  type ProfileInfo,
} from './api';

const AGGREGATION_METADATA: Record<string, AggregationMetadata> = {
  thinking: { isSpoken: false, displayMode: 'block' as const },
  tool_start: { isSpoken: false, displayMode: 'block' as const },
  tool_end: { isSpoken: false, displayMode: 'block' as const },
  error: { isSpoken: false, displayMode: 'block' as const },
  info: { isSpoken: false, displayMode: 'block' as const },
};

const PAGE = 100;
const OPEN_KEY = 'talky.openConvo';
const URL_PARAM = 'session';

function sessionFromUrl(): string | null {
  try {
    return new URLSearchParams(window.location.search).get(URL_PARAM);
  } catch {
    return null;
  }
}

function writeUrl(id: string | null) {
  try {
    const url = new URL(window.location.href);
    if ((url.searchParams.get(URL_PARAM) ?? null) === id) return;
    if (id) url.searchParams.set(URL_PARAM, id);
    else url.searchParams.delete(URL_PARAM);
    window.history.replaceState(window.history.state, '', url);
  } catch { /* no history API */ }
}

type SessionsValue = {
  rows: ConvoRow[];
  query: string;
  setQuery: (q: string) => void;
  showArchived: boolean;
  setShowArchived: (v: boolean) => void;
  current: CurrentConvo;
  openId: string | null;
  profiles: ProfileInfo[];
  /** history + live messages, in order. */
  messages: TalkyMessage[];
  /** The first load (which session is open) has finished. */
  ready: boolean;
  /** The open session's turns are still loading. */
  loading: boolean;
  hasOlder: boolean;
  loadOlder: () => Promise<void>;
  open: (id: string, profile?: string) => Promise<void>;
  newChat: () => Promise<void>;
  rename: (id: string, title: string) => Promise<void>;
  setPinned: (id: string, pinned: boolean) => Promise<void>;
  setArchived: (id: string, archived: boolean) => Promise<void>;
  remove: (id: string) => Promise<void>;
  error: string | null;
};

const SessionsContext = createContext<SessionsValue | null>(null);

function turnToMessage(convoId: string, t: ConvoTurn): TalkyMessage {
  return {
    id: `h-${convoId}-${t.index}`,
    role: t.role,
    createdAt: new Date(t.timestamp * 1000).toISOString(),
    final: true,
    parts: [{ kind: 'text', spoken: t.content, unspoken: '' }],
  };
}

export function SessionsProvider({ children }: { children: ReactNode }) {
  const live = usePipecatConversation({ aggregationMetadata: AGGREGATION_METADATA });
  const liveMessages = useMemo(() => adaptMessages(live.messages), [live.messages]);
  const liveLenRef = useRef(0);
  liveLenRef.current = liveMessages.length;

  const [rows, setRows] = useState<ConvoRow[]>([]);
  const [query, setQuery] = useState('');
  const [showArchived, setShowArchived] = useState(false);
  const [current, setCurrent] = useState<CurrentConvo>(null);
  const inCallRef = useRef(false);
  const transportState = usePipecatClientTransportState();
  inCallRef.current = transportState === 'connected' || transportState === 'ready';
  const [profiles, setProfiles] = useState<ProfileInfo[]>([]);
  const [openId, setOpenIdState] = useState<string | null>(null);
  const openIdRef = useRef<string | null>(null);
  const [history, setHistory] = useState<TalkyMessage[]>([]);
  const [historyStart, setHistoryStart] = useState(0);
  const [liveMark, setLiveMark] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [loadingId, setLoadingId] = useState<string | null>(null);
  const activeProfileRef = useRef<string>('');
  const loadSeq = useRef(0);

  const setOpenId = useCallback((id: string | null) => {
    openIdRef.current = id;
    setOpenIdState(id);
    try {
      if (id) localStorage.setItem(OPEN_KEY, id);
      else localStorage.removeItem(OPEN_KEY);
    } catch { /* private mode */ }
    writeUrl(id);
  }, []);

  // ── list ──────────────────────────────────────────────────────────────
  const queryRef = useRef(query);
  queryRef.current = query;
  const archivedRef = useRef(showArchived);
  archivedRef.current = showArchived;

  // Only the newest request may set the list: an unfiltered refresh that
  // resolves after a search must not overwrite the search's rows.
  const listSeq = useRef(0);
  const refreshList = useCallback(async () => {
    const seq = ++listSeq.current;
    try {
      const out = await convosApi.list(queryRef.current, archivedRef.current);
      if (seq !== listSeq.current) return out;
      setRows(out.convos);
      setCurrent(out.current);
      return out;
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
      return null;
    }
  }, []);

  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const scheduleRefresh = useCallback(
    (delay = 250) => {
      if (refreshTimer.current) clearTimeout(refreshTimer.current);
      refreshTimer.current = setTimeout(() => void refreshList(), delay);
    },
    [refreshList],
  );

  useEffect(() => {
    scheduleRefresh(query ? 200 : 0);
  }, [query, showArchived, scheduleRefresh]);

  // ── history ───────────────────────────────────────────────────────────
  const loadHistory = useCallback(async (id: string) => {
    const seq = ++loadSeq.current;
    try {
      const page = await convosApi.get(id, { limit: PAGE });
      if (seq !== loadSeq.current || openIdRef.current !== id) return;
      setHistory(page.turns.map((t) => turnToMessage(id, t)));
      setHistoryStart(page.start);
    } finally {
      if (seq === loadSeq.current) setLoadingId((l) => (l === id ? null : l));
    }
  }, []);

  const clearView = useCallback(() => {
    loadSeq.current++;
    setOpenId(null);
    setHistory([]);
    setHistoryStart(0);
    setLiveMark(liveLenRef.current);
  }, [setOpenId]);

  /** Make `id` the daemon's current convo (resume + profile switch); no LLM call. */
  const bind = useCallback(async (id: string, profile?: string) => {
    try {
      // The daemon switches the live pipeline to the chat's profile itself (WIN-582); it answers 409 if it can't.
      await convosApi.resume(id, profile);
      // Read the active profile fresh: the cached one is stale after a daemon restart.
      const now = await convosApi.profiles();
      activeProfileRef.current = now.profiles.find((p) => p.active)?.name ?? '';
    } catch (e) {
      setError(`Couldn't continue session: ${e instanceof Error ? e.message : e}`);
    }
  }, []);

  /** Show a session's transcript. With `bind`, also make it the daemon's current convo. */
  const show = useCallback(
    async (id: string, opts: { bind: boolean; profile?: string }) => {
      setError(null);
      setOpenId(id);
      setLiveMark(liveLenRef.current);
      setHistory([]);
      setHistoryStart(0);
      setLoadingId(id);
      const loading = loadHistory(id).catch((e) => setError(`Couldn't load session: ${e.message ?? e}`));
      if (opts.bind) await bind(id, opts.profile);
      await loading;
    },
    [bind, loadHistory, setOpenId],
  );

  const open = useCallback(
    (id: string, profile?: string) => show(id, { bind: true, profile }),
    [show],
  );

  const loadOlder = useCallback(async () => {
    const id = openIdRef.current;
    if (!id || historyStart <= 0) return;
    const seq = loadSeq.current;
    const page = await convosApi.get(id, { before: historyStart, limit: PAGE });
    // A reconnect or another open reloaded the history meanwhile: this page no longer fits.
    if (openIdRef.current !== id || seq !== loadSeq.current) return;
    setHistory((h) => [...page.turns.map((t) => turnToMessage(id, t)), ...h]);
    setHistoryStart(page.start);
  }, [historyStart]);

  const newChat = useCallback(async () => {
    clearView();
    try {
      await convosApi.startNew();
    } catch (e) {
      setError(`Couldn't start a new session: ${e instanceof Error ? e.message : e}`);
    }
  }, [clearView]);

  // ── row actions ───────────────────────────────────────────────────────
  const patch = useCallback(
    async (id: string, fields: Parameters<typeof convosApi.patch>[1]) => {
      setRows((rs) => rs.map((r) => (r.id === id ? { ...r, ...fields } : r)));
      try {
        await convosApi.patch(id, fields);
      } catch (e) {
        setError(String(e instanceof Error ? e.message : e));
      }
      scheduleRefresh(0);
    },
    [scheduleRefresh],
  );
  const rename = useCallback((id: string, title: string) => patch(id, { title }), [patch]);
  const setPinned = useCallback((id: string, pinned: boolean) => patch(id, { pinned }), [patch]);
  const setArchived = useCallback((id: string, archived: boolean) => patch(id, { archived }), [patch]);
  const remove = useCallback(
    async (id: string) => {
      if (openIdRef.current === id) clearView();
      setRows((rs) => rs.filter((r) => r.id !== id));
      try {
        await convosApi.remove(id);
      } catch (e) {
        setError(String(e instanceof Error ? e.message : e));
      }
      scheduleRefresh(0);
    },
    [clearView, scheduleRefresh],
  );

  // ── initial load: reopen what the daemon (or this browser) had open ────
  useEffect(() => {
    let cancelled = false;
    (async () => {
      convosApi
        .profiles()
        .then((p) => {
          if (cancelled) return;
          setProfiles(p.profiles);
          activeProfileRef.current = p.profiles.find((x) => x.active)?.name ?? '';
        })
        .catch(() => {});
      const fromUrl = sessionFromUrl();
      // An address names the session: show it while the list loads.
      if (fromUrl) {
        openIdRef.current = fromUrl;
        setOpenIdState(fromUrl);
        setLoadingId(fromUrl);
        setReady(true);
      }
      const out = await refreshList();
      if (cancelled) return;
      if (!out) {
        setReady(true);
        setLoadingId(null);
        return;
      }
      if (fromUrl) {
        if (out.convos.some((c) => c.id === fromUrl) || out.current?.id === fromUrl) {
          await show(fromUrl, { bind: out.current?.id !== fromUrl });
        } else {
          setError("That session doesn't exist on this machine.");
          clearView();
          setLoadingId(null);
        }
        return;
      }
      setReady(true);
      if (out.current) {
        await show(out.current.id, { bind: false });
        return;
      }
      let remembered: string | null = null;
      try {
        remembered = localStorage.getItem(OPEN_KEY);
      } catch { /* private mode */ }
      // The daemon restarted (or the reload carry-over expired): rebind what
      // this browser had open, if it still exists.
      if (remembered && out.convos.some((c) => c.id === remembered)) {
        await show(remembered, { bind: true });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [refreshList, show, clearView]);

  // ── live updates ──────────────────────────────────────────────────────
  useDaemonEvent('profileChanged', (d: { type?: string; profile?: string }) => {
    if (d?.type === 'llm' && d.profile) {
      activeProfileRef.current = d.profile;
      setProfiles((ps) => ps.map((p) => ({ ...p, active: p.name === d.profile })));
    }
  });
  useDaemonEvent('init', (d: { profiles?: ProfileInfo[] }) => {
    if (Array.isArray(d?.profiles)) {
      setProfiles(d.profiles);
      activeProfileRef.current = d.profiles.find((p: ProfileInfo) => p.active)?.name ?? '';
    }
  });
  useDaemonEvent('convoChanged', (d: ConvoChangedEvent) => {
    setCurrent((prev) => {
      if (!d.current) return null;
      const status = d.id === d.current ? d.status : prev?.id === d.current ? prev.status : 'idle';
      return { id: d.current, status, live: prev?.live ?? false };
    });
    const openNow = openIdRef.current;
    if (d.reason === 'deleted' && d.id === openNow) {
      clearView();
    } else if (d.current && d.current !== openNow) {
      if (d.reason === 'created' && !openNow) {
        // A fresh call just got its record: the live messages are its turns —
        // unless this tab isn't in the call, then read them from disk.
        setOpenId(d.current);
        if (!inCallRef.current) void loadHistory(d.current).catch(() => {});
      } else {
        // Someone else (the CLI, another tab) switched the daemon's session.
        void show(d.current, { bind: false });
      }
    } else if (!d.current && d.reason === 'current' && openNow) {
      clearView();
    }
    scheduleRefresh(d.reason === 'turn' || d.reason === 'status' ? 400 : 0);
  });

  // Back from a hidden tab and not in a call: catch up on turns saved meanwhile.
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState !== 'visible') return;
      void refreshList();
      const id = openIdRef.current;
      if (id && !inCallRef.current) void loadHistory(id).catch(() => {});
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [loadHistory, refreshList]);

  // A (re)connect clears voice-ui-kit's store, so the previous call's turns
  // vanish from the live list; re-read them from disk.
  useRTVIClientEvent(
    RTVIEvent.Connected,
    useCallback(() => {
      setLiveMark(0);
      const id = openIdRef.current;
      if (!id) return;
      void loadHistory(id).catch(() => {});
      // If the daemon lost the binding (e.g. a queued resume expired while the
      // tab sat idle), bind the session on screen again before the first turn.
      void convosApi
        .list('', false)
        .then((out) => {
          // Only when the daemon has nothing bound: another convo (CLI, other tab) wins.
          // It continues on its own profile — after a daemon restart the "active"
          // profile is just the config default, not the user's choice.
          if (openIdRef.current === id && !out.current) return bind(id);
        })
        .catch(() => {});
    }, [bind, loadHistory]),
  );

  const mark = liveMark > liveMessages.length ? 0 : liveMark;
  const messages = useMemo(
    () => (history.length ? [...history, ...liveMessages.slice(mark)] : liveMessages.slice(mark)),
    [history, liveMessages, mark],
  );

  const value: SessionsValue = {
    rows,
    query,
    setQuery,
    showArchived,
    setShowArchived,
    current,
    openId,
    profiles,
    messages,
    ready,
    loading: !ready || (loadingId !== null && loadingId === openId),
    hasOlder: historyStart > 0,
    loadOlder,
    open,
    newChat,
    rename,
    setPinned,
    setArchived,
    remove,
    error,
  };
  return <SessionsContext.Provider value={value}>{children}</SessionsContext.Provider>;
}

export function useSessions(): SessionsValue {
  const v = useContext(SessionsContext);
  if (!v) throw new Error('useSessions must be used inside <SessionsProvider>');
  return v;
}

export function useOptionalSessions(): SessionsValue | null {
  return useContext(SessionsContext);
}

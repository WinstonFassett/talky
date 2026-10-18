// A turn: from the moment a message goes out (or the bot starts on a spoken
// one) until the bot has finished thinking, synthesizing and speaking (WIN-536).
//
// Drives the Stop button, Esc, the thinking indicator, the task timer, the tab
// title, and the transcript notices (errors and "nothing visible" placed in time,
// after the message they belong to, cleared when the next turn starts).
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
import { usePipecatClient, useRTVIClientEvent } from '@pipecat-ai/client-react';
import { RTVIEvent } from '@pipecat-ai/client-js';

import { useSessions } from '../sessions/SessionsProvider';
import { useCall } from '../call/CallProvider';
import { useDaemonEvent } from '../lib/daemonEvents';

export type Notice = {
  id: number;
  kind: 'error' | 'info';
  /** One readable line. */
  text: string;
  /** Full text, shown on demand. */
  detail?: string;
  /** Shown after this many transcript messages. */
  after: number;
  /** Cleared when the next turn starts. */
  transient: boolean;
  action?: { label: string; run: () => void };
};

type TurnValue = {
  running: boolean;
  /** ms epoch the running turn started, else null. */
  startedAt: number | null;
  /** Nothing visible has arrived yet in the running turn. */
  waiting: boolean;
  /** A message just went out. */
  begin: () => void;
  /** Cancel the running turn. */
  stop: () => void;
  notices: Notice[];
  addNotice: (n: Omit<Notice, 'id' | 'after'> & { after?: number }) => void;
  dismiss: (id: number) => void;
};

const TurnContext = createContext<TurnValue | null>(null);

export const EMPTY_REPLY = 'Agent completed without a visible response. Please retry.';
const END_GRACE = 600;
const PENDING_TIMEOUT = 30000;
const TITLE = 'Talky';

let nextId = 1;

export function TurnProvider({ children }: { children: ReactNode }) {
  const client = usePipecatClient();
  const call = useCall();
  const { messages, openId } = useSessions();
  const countRef = useRef(messages.length);
  countRef.current = messages.length;

  const [running, setRunning] = useState(false);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [waiting, setWaiting] = useState(false);
  const [notices, setNotices] = useState<Notice[]>([]);

  // Raw signals.
  const pending = useRef(false);
  const llm = useRef(false);
  const tts = useRef(false);
  const speaking = useRef(false);
  const sawLlm = useRef(false);
  const sawOutput = useRef(false);
  const stopped = useRef(false);
  const runningRef = useRef(false);
  const endTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const addNotice = useCallback((n: Omit<Notice, 'id' | 'after'> & { after?: number }) => {
    setNotices((ns) => [...ns, { ...n, id: nextId++, after: n.after ?? countRef.current }]);
  }, []);
  const dismiss = useCallback((id: number) => setNotices((ns) => ns.filter((n) => n.id !== id)), []);

  const start = useCallback(() => {
    if (runningRef.current) return;
    runningRef.current = true;
    sawLlm.current = false;
    sawOutput.current = false;
    stopped.current = false;
    setRunning(true);
    setWaiting(true);
    setStartedAt(Date.now());
    setNotices((ns) => ns.filter((n) => !n.transient));
  }, []);

  const finish = useCallback(() => {
    if (!runningRef.current) return;
    runningRef.current = false;
    pending.current = false;
    setRunning(false);
    setWaiting(false);
    setStartedAt(null);
    if (sawLlm.current && !sawOutput.current && !stopped.current) {
      setNotices((ns) => [...ns, { id: nextId++, kind: 'info', text: EMPTY_REPLY, after: countRef.current, transient: true }]);
    }
  }, []);

  // The turn is over once every signal has been quiet for a moment (there are
  // short gaps between the LLM finishing and TTS starting, and between sentences).
  const settle = useCallback(() => {
    if (endTimer.current) clearTimeout(endTimer.current);
    endTimer.current = null;
    if (pending.current || llm.current || tts.current || speaking.current) return;
    endTimer.current = setTimeout(() => {
      if (!pending.current && !llm.current && !tts.current && !speaking.current) finish();
    }, END_GRACE);
  }, [finish]);

  const begin = useCallback(() => {
    pending.current = true;
    if (pendingTimer.current) clearTimeout(pendingTimer.current);
    // A profile with no LLM of its own (MCP mode) never reports one starting.
    pendingTimer.current = setTimeout(() => {
      pending.current = false;
      settle();
    }, PENDING_TIMEOUT);
    if (runningRef.current) {
      // A new message while one is running starts a new turn.
      runningRef.current = false;
    }
    start();
  }, [settle, start]);

  const stop = useCallback(() => {
    if (!runningRef.current) return;
    stopped.current = true;
    client?.sendClientMessage('interrupt');
    pending.current = false;
    llm.current = false;
    tts.current = false;
    speaking.current = false;
    finish();
  }, [client, finish]);

  const set = (flag: { current: boolean }, v: boolean) => {
    flag.current = v;
    if (v) {
      if (endTimer.current) clearTimeout(endTimer.current);
      endTimer.current = null;
    } else settle();
  };

  useRTVIClientEvent(
    RTVIEvent.BotLlmStarted,
    useCallback(() => {
      pending.current = false;
      if (pendingTimer.current) clearTimeout(pendingTimer.current);
      if (!runningRef.current) start();
      sawLlm.current = true;
      set(llm, true);
    }, [start]), // eslint-disable-line react-hooks/exhaustive-deps
  );
  useRTVIClientEvent(RTVIEvent.BotLlmStopped, useCallback(() => set(llm, false), [settle])); // eslint-disable-line react-hooks/exhaustive-deps
  useRTVIClientEvent(RTVIEvent.BotTtsStarted, useCallback(() => set(tts, true), [settle])); // eslint-disable-line react-hooks/exhaustive-deps
  useRTVIClientEvent(RTVIEvent.BotTtsStopped, useCallback(() => set(tts, false), [settle])); // eslint-disable-line react-hooks/exhaustive-deps
  useRTVIClientEvent(RTVIEvent.BotStartedSpeaking, useCallback(() => set(speaking, true), [settle])); // eslint-disable-line react-hooks/exhaustive-deps
  useRTVIClientEvent(RTVIEvent.BotStoppedSpeaking, useCallback(() => set(speaking, false), [settle])); // eslint-disable-line react-hooks/exhaustive-deps
  useRTVIClientEvent(
    RTVIEvent.BotOutput,
    useCallback((data: { text?: string }) => {
      if (!runningRef.current || !data?.text?.trim()) return;
      sawOutput.current = true;
      setWaiting(false);
    }, []),
  );
  // The call ended mid-turn: nothing more will arrive.
  useRTVIClientEvent(
    RTVIEvent.Disconnected,
    useCallback(() => {
      stopped.current = true;
      pending.current = llm.current = tts.current = speaking.current = false;
      finish();
    }, [finish]),
  );

  // Backend failures are notices in the transcript, after the turn they ended.
  useDaemonEvent('backendError', (d: { message?: string }) => {
    if (!d?.message) return;
    const [first, ...rest] = String(d.message).split(/(?<=[.!?])\s+|\n/);
    addNotice({
      kind: 'error',
      text: first,
      detail: rest.length ? String(d.message) : undefined,
      transient: true,
      // A dead backend comes back with a fresh call.
      action: {
        label: 'Reconnect',
        run: () => {
          call.disconnect();
          setTimeout(() => void call.connect(), 500);
        },
      },
    });
    stopped.current = true;
    pending.current = llm.current = tts.current = speaking.current = false;
    finish();
  });
  useDaemonEvent('turnResumed', (d: { convo_id?: string }) => {
    if (d?.convo_id && d.convo_id !== openId) return;
    addNotice({ kind: 'info', text: 'This turn was interrupted by a restart. Resuming from the last saved message.', transient: false });
  });

  // Another session on screen: its notices don't belong here.
  useEffect(() => {
    setNotices([]);
  }, [openId]);

  // Esc cancels the running turn, unless a dialog or menu has it.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented || !runningRef.current) return;
      if (document.querySelector('[role="dialog"], [role="menu"], [role="listbox"], [data-state="open"][data-radix-popper-content-wrapper]')) return;
      stop();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [stop]);

  // The tab title says it is working.
  useEffect(() => {
    document.title = running ? `Working… · ${TITLE}` : TITLE;
  }, [running]);

  const value = useMemo(
    () => ({ running, startedAt, waiting, begin, stop, notices, addNotice, dismiss }),
    [running, startedAt, waiting, begin, stop, notices, addNotice, dismiss],
  );
  return <TurnContext.Provider value={value}>{children}</TurnContext.Provider>;
}

export function useTurn(): TurnValue {
  const v = useContext(TurnContext);
  if (!v) throw new Error('useTurn must be used inside <TurnProvider>');
  return v;
}

/** Seconds since `since`, ticking once a second while `live`. */
export function useElapsed(since: number | null, live: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!live) return;
    setNow(Date.now());
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [live]);
  return since === null ? 0 : Math.max(0, Math.floor((now - since) / 1000));
}

export function formatDuration(secs: number): string {
  if (secs < 60) return `${secs}s`;
  return `${Math.floor(secs / 60)}m ${String(secs % 60).padStart(2, '0')}s`;
}

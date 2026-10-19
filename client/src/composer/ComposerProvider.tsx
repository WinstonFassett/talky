// The composer's text and how a message goes out (WIN-536).
//
// - The composer works before a call: Send starts one (mic muted, since the
//   user chose to type) and delivers the message once it is connected.
// - A send that can't go out (the call is reconnecting or offline) keeps the
//   draft and says so.
// - Drafts are saved per session and come back after a reload; a draft older
//   than an hour is dropped.
// - Starters, Retry, Edit and Branch off use the same `submit` / `setText`.
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from 'react';
import { usePipecatClient } from '@pipecat-ai/client-react';
import { useConversationContext } from '@pipecat-ai/voice-ui-kit';

import { useCall } from '../call/CallProvider';
import { useTurn } from '../turn/TurnProvider';
import { useSessions } from '../sessions/SessionsProvider';

export const NOT_SENT = 'Message not sent: reconnecting. Your draft is still in the composer';
const NOT_CONNECTED = "Message not sent: couldn't start the call. Your draft is still in the composer";
const DRAFT_PREFIX = 'talky.draft.v1:';
const DRAFT_TTL = 60 * 60 * 1000;

type ComposerValue = {
  text: string;
  setText: (t: string) => void;
  /** Send the composer's text. */
  send: () => void;
  /** Send `text` without touching the draft (starters, Retry). */
  submit: (text: string) => void;
  /** A message is waiting for the call to connect. */
  sending: boolean;
  error: string | null;
  inputRef: RefObject<HTMLTextAreaElement | null>;
  focus: () => void;
};

const ComposerContext = createContext<ComposerValue | null>(null);

const draftKey = (convo: string | null) => DRAFT_PREFIX + (convo ?? 'new');

function readDraft(convo: string | null): string {
  try {
    const raw = localStorage.getItem(draftKey(convo));
    if (!raw) return '';
    const d = JSON.parse(raw) as { text: string; at: number };
    if (Date.now() - d.at > DRAFT_TTL) {
      localStorage.removeItem(draftKey(convo));
      return '';
    }
    return d.text ?? '';
  } catch {
    return '';
  }
}

function writeDraft(convo: string | null, text: string) {
  try {
    if (text) localStorage.setItem(draftKey(convo), JSON.stringify({ text, at: Date.now() }));
    else localStorage.removeItem(draftKey(convo));
  } catch { /* private mode */ }
}

export function ComposerProvider({ children }: { children: ReactNode }) {
  const client = usePipecatClient();
  const { injectMessage } = useConversationContext();
  const call = useCall();
  const turn = useTurn();
  const { openId, ready } = useSessions();
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  // The draft is on screen at first paint: the address names the session
  // (no address = a new chat), so it needn't wait for the session list.
  const urlSession = useRef<string | null>(
    typeof window === 'undefined' ? null : new URLSearchParams(window.location.search).get('session'),
  );
  const [text, setTextState] = useState(() => readDraft(urlSession.current));
  const [error, setError] = useState<string | null>(null);
  const [queued, setQueued] = useState<{ text: string; fromDraft: boolean } | null>(null);

  // Drafts follow the session on screen.
  const openRef = useRef<string | null>(openId ?? urlSession.current);
  const loaded = useRef(false);
  const touched = useRef(false);
  useEffect(() => {
    if (!ready) return;
    const prev = openRef.current;
    openRef.current = openId;
    if (!loaded.current) {
      loaded.current = true;
      // The session that actually opened isn't the one guessed at first paint
      // (e.g. the daemon's current one): show its draft, unless typing began.
      if (openId !== urlSession.current) {
        if (!touched.current) setTextState(readDraft(openId));
        else {
          // Typed before the session was known: the draft belongs to the one that opened.
          setTextState((t) => {
            writeDraft(urlSession.current, '');
            writeDraft(openId, t);
            return t;
          });
        }
      }
      return;
    }
    // A brand-new chat getting its id keeps whatever is being typed.
    if (prev === null && openId !== null) {
      const draft = readDraft(openId);
      if (draft) setTextState(draft);
      return;
    }
    if (prev !== openId) setTextState(readDraft(openId));
  }, [openId, ready]);

  const setText = useCallback((t: string) => {
    touched.current = true;
    setTextState(t);
    setError(null);
    writeDraft(openRef.current, t);
  }, []);

  const focus = useCallback(() => {
    const el = inputRef.current;
    if (!el) return;
    el.focus();
    el.setSelectionRange(el.value.length, el.value.length);
  }, []);

  const deliver = useCallback(
    async (value: string, fromDraft: boolean) => {
      if (!client) return;
      // Optimistic user turn: the daemon doesn't echo typed text back (9e9b / 5bc6).
      injectMessage({ role: 'user', parts: [{ text: value, final: true, createdAt: new Date().toISOString() }] });
      turn.begin();
      try {
        // run_immediately ends the current user turn so each typed message is its own turn.
        await client.sendText(value, { run_immediately: true });
        if (fromDraft) {
          setTextState((t) => (t === value ? '' : t));
          writeDraft(openRef.current, '');
        }
      } catch (err) {
        console.warn('sendText failed', err);
        setError(NOT_SENT);
      }
    },
    [client, injectMessage, turn],
  );

  const go = useCallback(
    (raw: string, fromDraft: boolean) => {
      const value = raw.trim();
      if (!value) return;
      setError(null);
      if (call.state === 'reconnecting' || call.state === 'offline' || navigator.onLine === false) {
        if (call.state === 'offline') call.retry();
        setError(NOT_SENT);
        if (!fromDraft) setText(raw);
        return;
      }
      if (call.state === 'connected') {
        void deliver(value, fromDraft);
        return;
      }
      setQueued({ text: value, fromDraft });
      if (call.state === 'disconnected') {
        void call.connect().then((ok) => {
          if (!ok) {
            setQueued(null);
            setError(NOT_CONNECTED);
            if (!fromDraft) setText(raw);
          }
        });
      }
    },
    [call, deliver, setText],
  );

  // Deliver what was waiting once the call is up.
  useEffect(() => {
    if (!queued) return;
    if (call.state === 'connected') {
      setQueued(null);
      void deliver(queued.text, queued.fromDraft);
    } else if (call.state === 'reconnecting' || call.state === 'offline') {
      setQueued(null);
      setError(NOT_SENT);
    }
  }, [call.state, queued, deliver]);

  const send = useCallback(() => go(text, true), [go, text]);
  const submit = useCallback((t: string) => go(t, false), [go]);

  const value = useMemo(
    () => ({ text, setText, send, submit, sending: !!queued, error, inputRef, focus }),
    [text, setText, send, submit, queued, error, focus],
  );
  return <ComposerContext.Provider value={value}>{children}</ComposerContext.Provider>;
}

export function useComposer(): ComposerValue {
  const v = useContext(ComposerContext);
  if (!v) throw new Error('useComposer must be used inside <ComposerProvider>');
  return v;
}

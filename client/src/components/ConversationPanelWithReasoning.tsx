import {
  cloneElement,
  isValidElement,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useId,
  useRef,
  useState,
  memo,
  Fragment,
  type ReactNode,
} from 'react';
import { usePipecatClient } from '@pipecat-ai/client-react';
import {
  ArrowDownIcon,
  CheckIcon,
  CopyIcon,
  GitBranchIcon,
  Loader2Icon,
  PencilIcon,
  RotateCcwIcon,
} from 'lucide-react';
import { Reasoning, ReasoningContent, ReasoningTrigger } from './ai-elements/reasoning';
import { TalkyTextInput } from './TalkyTextInput';
import { Streamdown } from 'streamdown';

import { useTalkyMessages } from '../messages/useTalkyMessages';
import type { TalkyMessage, TalkyPart } from '../messages/types';
import { SteerModeChip } from './SteerModeChip';
import { BotSpeakingBar } from './BotSpeakingBar';
import { ConverseButton } from './ConverseButton';
import { EmptyState } from './EmptyState';
import type { VoiceState } from './useVoiceState';
import { useUrlParam } from '../fixtures/harness';
import { useSessions } from '../sessions/SessionsProvider';
import { formatDuration, useElapsed, useTurn, type Notice } from '../turn/TurnProvider';
import { useComposer } from '../composer/ComposerProvider';

export interface ConversationChromeProps {
  voiceState: VoiceState;
  connected: boolean;
  connecting: boolean;
  error: boolean;
  onConnect: () => void;
  onDisconnect: () => void;
}

/** Within this many px of the bottom, the transcript follows new content. */
const FOLLOW_SLACK = 80;
/** A turn with no visible output for this long shows the thinking indicator. */
const THINKING_DELAY = 600;
/** The whole-task timer appears once a turn has run this long. */
const TASK_TIMER_AFTER = 10;

// Streamdown plugins are heavy (Shiki for code, KaTeX for math, Mermaid for
// diagrams). Only code is useful here, and it's lazy-loaded so first paint
// doesn't pay for it. Mermaid / math / cjk are dropped — voice transcripts
// almost never contain them.
type StreamdownPlugins = NonNullable<Parameters<typeof Streamdown>[0]['plugins']>;
let codePluginPromise: Promise<StreamdownPlugins> | null = null;
function useCodePlugin(): StreamdownPlugins | undefined {
  const [plugins, setPlugins] = useState<StreamdownPlugins | undefined>(undefined);
  useEffect(() => {
    codePluginPromise ??= import('@streamdown/code').then((m) => ({ code: m.code }));
    let cancelled = false;
    codePluginPromise.then((p) => { if (!cancelled) setPlugins(p); });
    return () => { cancelled = true; };
  }, []);
  return plugins;
}

// ─── CODE BLOCKS ───────────────────────────────────────────────────────
// Streamdown's own copy control is an unlabeled icon; ours says "Copy" and
// "Copied" for a moment after a click.
// The flash is keyed by the block's text, not component state: the lazy
// highlighter swaps the code subtree out once it loads, remounting this
// button — a state-only flag would die with the old instance.
const copyFlash = new Map<string, number>();

function CodeCopy() {
  const ref = useRef<HTMLButtonElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [copied, setCopied] = useState(false);
  // A stable accessible name, independent of the Copied flash: the visible
  // text toggles but the control is still "the copy button".
  const labelId = useId();

  const codeText = () => {
    const block = ref.current?.closest('.talky-code');
    const code = block?.querySelector('pre code') ?? block?.querySelector('code');
    // innerText keeps the line breaks between the highlighter's line spans;
    // textContent flattens them.
    return ((code instanceof HTMLElement ? code.innerText : code?.textContent) ?? '').replace(/\n$/, '');
  };

  // A remount after the highlighter upgrade: pick the flash back up.
  useLayoutEffect(() => {
    const until = copyFlash.get(codeText());
    if (until && until > Date.now()) {
      setCopied(true);
      timer.current = setTimeout(() => setCopied(false), until - Date.now());
    }
  }, []);

  const copy = () => {
    const text = codeText();
    copyFlash.set(text, Date.now() + 1000);
    setCopied(true);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(false), 1000);
    void navigator.clipboard.writeText(text).catch(() => setCopied(false));
  };
  return (
    <>
      <span id={labelId} className="sr-only">Copy code</span>
      <button
        ref={ref}
        type="button"
        onClick={copy}
        aria-labelledby={labelId}
        data-testid="code-copy"
        className="absolute right-1.5 top-1.5 z-10 rounded px-1.5 py-0.5 font-mono text-[11px] transition-colors hover:bg-[var(--color-panel-3)]"
        style={{ color: copied ? 'var(--color-success)' : 'var(--color-text-dim)' }}
      >
        {copied ? 'Copied' : 'Copy'}
      </button>
    </>
  );
}

const STREAMDOWN_COMPONENTS = {
  pre: ({ children }: { children?: ReactNode }) => (
    <div className="talky-code relative">
      {isValidElement(children) ? cloneElement(children as React.ReactElement<Record<string, unknown>>, { 'data-block': 'true' }) : children}
      <CodeCopy />
    </div>
  ),
};
const STREAMDOWN_CONTROLS = { code: false } as const;

type TextChunk = { kind: 'text'; spoken: string; unspoken: string; key: number };
type ToolChunk = { kind: 'tool'; name: string; key: number; startKey: string; endKey: string | null; failed: boolean };
type BlockChunk = { kind: 'block'; part: TalkyPart; key: number };
type RenderChunk = TextChunk | ToolChunk | BlockChunk;

const toolName = (content: string) => content.replace(/^[▶✓✗⟳]\s*/, '').split(/[:(]/)[0].trim();

function buildChunks(messageId: string, parts: TalkyPart[]): RenderChunk[] {
  const out: RenderChunk[] = [];
  const open: ToolChunk[] = [];
  parts.forEach((part, i) => {
    if (part.kind === 'thinking') return;
    if (part.kind === 'text') {
      if (!part.spoken && !part.unspoken) return;
      out.push({ kind: 'text', spoken: part.spoken, unspoken: part.unspoken, key: i });
      return;
    }
    if (part.kind === 'tool_start') {
      const chunk: ToolChunk = { kind: 'tool', name: part.content.replace(/^▶\s*/, ''), key: i, startKey: `${messageId}:${i}`, endKey: null, failed: false };
      open.push(chunk);
      out.push(chunk);
      return;
    }
    if (part.kind === 'tool_end') {
      // Pair with the oldest unfinished call of the same tool.
      const name = toolName(part.content);
      const idx = open.findIndex((c) => toolName(c.name) === name);
      if (idx >= 0) {
        const [chunk] = open.splice(idx, 1);
        chunk.endKey = `${messageId}:${i}`;
        chunk.failed = part.content.trim().startsWith('✗');
      } else {
        out.push({ kind: 'block', part, key: i });
      }
      return;
    }
    out.push({ kind: 'block', part, key: i });
  });
  return out;
}

// When each tool part was first seen. The daemon's parts carry no time, so the
// client stamps them on arrival.
const seenAt = new Map<string, number>();
function stamp(key: string | null): number | null {
  if (key === null) return null;
  if (!seenAt.has(key)) seenAt.set(key, Date.now());
  return seenAt.get(key)!;
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString('en-US', {
      hour12: false,
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  } catch {
    return '';
  }
}

function partAsString(part: TalkyPart): string {
  if (part.kind === 'text') return part.spoken + part.unspoken;
  return part.content;
}

function messageText(message: TalkyMessage): string {
  return message.parts
    .filter((p) => p.kind === 'text')
    .map(partAsString)
    .join('\n\n');
}

function authorLabel(message: TalkyMessage): string {
  if (message.role === 'user') return 'You';
  if (message.profile) return message.profile.charAt(0).toUpperCase() + message.profile.slice(1);
  return 'Assistant';
}

// ─── KARAOKE TEXT ──────────────────────────────────────────────────────
function KaraokePart({
  spoken,
  unspoken,
  isStreaming,
}: {
  spoken: string;
  unspoken: string;
  isStreaming: boolean;
}) {
  const plugins = useCodePlugin();
  return (
    <span className="karaoke-part" data-testid="karaoke-part">
      {spoken && (
        <Streamdown
          className="karaoke-spoken"
          plugins={plugins}
          components={STREAMDOWN_COMPONENTS}
          controls={STREAMDOWN_CONTROLS}
          parseIncompleteMarkdown={isStreaming}
          isAnimating={isStreaming && !unspoken}
        >
          {spoken}
        </Streamdown>
      )}
      {unspoken && (
        <Streamdown
          className="karaoke-unspoken text-muted-foreground"
          plugins={plugins}
          components={STREAMDOWN_COMPONENTS}
          controls={STREAMDOWN_CONTROLS}
          parseIncompleteMarkdown={isStreaming}
          isAnimating={false}
        >
          {unspoken}
        </Streamdown>
      )}
    </span>
  );
}

// ─── TOOL ROW ──────────────────────────────────────────────────────────
// A call shows its elapsed time, ticking each second while it runs and
// frozen when it ends. A call the turn ended without a result for is an
// error, not a timer that climbs forever.
function ToolRow({ chunk, live }: { chunk: ToolChunk; live: boolean }) {
  const startedAt = stamp(chunk.startKey)!;
  const endedAt = stamp(chunk.endKey);
  const state = endedAt !== null ? (chunk.failed ? 'failed' : 'done') : live ? 'running' : 'error';
  // A stuck call's clock stops when the turn did.
  const stuckKey = `${chunk.startKey}:stuck`;
  const stoppedAt = state === 'error' ? stamp(stuckKey) : null;
  const ticking = useElapsed(startedAt, state === 'running');
  const secs =
    state === 'running' ? ticking : Math.max(0, Math.floor(((endedAt ?? stoppedAt ?? startedAt) - startedAt) / 1000));
  const icon = state === 'running' ? '⟳' : state === 'done' ? '✓' : '✗';
  const label = state === 'running' ? 'Running' : state === 'done' ? 'Done' : state === 'failed' ? 'Failed' : 'No result';
  const bad = state === 'error' || state === 'failed';
  return (
    <div
      className="flex items-center gap-2 py-1 text-xs font-mono"
      data-testid="tool-row"
      data-state={state}
      data-elapsed={secs}
      style={{ color: bad ? 'var(--color-destructive)' : 'var(--color-muted-foreground)' }}
    >
      <span className="opacity-60" aria-hidden="true">{icon}</span>
      <span className="opacity-50 uppercase tracking-wider shrink-0">{label}</span>
      <span className="opacity-80 truncate min-w-0">{chunk.name}</span>
      <span className="ml-auto shrink-0 tabular-nums opacity-70" data-testid="tool-timer">
        {formatDuration(secs)}
      </span>
    </div>
  );
}

function InfoBlock({ content }: { content: string }) {
  return (
    <div className="text-xs opacity-50 py-0.5" style={{ color: 'var(--color-text-mute)' }}>
      {content}
    </div>
  );
}

// ─── NOTICES & ERRORS ──────────────────────────────────────────────────
// One readable line; the full text behind "Show details".
function Summarised({ text, detail, kind }: { text: string; detail?: string; kind: 'error' | 'info' }) {
  const [open, setOpen] = useState(false);
  const color = kind === 'error' ? 'var(--color-destructive)' : 'var(--color-text-dim)';
  return (
    <div className="min-w-0">
      <div className="flex items-center gap-2 min-w-0" style={{ color }}>
        {kind === 'error' && <span aria-hidden="true">✗</span>}
        <span className="truncate min-w-0" title={text}>{text}</span>
        {detail && (
          <button
            type="button"
            className="shrink-0 text-[11px] underline-offset-2 hover:underline"
            style={{ color: 'var(--color-text-mute)' }}
            aria-expanded={open}
            onClick={() => setOpen(!open)}
          >
            {open ? 'Hide details' : 'Show details'}
          </button>
        )}
      </div>
      {detail && open && (
        <pre
          className="mt-1 max-h-60 overflow-auto whitespace-pre-wrap break-words rounded px-2 py-1.5 font-mono text-[11px]"
          style={{ backgroundColor: 'var(--color-panel-2)', color: 'var(--color-text-dim)' }}
        >
          {detail}
        </pre>
      )}
    </div>
  );
}

function splitError(content: string): { text: string; detail?: string } {
  const flat = content.trim();
  const first = flat.split(/\n|(?<=[.!?])\s+/)[0];
  return first.length < flat.length ? { text: first, detail: flat } : { text: flat };
}

function ErrorBlock({ content }: { content: string }) {
  const { text, detail } = splitError(content);
  return (
    <div className="text-xs font-mono py-0.5">
      <Summarised text={text} detail={detail} kind="error" />
    </div>
  );
}

function NoticeRow({ notice }: { notice: Notice }) {
  return (
    <div
      className="py-2 text-[13px]"
      data-testid="transcript-notice"
      data-kind={notice.kind}
      style={{ borderBottom: '1px solid var(--color-border-soft)' }}
    >
      <Summarised text={notice.text} detail={notice.detail} kind={notice.kind} />
      {notice.action && (
        <button
          type="button"
          className="mt-1.5 rounded-md px-2.5 py-1 text-[12px] font-medium"
          style={{ border: '1px solid var(--color-border)' }}
          onClick={notice.action.run}
        >
          {notice.action.label}
        </button>
      )}
    </div>
  );
}

function renderBlock(part: TalkyPart): ReactNode {
  if (part.kind === 'tool_start' || part.kind === 'tool_end') {
    return <InfoBlock content={part.content} />;
  }
  if (part.kind === 'info') return <InfoBlock content={part.content} />;
  if (part.kind === 'error') return <ErrorBlock content={part.content} />;
  return null;
}

// ─── MESSAGE ACTIONS ───────────────────────────────────────────────────
function ActionButton({
  label,
  title,
  onClick,
  children,
  disabled,
}: {
  label: string;
  title?: string;
  onClick: () => void;
  children: ReactNode;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={title ?? label}
      onClick={onClick}
      disabled={disabled}
      className="inline-flex items-center justify-center size-6 rounded transition-colors hover:bg-[var(--color-panel-2)] disabled:opacity-40"
      style={{ color: 'var(--color-text-mute)' }}
    >
      {children}
    </button>
  );
}

function CopyMessage({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <ActionButton
      label={copied ? 'Copied' : 'Copy message'}
      onClick={() => {
        void navigator.clipboard.writeText(text).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        });
      }}
    >
      {copied ? <CheckIcon size={13} style={{ color: 'var(--color-success)' }} /> : <CopyIcon size={13} />}
    </ActionButton>
  );
}

// Retry sends the same words again; Edit puts them back in the composer to
// change and send; Branch off starts a new session with them in the composer.
// Backends keep their own history, so none of these rewrite past turns.
function UserActions({ text }: { text: string }) {
  const composer = useComposer();
  const { newChat } = useSessions();
  return (
    <>
      <ActionButton label="Retry" title="Retry: send this message again" onClick={() => composer.submit(text)}>
        <RotateCcwIcon size={13} />
      </ActionButton>
      <ActionButton
        label="Edit"
        title="Edit: put this message back in the composer"
        onClick={() => {
          composer.setText(text);
          composer.focus();
        }}
      >
        <PencilIcon size={13} />
      </ActionButton>
      <ActionButton
        label="Branch off"
        title="Branch off: start a new session from this message"
        onClick={() => {
          void newChat().then(() => {
            composer.setText(text);
            composer.focus();
          });
        }}
      >
        <GitBranchIcon size={13} />
      </ActionButton>
    </>
  );
}

// ─── THINKING ──────────────────────────────────────────────────────────
// Reasoning is folded: a closed disclosure above the answer, open on demand.
function ThinkingBlock({ text, isStreaming }: { text: string; isStreaming: boolean }) {
  return (
    <Reasoning isStreaming={isStreaming} defaultOpen={false} className="mb-2">
      <ReasoningTrigger />
      <ReasoningContent>{text}</ReasoningContent>
    </Reasoning>
  );
}

// ─── MESSAGE ROW ───────────────────────────────────────────────────────
function MessageRow({ message, live }: { message: TalkyMessage; live: boolean }) {
  const isUser = message.role === 'user';
  const isStreaming = !message.final;

  const thinkingText = useMemo(
    () =>
      message.parts
        .filter((p): p is Extract<TalkyPart, { kind: 'thinking' }> => p.kind === 'thinking')
        .map((p) => p.content)
        .join(''),
    [message.parts],
  );

  const chunks = useMemo(() => buildChunks(message.id, message.parts), [message.id, message.parts]);
  const ts = formatTime(message.createdAt);
  const fullText = useMemo(() => messageText(message), [message]);

  return (
    <div
      className="message-row group py-3 relative"
      style={{ borderBottom: '1px solid var(--color-border-soft)' }}
      data-testid="transcript-message"
      data-message-id={message.id}
      data-role={message.role}
    >
      <div className="flex items-center gap-2 mb-1 min-h-[22px]">
        <span
          className="text-[13px] font-medium"
          style={{ color: isUser ? 'var(--color-accent)' : 'var(--color-text-dim)' }}
        >
          {authorLabel(message)}
        </span>
        <div className="flex-1" />
        <span
          className="message-meta font-mono text-[11px]"
          style={{ color: 'var(--color-text-mute)' }}
        >
          {ts}
        </span>
        {fullText && (
          <div className="message-actions flex items-center gap-0.5">
            {isUser && <UserActions text={fullText} />}
            <CopyMessage text={fullText} />
          </div>
        )}
      </div>

      {thinkingText && !isUser && (
        <ThinkingBlock text={thinkingText} isStreaming={isStreaming} />
      )}

      <div className="text-[15px] leading-relaxed max-w-[65ch] min-w-0">
        {chunks.map((c) =>
          c.kind === 'block' ? (
            <Fragment key={c.key}>{renderBlock(c.part)}</Fragment>
          ) : c.kind === 'tool' ? (
            <ToolRow key={c.key} chunk={c} live={live} />
          ) : (
            <KaraokePart
              key={c.key}
              spoken={c.spoken}
              unspoken={c.unspoken}
              isStreaming={isStreaming}
            />
          ),
        )}
      </div>
    </div>
  );
}

// ─── TURN STATUS ───────────────────────────────────────────────────────
function ThinkingIndicator() {
  const { running, waiting } = useTurn();
  const [show, setShow] = useState(false);
  useEffect(() => {
    if (!(running && waiting)) {
      setShow(false);
      return;
    }
    const t = setTimeout(() => setShow(true), THINKING_DELAY);
    return () => clearTimeout(t);
  }, [running, waiting]);
  return (
    <div role="status" aria-live="polite" className="min-h-0">
      {show && (
        <div className="flex items-center gap-2 py-3 text-[13px]" data-testid="thinking-indicator" style={{ color: 'var(--color-text-dim)' }}>
          <Loader2Icon size={13} className="animate-spin" aria-hidden="true" />
          Thinking…
        </div>
      )}
    </div>
  );
}

function TaskTimer() {
  const { running, startedAt } = useTurn();
  const secs = useElapsed(startedAt, running);
  if (!running || secs < TASK_TIMER_AFTER) return null;
  return (
    <span data-testid="task-timer" className="font-mono text-[11px] tabular-nums shrink-0" style={{ color: 'var(--color-text-dim)' }}>
      task {formatDuration(secs)}
    </span>
  );
}

// ─── TRANSCRIPT ────────────────────────────────────────────────────────
function ConversationMessages({
  activeProfile,
  chrome,
}: {
  activeProfile: string;
  chrome: ConversationChromeProps;
}) {
  const messages = useTalkyMessages();
  const client = usePipecatClient();
  const turn = useTurn();
  const { voiceState, connected } = chrome;
  // ?voiceState=speaking lets the bot-speaking bar be inspected in fixtures
  // without a live bot.
  const voiceStateOverride = useUrlParam('voiceState');
  const botSpeaking =
    voiceStateOverride === 'speaking' ||
    (!voiceStateOverride && connected && voiceState === 'speaking');
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const { hasOlder, loadOlder, loading, openId } = useSessions();

  // ── Follow the reader, never the stream ──
  // `following` is true while the reader is within FOLLOW_SLACK of the bottom.
  // While following, every change pins to the bottom with an instant jump.
  // Otherwise nothing moves the reader: not tokens, tool rows, or new messages.
  const following = useRef(true);
  const [awayFromBottom, setAwayFromBottom] = useState(false);
  const [unread, setUnread] = useState(0);
  const seen = useRef<{ len: number; key: string }>({ len: messages.length, key: '' });

  const pin = useCallback(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, []);

  // Content version of a message: identity + how much it holds. A reply
  // streams into the same message, so length alone can't tell it grew.
  const keyOfMessage = (m: TalkyMessage | undefined) =>
    m ? `${m.id}:${m.parts.length}:${m.parts.reduce((n, p) => n + (p.kind === 'text' ? p.spoken.length + p.unspoken.length : 1), 0)}` : '';

  const follow = useCallback(() => {
    following.current = true;
    seen.current = { len: messages.length, key: keyOfMessage(messages.at(-1)) };
    setUnread(0);
    setAwayFromBottom(false);
  }, [messages]);

  // A different session opened: land at its bottom.
  useLayoutEffect(() => {
    following.current = true;
    setUnread(0);
    setAwayFromBottom(false);
  }, [openId]);

  const prev = useRef<{ first?: string; last?: string; height: number; top: number }>({ height: 0, top: 0 });
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const first = messages[0]?.id;
    const lastMsg = messages[messages.length - 1];
    const last = lastMsg?.id;
    const p = prev.current;
    const prepended = p.last !== undefined && last === p.last && first !== p.first;
    // My own new message always pins.
    if (lastMsg && last !== p.last && lastMsg.role === 'user' && !prepended) following.current = true;
    if (prepended) {
      el.scrollTop = el.scrollHeight - p.height + p.top;
    } else if (following.current) {
      el.scrollTop = el.scrollHeight;
      seen.current = { len: messages.length, key: keyOfMessage(lastMsg) };
      setUnread(0);
      setAwayFromBottom(false);
    } else {
      // A reply streams into a message that existed when we scrolled up, so
      // "unread" is new messages OR the tail message that grew meanwhile.
      let n = Math.max(0, messages.length - seen.current.len);
      if (n === 0 && keyOfMessage(lastMsg) !== seen.current.key) n = 1;
      setUnread((u) => (u === n ? u : n));
    }
    prev.current = { first, last, height: el.scrollHeight, top: el.scrollTop };
  }, [messages, turn.notices]);

  // Content that grows inside a message (streamed text, highlighting, a tool
  // row) doesn't change `messages`; keep pinned through resizes too.
  useEffect(() => {
    const content = contentRef.current;
    if (!content) return;
    const ro = new ResizeObserver(() => {
      if (following.current) pin();
    });
    ro.observe(content);
    return () => ro.disconnect();
  }, [pin]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const distance = el.scrollHeight - el.clientHeight - el.scrollTop;
    const near = distance <= FOLLOW_SLACK;
    if (near && !following.current) follow();
    else if (!near && following.current) following.current = false;
    if (!near !== awayFromBottom) setAwayFromBottom(!near);
    prev.current = { ...prev.current, height: el.scrollHeight, top: el.scrollTop };
  };

  // Scrolling up with the wheel or trackpad releases the pin on that event,
  // before any scroll (or the next token) happens.
  const onWheel = (e: React.WheelEvent) => {
    if (e.deltaY < 0) following.current = false;
  };

  const jumpToLatest = () => {
    follow();
    pin();
  };

  const loadingOlder = useRef(false);
  const pageOlder = useCallback(async () => {
    if (loadingOlder.current || !hasOlder) return;
    const el = scrollRef.current;
    if (el) prev.current = { ...prev.current, height: el.scrollHeight, top: el.scrollTop };
    loadingOlder.current = true;
    try {
      await loadOlder();
    } finally {
      loadingOlder.current = false;
    }
  }, [hasOlder, loadOlder]);

  // Page older turns in when the top of the transcript scrolls into view.
  const sentinelRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el || !hasOlder) return;
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) void pageOlder();
    }, { root: scrollRef.current, rootMargin: '200px 0px 0px 0px' });
    io.observe(el);
    return () => io.disconnect();
  }, [hasOlder, pageOlder]);

  // Notices sit after the message they belong to, in the session they were
  // raised on (a notice from another convo stays hidden until it's back).
  const noticesAfter = useMemo(() => {
    const m = new Map<number, Notice[]>();
    for (const n of turn.notices) {
      // null convo = raised while no session was bound (e.g. the turn that
      // created this convo); show it here rather than losing it.
      if (n.convo !== null && n.convo !== openId) continue;
      const at = Math.min(n.after, messages.length);
      m.set(at, [...(m.get(at) ?? []), n]);
    }
    return m;
  }, [turn.notices, messages.length, openId]);

  const lastIndex = messages.length - 1;
  const showEmpty = !loading && messages.length === 0 && turn.notices.length === 0 && !turn.running;

  return (
    <div className="relative h-full flex flex-col min-h-0">
      <h2 id="transcript-heading" className="sr-only">Conversation</h2>
      <div
        ref={scrollRef}
        className="transcript relative flex-1 min-h-0 overflow-y-auto overflow-x-hidden px-5 pt-2"
        data-testid="transcript-list"
        role="log"
        aria-live="polite"
        aria-labelledby="transcript-heading"
        aria-busy={loading || undefined}
        onScroll={onScroll}
        onWheel={onWheel}
      >
        <div ref={contentRef}>
          {loading ? (
            <div className="flex items-center justify-center gap-2 py-16 text-[13px]" data-testid="transcript-loading" style={{ color: 'var(--color-text-mute)' }}>
              <Loader2Icon size={14} className="animate-spin" aria-hidden="true" />
              Loading conversation…
            </div>
          ) : showEmpty ? (
            <EmptyState onConnect={chrome.onConnect} />
          ) : (
            <>
              {hasOlder && (
                <button
                  ref={sentinelRef}
                  type="button"
                  data-testid="transcript-load-older"
                  className="block w-full py-2 text-center text-[12px] hover:underline"
                  style={{ color: 'var(--color-text-mute)' }}
                  onClick={() => void pageOlder()}
                >
                  Earlier messages…
                </button>
              )}
              {noticesAfter.get(0)?.map((n) => <NoticeRow key={`n${n.id}`} notice={n} />)}
              {messages.map((m, i) => (
                <Fragment key={m.id}>
                  <MessageRow message={m} live={turn.running && i === lastIndex} />
                  {noticesAfter.get(i + 1)?.map((n) => <NoticeRow key={`n${n.id}`} notice={n} />)}
                </Fragment>
              ))}
              <ThinkingIndicator />
            </>
          )}
        </div>
      </div>
      {awayFromBottom && !loading && (
        <button
          type="button"
          data-testid="jump-to-latest"
          data-unread={unread}
          aria-label={unread ? `Scroll to latest, ${unread} new` : 'Scroll to latest'}
          onClick={jumpToLatest}
          className="absolute left-1/2 -translate-x-1/2 z-10 inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-[12px]"
          style={{
            bottom: 'calc(var(--composer-height, 96px) + 8px)',
            border: '1px solid var(--color-border)',
            backgroundColor: 'var(--color-panel-2)',
            color: 'var(--color-foreground)',
          }}
        >
          <ArrowDownIcon size={13} aria-hidden="true" />
          Scroll to latest
          {unread > 0 && (
            <span className="rounded-full px-1.5 font-mono text-[10px]" style={{ backgroundColor: 'var(--color-panel-3)' }}>
              {unread}
            </span>
          )}
        </button>
      )}
      <ComposerArea activeProfile={activeProfile} chrome={chrome} botSpeaking={botSpeaking} onStop={() => client?.sendClientMessage('interrupt')} />
    </div>
  );
}

function ComposerArea({
  activeProfile,
  chrome,
  botSpeaking,
  onStop,
}: {
  activeProfile: string;
  chrome: ConversationChromeProps;
  botSpeaking: boolean;
  onStop: () => void;
}) {
  // The jump-to-latest button sits just above the composer, whatever its height.
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const set = () => el.parentElement?.style.setProperty('--composer-height', `${el.offsetHeight}px`);
    set();
    const ro = new ResizeObserver(set);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return (
    <div
      ref={ref}
      className="px-3 pt-2 pb-2 border-t"
      style={{
        borderColor: 'var(--color-border-soft)',
        backgroundColor: 'var(--color-card)',
      }}
    >
      {botSpeaking && (
        // Cancel in-flight TTS by sending an 'interrupt' app-message over the
        // existing WebRTC data channel; the daemon queues one InterruptionFrame.
        <BotSpeakingBar onStop={onStop} />
      )}
      <div className="flex items-start gap-2">
        <SteerModeChip activeProfile={activeProfile} />
        <TalkyTextInput />
        <ConverseButton
          voiceState={chrome.voiceState}
          connected={chrome.connected}
          connecting={chrome.connecting}
          error={chrome.error}
          onConnect={chrome.onConnect}
          onDisconnect={chrome.onDisconnect}
        />
      </div>
      <div className="flex justify-end pr-1 -mt-4 h-4 pointer-events-none">
        <TaskTimer />
      </div>
    </div>
  );
}

export const ConversationPanelWithReasoning = memo(
  ({
    activeProfile,
    ...chrome
  }: { activeProfile: string } & ConversationChromeProps) => {
    return <ConversationMessages activeProfile={activeProfile} chrome={chrome} />;
  },
);

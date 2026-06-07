import { useEffect, useMemo, useRef, useCallback, useState, memo, Fragment } from 'react';
import { usePipecatClient } from '@pipecat-ai/client-react';
import { ChevronRightIcon, CopyIcon, CheckIcon } from 'lucide-react';
import { Reasoning, ReasoningContent, ReasoningTrigger } from './ai-elements/reasoning';
import { TalkyTextInput } from './TalkyTextInput';
import { Streamdown } from 'streamdown';

import { useTalkyMessages } from '../messages/useTalkyMessages';
import type { TalkyMessage, TalkyPart } from '../messages/types';
import { SteerModeChip } from './SteerModeChip';
import { BotSpeakingBar } from './BotSpeakingBar';
import { ConverseButton } from './ConverseButton';
import { MuteButton } from './MuteButton';
import type { VoiceState } from './useVoiceState';
import { useUrlParam } from '../fixtures/harness';

export interface ConversationChromeProps {
  voiceState: VoiceState;
  connected: boolean;
  connecting: boolean;
  error: boolean;
  onConnect: () => void;
  onDisconnect: () => void;
}

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

type TextChunk = { kind: 'text'; spoken: string; unspoken: string; key: number };
type BlockChunk = { kind: 'block'; part: TalkyPart; key: number };
type RenderChunk = TextChunk | BlockChunk;

function buildChunks(parts: TalkyPart[]): RenderChunk[] {
  const out: RenderChunk[] = [];
  parts.forEach((part, i) => {
    if (part.kind === 'thinking') return;
    if (part.kind === 'text') {
      if (!part.spoken && !part.unspoken) return;
      out.push({ kind: 'text', spoken: part.spoken, unspoken: part.unspoken, key: i });
      return;
    }
    out.push({ kind: 'block', part, key: i });
  });
  return out;
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
    <span className="karaoke-part">
      {spoken && (
        <Streamdown
          className="karaoke-spoken"
          plugins={plugins}
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
          parseIncompleteMarkdown={isStreaming}
          isAnimating={false}
        >
          {unspoken}
        </Streamdown>
      )}
    </span>
  );
}

// ─── TOOL CARD ─────────────────────────────────────────────────────────
function ToolBlock({ kind, content }: { kind: 'tool_start' | 'tool_end'; content: string }) {
  const icon = kind === 'tool_start' ? '⟳' : '✓';
  const label = kind === 'tool_start' ? 'Running' : 'Done';
  return (
    <div className="flex items-center gap-2 py-1 text-xs font-mono text-muted-foreground">
      <span className="opacity-60">{icon}</span>
      <span className="opacity-50 uppercase tracking-wider">{label}</span>
      <span className="opacity-80 truncate">{content}</span>
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

function ErrorBlock({ content }: { content: string }) {
  return (
    <div className="text-xs font-mono py-0.5" style={{ color: 'var(--color-destructive)' }}>
      ✗ {content}
    </div>
  );
}

function renderBlock(part: TalkyPart): React.ReactNode {
  if (part.kind === 'tool_start' || part.kind === 'tool_end') {
    return <ToolBlock kind={part.kind} content={part.content} />;
  }
  if (part.kind === 'info') return <InfoBlock content={part.content} />;
  if (part.kind === 'error') return <ErrorBlock content={part.content} />;
  return null;
}

// ─── COPY BUTTON ───────────────────────────────────────────────────────
function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const handle = () => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };
  return (
    <button
      onClick={handle}
      title={copied ? 'Copied' : 'Copy'}
      className="inline-flex items-center justify-center size-6 rounded transition-colors"
      style={{ color: copied ? 'var(--color-success)' : 'var(--color-text-mute)' }}
    >
      {copied ? <CheckIcon size={13} /> : <CopyIcon size={13} />}
    </button>
  );
}

// ─── THINKING ──────────────────────────────────────────────────────────
function ThinkingBlock({ text, isStreaming }: { text: string; isStreaming: boolean }) {
  return (
    <Reasoning isStreaming={isStreaming} defaultOpen={isStreaming} className="mb-2">
      <ReasoningTrigger />
      <ReasoningContent>{text}</ReasoningContent>
    </Reasoning>
  );
}

// ─── MESSAGE ROW ───────────────────────────────────────────────────────
function MessageRow({ message }: { message: TalkyMessage }) {
  const [hovered, setHovered] = useState(false);
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

  const chunks = useMemo(() => buildChunks(message.parts), [message.parts]);
  const ts = formatTime(message.createdAt);
  const fullText = useMemo(() => messageText(message), [message]);

  return (
    <div
      className="py-3 relative"
      style={{ borderBottom: '1px solid var(--color-border-soft)' }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
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
          className="font-mono text-[11px] transition-opacity"
          style={{
            color: 'var(--color-text-mute)',
            opacity: hovered ? 0.5 : 0,
          }}
        >
          {ts}
        </span>
        {fullText && (
          <div
            className="transition-opacity"
            style={{ opacity: hovered ? 1 : 0, pointerEvents: hovered ? 'auto' : 'none' }}
          >
            <CopyButton text={fullText} />
          </div>
        )}
      </div>

      {thinkingText && !isUser && (
        <ThinkingBlock text={thinkingText} isStreaming={isStreaming} />
      )}

      <div className="text-[15px] leading-relaxed max-w-[65ch]">
        {chunks.map((c) =>
          c.kind === 'block' ? (
            <Fragment key={c.key}>{renderBlock(c.part)}</Fragment>
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
  const { voiceState, connected } = chrome;
  // ?voiceState=speaking lets the bot-speaking bar be inspected in fixtures
  // without a live bot.
  const voiceStateOverride = useUrlParam('voiceState');
  const botSpeaking =
    voiceStateOverride === 'speaking' ||
    (!voiceStateOverride && connected && voiceState === 'speaking');
  const scrollRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, scrollToBottom]);

  return (
    <div className="relative h-full flex flex-col">
      <div ref={scrollRef} className="relative flex-1 overflow-y-auto px-5 pt-2">
        {messages.map((m) => (
          <MessageRow key={m.id} message={m} />
        ))}
      </div>
      <div
        className="px-3 pt-2 pb-3 border-t"
        style={{
          borderColor: 'var(--color-border-soft)',
          backgroundColor: 'var(--color-card)',
        }}
      >
        {botSpeaking && (
          <BotSpeakingBar
            onStop={() => {
              // Cancel in-flight TTS by sending an 'interrupt' app-message over
              // the existing WebRTC data channel. The daemon's on_app_message
              // handler queues one InterruptionFrame — the same frame a VAD
              // speech-onset produces. Rides the audio connection (no separate
              // HTTP round-trip); the bar clears when botStoppedSpeaking arrives.
              client?.sendClientMessage('interrupt');
            }}
          />
        )}
        <div className="flex items-center gap-2">
          <SteerModeChip activeProfile={activeProfile} />
          {/* Mic (input) mute lives with the composer; speaker (output) mute
              lives in the header. Only meaningful once connected. */}
          {connected && <MuteButton />}
          <TalkyTextInput connected={connected} />
          <ConverseButton
            voiceState={voiceState}
            connected={chrome.connected}
            connecting={chrome.connecting}
            error={chrome.error}
            onConnect={chrome.onConnect}
            onDisconnect={chrome.onDisconnect}
          />
        </div>
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

// Suppress unused-imports — kept for next phase (collapsible tool detail).
void ChevronRightIcon;

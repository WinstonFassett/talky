import { useEffect, useMemo, useRef, useCallback, useState, memo, Fragment } from 'react';
import { usePipecatClientTransportState } from '@pipecat-ai/client-react';
import { ChevronRightIcon, CopyIcon, CheckIcon } from 'lucide-react';
import { Reasoning, ReasoningContent, ReasoningTrigger } from './ai-elements/reasoning';
import { TalkyTextInput } from './TalkyTextInput';
import { cjk } from '@streamdown/cjk';
import { code } from '@streamdown/code';
import { math } from '@streamdown/math';
import { mermaid } from '@streamdown/mermaid';
import { Streamdown } from 'streamdown';

import { useTalkyMessages } from '../messages/useTalkyMessages';
import type { TalkyMessage, TalkyPart } from '../messages/types';
import { SteerModeChip } from './SteerModeChip';

const streamdownPlugins = { cjk, code, math, mermaid };

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
  isLast,
}: {
  spoken: string;
  unspoken: string;
  isStreaming: boolean;
  isLast: boolean;
}) {
  return (
    <span className="karaoke-part">
      {spoken && (
        <Streamdown
          className="karaoke-spoken"
          plugins={streamdownPlugins}
          parseIncompleteMarkdown={isStreaming}
          isAnimating={isStreaming && !unspoken}
          caret={isStreaming && !unspoken && isLast ? 'block' : undefined}
        >
          {spoken}
        </Streamdown>
      )}
      {unspoken && (
        <Streamdown
          className="karaoke-unspoken text-muted-foreground"
          plugins={streamdownPlugins}
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
        {chunks.map((c, i) =>
          c.kind === 'block' ? (
            <Fragment key={c.key}>{renderBlock(c.part)}</Fragment>
          ) : (
            <KaraokePart
              key={c.key}
              spoken={c.spoken}
              unspoken={c.unspoken}
              isStreaming={isStreaming}
              isLast={i === chunks.length - 1}
            />
          ),
        )}
      </div>
    </div>
  );
}

// ─── TRANSCRIPT ────────────────────────────────────────────────────────
function ConversationMessages({ activeProfile }: { activeProfile: string }) {
  const messages = useTalkyMessages();
  const transportState = usePipecatClientTransportState();
  const connected = transportState === 'connected' || transportState === 'ready';
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
        className="p-3 border-t flex items-center gap-2"
        style={{
          borderColor: 'var(--color-border-soft)',
          backgroundColor: 'var(--color-card)',
        }}
      >
        <SteerModeChip activeProfile={activeProfile} />
        <TalkyTextInput connected={connected} />
      </div>
    </div>
  );
}

export const ConversationPanelWithReasoning = memo(
  ({ activeProfile }: { activeProfile: string }) => {
    return <ConversationMessages activeProfile={activeProfile} />;
  },
);

// Suppress unused-imports — kept for next phase (collapsible tool detail).
void ChevronRightIcon;

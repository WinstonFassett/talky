import { useCallback, useEffect, useRef } from 'react';
import type { ConversationMessage } from '@pipecat-ai/voice-ui-kit';
import {
  MessageContainer,
  usePipecatConversation,
} from '@pipecat-ai/voice-ui-kit';
import { usePipecatClientTransportState } from '@pipecat-ai/client-react';
import { useConversationContext } from '@pipecat-ai/voice-ui-kit';
import { Reasoning } from './Reasoning';

const AGGREGATION_METADATA = {
  tool_start: { isSpoken: false, displayMode: 'block' as const },
  tool_end:   { isSpoken: false, displayMode: 'block' as const },
  thinking:   { isSpoken: false, displayMode: 'block' as const },
  error:      { isSpoken: false, displayMode: 'block' as const },
  info:       { isSpoken: false, displayMode: 'block' as const },
};

function splitEventContent(content: string): { summary: string; payload: unknown | null } {
  const i = content.indexOf('\x00');
  if (i < 0) return { summary: content, payload: null };
  const rest = content.slice(i + 1);
  try {
    return { summary: content.slice(0, i), payload: JSON.parse(rest) };
  } catch {
    return { summary: content.slice(0, i), payload: rest };
  }
}

const BOT_OUTPUT_RENDERERS = {
  thinking: (content: string) => (
    <Reasoning text={content} isStreaming={false} />
  ),
  tool_start: (content: string) => {
    const { summary } = splitEventContent(content);
    return (
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground font-mono py-0.5">
        <span className="opacity-40">⟳</span>
        <span className="opacity-70">{summary}</span>
      </div>
    );
  },
  tool_end: (content: string) => {
    const { summary, payload } = splitEventContent(content);
    const p = typeof payload === 'object' && payload !== null ? payload as Record<string, unknown> : {};
    const isError = !!p.is_error;
    const lines = typeof p.result_lines === 'number' ? p.result_lines : null;
    return (
      <div className={`flex items-center gap-1.5 text-xs font-mono py-0.5 ${isError ? 'text-destructive' : 'text-muted-foreground'}`}>
        <span>{isError ? '✗' : '✓'}</span>
        <span className="opacity-70">{summary}{lines != null ? ` (${lines} lines)` : ''}</span>
      </div>
    );
  },
  error: (content: string) => {
    const { summary } = splitEventContent(content);
    return (
      <div className="text-xs font-mono text-destructive py-0.5">✗ {summary}</div>
    );
  },
  info: (content: string) => {
    const { summary } = splitEventContent(content);
    return (
      <div className="text-xs text-muted-foreground opacity-50 py-0.5">{summary}</div>
    );
  },
};

const MESSAGE_CLASS_NAMES = {
  container: 'py-3 border-b border-border/50 last:border-0',
};

function MessageList({ messages }: { messages: ConversationMessage[] }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const isAtBottom = useRef(true);

  const maybeScrollToBottom = useCallback(() => {
    if (!scrollRef.current || !isAtBottom.current) return;
    scrollRef.current.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, []);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const handleScroll = () => {
      isAtBottom.current = Math.ceil(el.scrollHeight - el.scrollTop) <= Math.ceil(el.clientHeight) + 4;
    };
    el.addEventListener('scroll', handleScroll, { passive: true });
    return () => el.removeEventListener('scroll', handleScroll);
  }, []);

  useEffect(() => {
    maybeScrollToBottom();
  }, [messages, maybeScrollToBottom]);

  return (
    <div ref={scrollRef} className="flex-1 overflow-y-auto">
      <div className="px-4 py-2">
        {messages.map((message, index) => (
          <MessageContainer
            key={`${message.createdAt}-${index}`}
            message={message}
            assistantLabel="Assistant"
            clientLabel="You"
            classNames={MESSAGE_CLASS_NAMES}
            botOutputRenderers={BOT_OUTPUT_RENDERERS}
            aggregationMetadata={AGGREGATION_METADATA}
          />
        ))}
      </div>
    </div>
  );
}

export function TranscriptPanel() {
  const transportState = usePipecatClientTransportState();
  const { messages } = usePipecatConversation({ aggregationMetadata: AGGREGATION_METADATA });
  const { botOutputSupported } = useConversationContext();

  const isConnecting = transportState === 'authenticating' || transportState === 'connecting';
  const isConnected = transportState === 'connected' || transportState === 'ready';

  if (messages.length > 0) {
    return <MessageList messages={messages} />;
  }

  const placeholder = isConnecting
    ? 'Connecting…'
    : !isConnected
      ? 'Not connected'
      : botOutputSupported === false
        ? 'BotOutput events not supported (requires RTVI 1.1.0+)'
        : 'Waiting for messages…';

  return (
    <div className="flex-1 flex items-center justify-center">
      <p className="text-sm text-muted-foreground">{placeholder}</p>
    </div>
  );
}

import { useEffect, useRef, type ReactNode } from 'react';
import type { ConversationMessage, ConversationMessagePart } from '@pipecat-ai/voice-ui-kit';
import { usePipecatConversation } from '@pipecat-ai/voice-ui-kit';
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

function partText(part: ConversationMessagePart): string {
  if (typeof part.text === 'string') return part.text;
  const t = part.text as { spoken?: string; unspoken?: string } | null;
  return t?.unspoken || t?.spoken || '';
}

type RenderNode =
  | { kind: 'user'; text: string; key: string }
  | { kind: 'thinking'; text: string; streaming: boolean; key: string }
  | { kind: 'bot-text'; text: string; key: string }
  | { kind: 'tool_start'; summary: string; key: string }
  | { kind: 'tool_end'; summary: string; isError: boolean; lines: number | null; key: string }
  | { kind: 'error'; summary: string; payload: unknown; key: string }
  | { kind: 'info'; summary: string; key: string };

function buildNodes(messages: ConversationMessage[]): RenderNode[] {
  const nodes: RenderNode[] = [];
  let thinkingChunks: string[] = [];
  let thinkingStreaming = false;
  let thinkingKey = '';
  const flushThinking = () => {
    if (thinkingChunks.length === 0) return;
    nodes.push({ kind: 'thinking', text: thinkingChunks.join(''), streaming: thinkingStreaming, key: thinkingKey });
    thinkingChunks = [];
    thinkingStreaming = false;
    thinkingKey = '';
  };

  messages.forEach((msg, mi) => {
    if (msg.role === 'user') {
      flushThinking();
      const text = msg.parts.map(partText).join('').trim();
      if (text) nodes.push({ kind: 'user', text, key: `user-${mi}` });
      return;
    }

    msg.parts.forEach((part, pi) => {
      const text = partText(part);
      const key = `${mi}-${pi}`;

      if (part.aggregatedBy === 'thinking') {
        if (thinkingChunks.length === 0) thinkingKey = `thinking-${mi}-${pi}`;
        thinkingChunks.push(text);
        // still streaming if this part isn't final
        thinkingStreaming = !part.final;
        return;
      }

      // Non-thinking part — flush any accumulated thinking block as done
      if (thinkingChunks.length > 0) {
        thinkingStreaming = false;
        flushThinking();
      }

      const isSpoken = part.aggregatedBy === 'sentence' || part.aggregatedBy === 'word' || !part.aggregatedBy;
      if (isSpoken) {
        if (text) {
          const prev = nodes[nodes.length - 1];
          if (prev?.kind === 'bot-text') {
            nodes[nodes.length - 1] = { ...prev, text: prev.text + ' ' + text };
          } else {
            nodes.push({ kind: 'bot-text', text, key: `bottext-${mi}-${pi}` });
          }
        }
        return;
      }

      if (part.aggregatedBy === 'tool_start') {
        const { summary } = splitEventContent(text);
        nodes.push({ kind: 'tool_start', summary, key });
      } else if (part.aggregatedBy === 'tool_end') {
        const { summary, payload } = splitEventContent(text);
        const p = typeof payload === 'object' && payload !== null ? payload as Record<string, unknown> : {};
        nodes.push({ kind: 'tool_end', summary, isError: !!p.is_error, lines: typeof p.result_lines === 'number' ? p.result_lines : null, key });
      } else if (part.aggregatedBy === 'error') {
        const { summary, payload } = splitEventContent(text);
        nodes.push({ kind: 'error', summary, payload, key });
      } else if (part.aggregatedBy === 'info') {
        const { summary } = splitEventContent(text);
        nodes.push({ kind: 'info', summary, key });
      }
    });
  });

  flushThinking();
  return nodes;
}

function renderNode(node: RenderNode): ReactNode {
  switch (node.kind) {
    case 'user':
      return (
        <div key={node.key} className="flex justify-end mb-2">
          <div className="bg-muted rounded-lg px-3 py-2 text-sm max-w-[80%]">{node.text}</div>
        </div>
      );
    case 'thinking':
      return <Reasoning key={node.key} text={node.text} isStreaming={node.streaming} />;
    case 'bot-text':
      return (
        <div key={node.key} className="text-sm mb-2">{node.text}</div>
      );
    case 'tool_start':
      return (
        <div key={node.key} className="flex items-center gap-1.5 text-xs text-muted-foreground font-mono py-0.5">
          <span className="opacity-50">⟳</span>
          <span>{node.summary}</span>
        </div>
      );
    case 'tool_end':
      return (
        <div key={node.key} className={`flex items-center gap-1.5 text-xs font-mono py-0.5 ${node.isError ? 'text-destructive' : 'text-muted-foreground'}`}>
          <span>{node.isError ? '✗' : '✓'}</span>
          <span>{node.summary}{node.lines != null ? ` (${node.lines} lines)` : ''}</span>
        </div>
      );
    case 'error':
      return (
        <div key={node.key} className="text-xs font-mono text-destructive py-0.5" title={node.payload ? JSON.stringify(node.payload) : undefined}>
          ✗ {node.summary}
        </div>
      );
    case 'info':
      return (
        <div key={node.key} className="text-xs text-muted-foreground opacity-50 py-0.5">
          {node.summary}
        </div>
      );
  }
}

export function TranscriptPanel() {
  const { messages } = usePipecatConversation({ aggregationMetadata: AGGREGATION_METADATA });
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const nodes = buildNodes(messages);

  return (
    <div className="flex-1 overflow-y-auto py-2 px-2">
      {nodes.map(node => renderNode(node))}
      <div ref={bottomRef} />
    </div>
  );
}

import { useEffect, useMemo, useRef, useCallback, memo, Fragment } from 'react';
import {
  Panel, PanelContent, PanelHeader,
  Tabs, TabsContent, TabsList, TabsTrigger,
  MessageRole,
  TextInput,
} from '@pipecat-ai/voice-ui-kit';
import { MessagesSquareIcon } from 'lucide-react';
import { Reasoning, ReasoningContent, ReasoningTrigger } from './ai-elements/reasoning';
import { cjk } from '@streamdown/cjk';
import { code } from '@streamdown/code';
import { math } from '@streamdown/math';
import { mermaid } from '@streamdown/mermaid';
import { Streamdown } from 'streamdown';

import { useTalkyMessages } from '../messages/useTalkyMessages';
import type { TalkyMessage, TalkyPart } from '../messages/types';

const streamdownPlugins = { cjk, code, math, mermaid };

type TextChunk = { kind: 'text'; spoken: string; unspoken: string; key: number };
type BlockChunk = { kind: 'block'; part: TalkyPart; key: number };
type RenderChunk = TextChunk | BlockChunk;

const BLOCK_RENDERERS: Record<string, (content: string) => React.JSX.Element> = {
  tool_start: (content) => (
    <div className="flex items-center gap-1.5 text-xs text-muted-foreground font-mono py-0.5">
      <span className="opacity-40">⟳</span>
      <span className="opacity-70">{content}</span>
    </div>
  ),
  tool_end: (content) => (
    <div className="flex items-center gap-1.5 text-xs text-muted-foreground font-mono py-0.5">
      <span>✓</span>
      <span className="opacity-70">{content}</span>
    </div>
  ),
  error: (content) => (
    <div className="text-xs font-mono text-destructive py-0.5">✗ {content}</div>
  ),
  info: (content) => (
    <div className="text-xs text-muted-foreground opacity-50 py-0.5">{content}</div>
  ),
};

function buildChunks(parts: TalkyPart[]): RenderChunk[] {
  const chunks: RenderChunk[] = [];
  parts.forEach((part, i) => {
    if (part.kind === 'thinking') return;
    if (part.kind === 'text') {
      if (!part.spoken && !part.unspoken) return;
      chunks.push({ kind: 'text', spoken: part.spoken, unspoken: part.unspoken, key: i });
      return;
    }
    chunks.push({ kind: 'block', part, key: i });
  });
  return chunks;
}

function blockContent(part: TalkyPart): string {
  if (part.kind === 'text' || part.kind === 'thinking') return '';
  return part.content;
}

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

function AssistantMessage({ message }: { message: TalkyMessage }) {
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

  return (
    <div className="mb-4">
      <MessageRole role="assistant" className="mb-1" />
      {thinkingText && (
        <Reasoning isStreaming={isStreaming} className="mb-2">
          <ReasoningTrigger />
          <ReasoningContent>{thinkingText}</ReasoningContent>
        </Reasoning>
      )}
      {chunks.length > 0 && (
        <div className="text-sm">
          {chunks.map((c, i) =>
            c.kind === 'block' ? (
              <Fragment key={c.key}>
                {(BLOCK_RENDERERS[c.part.kind] ?? ((s: string) => <span>{s}</span>))(
                  blockContent(c.part),
                )}
              </Fragment>
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
      )}
    </div>
  );
}

function UserMessage({ message }: { message: TalkyMessage }) {
  const text = useMemo(
    () =>
      message.parts
        .filter((p): p is Extract<TalkyPart, { kind: 'text' }> => p.kind === 'text')
        .map((p) => p.spoken + p.unspoken)
        .join(' '),
    [message.parts],
  );

  return (
    <div className="mb-4 flex flex-col items-end">
      <MessageRole role="user" className="mb-1" />
      <div className="text-sm text-right">{text}</div>
    </div>
  );
}

function ConversationMessages() {
  const messages = useTalkyMessages();
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
      <div ref={scrollRef} className="relative flex-1 overflow-y-auto p-4 pb-2">
        {messages.map((message) => {
          if (message.role === 'assistant') {
            return <AssistantMessage key={message.id} message={message} />;
          }
          if (message.role === 'user') {
            return <UserMessage key={message.id} message={message} />;
          }
          return null;
        })}
      </div>
      <div className="p-3 border-t">
        <TextInput classNames={{ container: 'items-center' }} />
      </div>
    </div>
  );
}

export const ConversationPanelWithReasoning = memo(() => {
  return (
    <Tabs className="h-full" defaultValue="conversation">
      <Panel className="h-full max-sm:border-none">
        <PanelHeader variant="noPadding" className="p-1.5 relative">
          <TabsList>
            <TabsTrigger value="conversation">
              <MessagesSquareIcon size={20} />
              Conversation
            </TabsTrigger>
          </TabsList>
        </PanelHeader>
        <PanelContent className="p-0! overflow-hidden h-full">
          <TabsContent value="conversation" className="overflow-hidden h-full">
            <ConversationMessages />
          </TabsContent>
        </PanelContent>
      </Panel>
    </Tabs>
  );
});

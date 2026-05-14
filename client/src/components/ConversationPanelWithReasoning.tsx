import { useEffect, useMemo, useRef, useCallback, memo, Fragment } from 'react';
import type { ConversationMessage, BotOutputText, ConversationMessagePart, AggregationMetadata } from '@pipecat-ai/voice-ui-kit';
import {
  Panel, PanelContent, PanelHeader,
  Tabs, TabsContent, TabsList, TabsTrigger,
  MessageRole,
  TextInput,
  usePipecatConversation,
} from '@pipecat-ai/voice-ui-kit';
import { MessagesSquareIcon } from 'lucide-react';
import { Reasoning, ReasoningContent, ReasoningTrigger } from './ai-elements/reasoning';

const AGGREGATION_METADATA: Record<string, AggregationMetadata> = {
  thinking: { isSpoken: false, displayMode: 'block' as const },
  tool_start: { isSpoken: false, displayMode: 'block' as const },
  tool_end: { isSpoken: false, displayMode: 'block' as const },
  error: { isSpoken: false, displayMode: 'block' as const },
  info: { isSpoken: false, displayMode: 'block' as const },
  permission_request: { isSpoken: false, displayMode: 'block' as const },
};

function splitEventContent(content: string): string {
  const i = content.indexOf('\x00');
  return i < 0 ? content : content.slice(0, i);
}

function isBotOutputText(val: unknown): val is BotOutputText {
  return typeof val === 'object' && val !== null && 'spoken' in val && 'unspoken' in val;
}

function partText(part: ConversationMessagePart): string {
  if (isBotOutputText(part.text)) return part.text.spoken + part.text.unspoken;
  return typeof part.text === 'string' ? part.text : '';
}

function splitPayload(part: ConversationMessagePart): { spoken: string; unspoken: string } {
  if (isBotOutputText(part.text)) return { spoken: part.text.spoken, unspoken: part.text.unspoken };
  const text = typeof part.text === 'string' ? part.text : '';
  return { spoken: text, unspoken: '' };
}

const BLOCK_RENDERERS: Record<string, (content: string) => React.JSX.Element> = {
  tool_start: (content) => (
    <div className="flex items-center gap-1.5 text-xs text-muted-foreground font-mono py-0.5">
      <span className="opacity-40">⟳</span>
      <span className="opacity-70">{splitEventContent(content)}</span>
    </div>
  ),
  tool_end: (content) => (
    <div className="flex items-center gap-1.5 text-xs text-muted-foreground font-mono py-0.5">
      <span>✓</span>
      <span className="opacity-70">{splitEventContent(content)}</span>
    </div>
  ),
  error: (content) => (
    <div className="text-xs font-mono text-destructive py-0.5">✗ {splitEventContent(content)}</div>
  ),
  info: (content) => (
    <div className="text-xs text-muted-foreground opacity-50 py-0.5">{splitEventContent(content)}</div>
  ),
  permission_request: (content) => (
    <div className="flex items-center gap-1.5 text-xs text-amber-500 font-mono py-0.5">
      <span>⚠</span>
      <span>{splitEventContent(content)}</span>
    </div>
  ),
};

function renderPart(part: ConversationMessagePart, idx: number): React.ReactNode {
  const key = idx;
  if (part.aggregatedBy === 'thinking') return null;

  const agg = part.aggregatedBy;
  if (agg && BLOCK_RENDERERS[agg]) {
    const text = partText(part);
    return <Fragment key={key}>{BLOCK_RENDERERS[agg](text)}</Fragment>;
  }

  const { spoken, unspoken } = splitPayload(part);
  return (
    <span key={key}>
      {spoken}
      {unspoken && <span className="text-muted-foreground">{unspoken}</span>}
    </span>
  );
}

function AssistantMessage({ message }: { message: ConversationMessage }) {
  const isStreaming = !message.final;

  const thinkingText = useMemo(
    () =>
      message.parts
        .filter(p => p.aggregatedBy === 'thinking')
        .map(p => partText(p))
        .join(''),
    [message.parts],
  );

  const visibleParts = useMemo(
    () => message.parts.filter(p => p.aggregatedBy !== 'thinking'),
    [message.parts],
  );

  return (
    <div className="mb-4">
      <MessageRole role="assistant" className="mb-1" />
      {thinkingText && (
        <Reasoning isStreaming={isStreaming} className="mb-2">
          <ReasoningTrigger />
          <ReasoningContent>{thinkingText}</ReasoningContent>
        </Reasoning>
      )}
      {visibleParts.length > 0 && (
        <div className="text-sm">
          {visibleParts.map((p, i) => renderPart(p, i))}
        </div>
      )}
    </div>
  );
}

function UserMessage({ message }: { message: ConversationMessage }) {
  const text = useMemo(
    () => message.parts.map(p => partText(p)).join(''),
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
  const { messages: allMessages } = usePipecatConversation({ aggregationMetadata: AGGREGATION_METADATA });
  const scrollRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [allMessages, scrollToBottom]);

  return (
    <div className="relative h-full flex flex-col">
      <div ref={scrollRef} className="relative flex-1 overflow-y-auto p-4 pb-2">
        {allMessages.map((message, index) => {
          if (message.role === 'assistant') {
            return <AssistantMessage key={`${message.createdAt}-${index}`} message={message} />;
          }
          return <UserMessage key={`${message.createdAt}-${index}`} message={message} />;
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

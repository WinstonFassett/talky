import { useEffect, useMemo, useRef, useCallback, memo } from 'react';
import type { ConversationMessage, BotOutputText } from '@pipecat-ai/voice-ui-kit';
import {
  Panel, PanelContent, PanelHeader,
  Tabs, TabsContent, TabsList, TabsTrigger,
  MessageContainer,
  TextInput,
  usePipecatConversation,
} from '@pipecat-ai/voice-ui-kit';
import { MessagesSquareIcon } from 'lucide-react';
import { Reasoning, ReasoningContent, ReasoningTrigger } from './ai-elements/reasoning';

const AGGREGATION_METADATA = {
  thinking: { spoken: false, displayMode: 'block' as const },
  tool_start: { spoken: false, displayMode: 'block' as const },
  tool_end: { spoken: false, displayMode: 'block' as const },
  error: { spoken: false, displayMode: 'block' as const },
  info: { spoken: false, displayMode: 'block' as const },
};

function splitEventContent(content: string): string {
  const i = content.indexOf('\x00');
  return i < 0 ? content : content.slice(0, i);
}

function isBotOutputText(val: unknown): val is BotOutputText {
  return typeof val === 'object' && val !== null && 'spoken' in val && 'unspoken' in val;
}

function getThinkingText(message: ConversationMessage): string {
  return message.parts
    .filter(p => p.aggregatedBy === 'thinking')
    .map(p => {
      if (isBotOutputText(p.text)) return (p.text as BotOutputText).spoken + (p.text as BotOutputText).unspoken;
      return typeof p.text === 'string' ? p.text : '';
    })
    .join('');
}

function hasOnlyThinking(message: ConversationMessage): boolean {
  return message.parts.every(p => p.aggregatedBy === 'thinking');
}

const BOT_OUTPUT_RENDERERS_NO_THINKING: Record<string, (content: string) => React.JSX.Element> = {
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
};

// Strip thinking parts out of a message before passing to MessageContainer
function stripThinkingParts(message: ConversationMessage): ConversationMessage {
  return {
    ...message,
    parts: message.parts.filter(p => p.aggregatedBy !== 'thinking'),
  };
}

function MessageWithReasoning({ message }: { message: ConversationMessage }) {
  const thinkingText = getThinkingText(message);
  const strippedMessage = useMemo(() => stripThinkingParts(message), [message]);
  const onlyThinking = hasOnlyThinking(message);
  const isStreaming = !message.final;

  return (
    <div>
      {thinkingText && (
        <Reasoning isStreaming={isStreaming} className="mb-1">
          <ReasoningTrigger />
          <ReasoningContent>{thinkingText}</ReasoningContent>
        </Reasoning>
      )}
      {!onlyThinking && (
        <MessageContainer
          message={strippedMessage}
          botOutputRenderers={BOT_OUTPUT_RENDERERS_NO_THINKING}
          aggregationMetadata={AGGREGATION_METADATA}
        />
      )}
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
            return (
              <MessageWithReasoning
                key={`${message.createdAt}-${index}`}
                message={message}
              />
            );
          }
          return (
            <MessageContainer
              key={`${message.createdAt}-${index}`}
              message={message}
              botOutputRenderers={BOT_OUTPUT_RENDERERS_NO_THINKING}
              aggregationMetadata={AGGREGATION_METADATA}
            />
          );
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

import { useEffect, useMemo, useRef, useCallback, memo, Fragment } from 'react';
import type {
  ConversationMessage,
  BotOutputText,
  ConversationMessagePart,
  AggregationMetadata,
} from '@pipecat-ai/voice-ui-kit';
import {
  Panel, PanelContent, PanelHeader,
  Tabs, TabsContent, TabsList, TabsTrigger,
  MessageRole,
  TextInput,
  usePipecatConversation,
} from '@pipecat-ai/voice-ui-kit';
import { MessagesSquareIcon } from 'lucide-react';
import { Reasoning, ReasoningContent, ReasoningTrigger } from './ai-elements/reasoning';
import { cjk } from '@streamdown/cjk';
import { code } from '@streamdown/code';
import { math } from '@streamdown/math';
import { mermaid } from '@streamdown/mermaid';
import { Streamdown } from 'streamdown';

const streamdownPlugins = { cjk, code, math, mermaid };

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

type RenderChunk =
  | { kind: 'block'; agg: string; content: string; key: number }
  | { kind: 'text'; spoken: string; unspoken: string; key: number };

function buildChunks(parts: ConversationMessagePart[]): RenderChunk[] {
  const chunks: RenderChunk[] = [];
  parts.forEach((part, i) => {
    const agg = part.aggregatedBy;
    if (agg === 'thinking') return;
    if (agg && BLOCK_RENDERERS[agg]) {
      chunks.push({ kind: 'block', agg, content: partText(part), key: i });
      return;
    }
    const { spoken, unspoken } = splitPayload(part);
    if (!spoken && !unspoken) return;
    chunks.push({ kind: 'text', spoken, unspoken, key: i });
  });
  return chunks;
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

function RawKaraokeBaseline({ parts }: { parts: ConversationMessagePart[] }) {
  return (
    <span>
      {parts.map((p, i) => {
        if (p.aggregatedBy === 'thinking') return null;
        if (p.aggregatedBy && BLOCK_RENDERERS[p.aggregatedBy]) return null;
        const { spoken, unspoken } = splitPayload(p);
        return (
          <Fragment key={i}>
            {spoken}
            {unspoken && <span className="text-muted-foreground">{unspoken}</span>}
          </Fragment>
        );
      })}
    </span>
  );
}

function AssistantMessage({ message }: { message: ConversationMessage }) {
  const isStreaming = !message.final;

  const thinkingText = useMemo(
    () =>
      message.parts
        .filter((p) => p.aggregatedBy === 'thinking')
        .map((p) => partText(p))
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
        <>
          <div className="text-xs text-muted-foreground opacity-60 mb-1 font-mono">[raw baseline]</div>
          <div className="text-sm mb-3">
            <RawKaraokeBaseline parts={message.parts} />
          </div>
          <div className="text-xs text-muted-foreground opacity-60 mb-1 font-mono">[markdown + karaoke]</div>
          <div className="text-sm">
            {chunks.map((c, i) =>
              c.kind === 'block' ? (
                <Fragment key={c.key}>{BLOCK_RENDERERS[c.agg](c.content)}</Fragment>
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
        </>
      )}
    </div>
  );
}

function UserMessage({ message }: { message: ConversationMessage }) {
  const text = useMemo(
    () => message.parts.map((p) => partText(p)).join(' '),
    [message.parts],
  );

  return (
    <div className="mb-4 flex flex-col items-end">
      <MessageRole role="user" className="mb-1" />
      <div className="text-sm text-right">{text}</div>
    </div>
  );
}

function buildFixtureMessages(name: string): ConversationMessage[] {
  const now = new Date().toISOString();
  const m = (text: string): ConversationMessage => ({
    role: 'assistant',
    final: true,
    createdAt: now,
    parts: [{ text: { spoken: text, unspoken: '' }, final: true, createdAt: now }],
  });
  const u = (text: string): ConversationMessage => ({
    role: 'user',
    final: true,
    createdAt: now,
    parts: [{ text, final: true, createdAt: now }],
  });
  const sets: Record<string, ConversationMessage[]> = {
    haiku: [
      u('give me a haiku in md with a title and each line is a bullet'),
      m('# Morning Light\n\n- Cherry blossoms fall\n- Dew glistens on silent grass\n- New day awakens'),
    ],
    'haiku-no-nl': [
      u('give me a haiku in md with a title and each line is a bullet (no newlines)'),
      m('# Morning Light - Cherry blossoms fall - Dew glistens on silent grass - New day awakens'),
    ],
    prose: [
      u('tell me a joke'),
      m("Why don't scientists trust atoms?\n\nBecause they make up everything!"),
    ],
    'prose-no-nl': [
      u('tell me a joke'),
      m("Why don't scientists trust atoms? Because they make up everything!"),
    ],
    code: [
      u('show a python function'),
      m('Here is a tiny example:\n\n```python\ndef hello(name):\n    return f"Hello, {name}!"\n```\n\nThat\'s it.'),
    ],
    karaoke: [
      u('tell me a long story so I can see the cursor mid-stream'),
      {
        role: 'assistant',
        final: false,
        createdAt: now,
        parts: [{
          text: {
            spoken:
              'Once upon a time, in a small village at the edge of an ancient forest, ' +
              'there lived a humble baker who loved to wake before dawn. ',
            unspoken:
              'Every morning she kneaded her dough by candlelight, listening to the soft ' +
              'creaking of the timbers and the distant call of an owl returning home. ' +
              'On this particular morning, however, something was different — a faint ' +
              'silver light spilled under her door, and a hush had fallen over the whole street.',
          },
          final: false,
          createdAt: now,
        }],
      },
    ],
  };
  return sets[name] ?? sets.haiku;
}

function ConversationMessages() {
  const live = usePipecatConversation({ aggregationMetadata: AGGREGATION_METADATA });
  const fixtureName = useMemo(() => {
    if (typeof window === 'undefined') return null;
    return new URLSearchParams(window.location.search).get('fixture');
  }, []);
  const fixtureMessages = useMemo(
    () => (fixtureName ? buildFixtureMessages(fixtureName) : null),
    [fixtureName],
  );
  const allMessages = fixtureMessages ?? live.messages;
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

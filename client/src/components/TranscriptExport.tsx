import {
  Button,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  usePipecatConversation,
  type ConversationMessage,
} from '@pipecat-ai/voice-ui-kit';

/** Extract plain text from a message part's text field. */
const partText = (text: unknown): string => {
  if (typeof text === 'string') return text;
  if (text && typeof text === 'object' && 'spoken' in text) return (text as { spoken: string }).spoken;
  return String(text ?? '');
};

const toMarkdown = (messages: ConversationMessage[]): string => {
  const lines: string[] = [`# Transcript — ${new Date().toLocaleString()}`, ''];
  for (const msg of messages) {
    const role = msg.role === 'user' ? '**User**' : msg.role === 'assistant' ? '**Bot**' : `**${msg.role}**`;
    const text = msg.parts.map((p) => partText(p.text)).join(' ').trim();
    if (!text) continue;
    lines.push(`${role} (${new Date(msg.createdAt).toLocaleTimeString()})`);
    lines.push(text);
    lines.push('');
  }
  return lines.join('\n');
};

const toJsonl = (messages: ConversationMessage[]): string =>
  messages
    .map((msg) => JSON.stringify({
      role: msg.role,
      text: msg.parts.map((p) => partText(p.text)).join(' ').trim(),
      createdAt: msg.createdAt,
    }))
    .join('\n');

const toCsv = (messages: ConversationMessage[]): string => {
  const escape = (s: string) => `"${s.replace(/"/g, '""')}"`;
  const rows = [['role', 'text', 'timestamp'].join(',')];
  for (const msg of messages) {
    const text = msg.parts.map((p) => partText(p.text)).join(' ').trim();
    if (!text) continue;
    rows.push([escape(msg.role), escape(text), escape(msg.createdAt)].join(','));
  }
  return rows.join('\n');
};

const download = (content: string, filename: string, mime: string) => {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
};

type Format = 'md' | 'jsonl' | 'csv';

export const TranscriptExport = () => {
  const { messages } = usePipecatConversation();

  const handleExport = (fmt: Format) => {
    const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
    const fname = `transcript-${ts}`;

    switch (fmt) {
      case 'md':
        download(toMarkdown(messages), `${fname}.md`, 'text/markdown');
        break;
      case 'jsonl':
        download(toJsonl(messages), `${fname}.jsonl`, 'application/jsonl');
        break;
      case 'csv':
        download(toCsv(messages), `${fname}.csv`, 'text/csv');
        break;
    }
  };

  if (messages.length === 0) return null;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" title="Export transcript">
          <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
            <polyline points="7 10 12 15 17 10" />
            <line x1="12" y1="15" x2="12" y2="3" />
          </svg>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onClick={() => handleExport('md')}>
          Markdown (.md)
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => handleExport('jsonl')}>
          JSONL (.jsonl)
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => handleExport('csv')}>
          CSV (.csv)
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
};

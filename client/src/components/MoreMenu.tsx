import { useState } from 'react';
import {
  Button,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
  useTheme,
} from '@pipecat-ai/voice-ui-kit';
import {
  CheckIcon,
  ClipboardIcon,
  DownloadIcon,
  MoonIcon,
  MoreVerticalIcon,
  SunIcon,
} from 'lucide-react';

import { useTalkyMessages } from '../messages/useTalkyMessages';
import {
  downloadBlob,
  timestamp,
  toCsv,
  toJsonl,
  toMarkdown,
} from '../messages/export';

export const MoreMenu = () => {
  const messages = useTalkyMessages();
  const { resolvedTheme, setTheme } = useTheme();
  const [copied, setCopied] = useState(false);

  const isDark = resolvedTheme === 'dark';
  const hasMessages = messages.length > 0;

  const handleCopy = () => {
    navigator.clipboard.writeText(toMarkdown(messages)).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    });
  };

  const dl = (fmt: 'md' | 'jsonl' | 'csv') => {
    const ts = timestamp();
    if (fmt === 'md') downloadBlob(toMarkdown(messages), `transcript-${ts}.md`, 'text/markdown');
    if (fmt === 'jsonl') downloadBlob(toJsonl(messages), `transcript-${ts}.jsonl`, 'application/jsonl');
    if (fmt === 'csv') downloadBlob(toCsv(messages), `transcript-${ts}.csv`, 'text/csv');
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="secondary"
          size="lg"
          title="More options"
          aria-label="More options"
        >
          <MoreVerticalIcon size={16} />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-[200px]">
        <DropdownMenuItem onClick={handleCopy} disabled={!hasMessages}>
          {copied ? (
            <CheckIcon size={14} style={{ color: 'var(--color-success)' }} />
          ) : (
            <ClipboardIcon size={14} />
          )}
          <span>{copied ? 'Copied transcript' : 'Copy transcript'}</span>
        </DropdownMenuItem>

        <DropdownMenuSub>
          <DropdownMenuSubTrigger disabled={!hasMessages}>
            <DownloadIcon size={14} />
            <span>Download</span>
          </DropdownMenuSubTrigger>
          <DropdownMenuSubContent>
            <DropdownMenuItem onClick={() => dl('md')}>
              <span className="flex-1">Markdown</span>
              <span className="font-mono text-[10px] opacity-50">.md</span>
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => dl('jsonl')}>
              <span className="flex-1">JSON Lines</span>
              <span className="font-mono text-[10px] opacity-50">.jsonl</span>
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => dl('csv')}>
              <span className="flex-1">CSV</span>
              <span className="font-mono text-[10px] opacity-50">.csv</span>
            </DropdownMenuItem>
          </DropdownMenuSubContent>
        </DropdownMenuSub>

        <DropdownMenuSeparator />

        <DropdownMenuItem onClick={() => setTheme(isDark ? 'light' : 'dark')}>
          {isDark ? <SunIcon size={14} /> : <MoonIcon size={14} />}
          <span>Switch to {isDark ? 'light' : 'dark'}</span>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
};

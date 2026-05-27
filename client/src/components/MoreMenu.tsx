import { useState } from 'react';
import {
  Button,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
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
  VolumeIcon,
} from 'lucide-react';

import { useTalkyMessages } from '../messages/useTalkyMessages';
import {
  downloadBlob,
  timestamp,
  toCsv,
  toJsonl,
  toMarkdown,
} from '../messages/export';
import { inferProvider, useVoiceProfiles } from './useVoiceProfiles';

export const MoreMenu = ({ showVoiceProfile = false }: { showVoiceProfile?: boolean } = {}) => {
  const messages = useTalkyMessages();
  const { resolvedTheme, setTheme } = useTheme();
  const [copied, setCopied] = useState(false);
  const { voices, activeVoice, switchVoice } = useVoiceProfiles();

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
      <DropdownMenuContent align="end" className="min-w-[220px]">
        {showVoiceProfile && voices.length > 0 && (
          <>
            <DropdownMenuSub>
              <DropdownMenuSubTrigger>
                <VolumeIcon size={14} />
                <span>Voice</span>
              </DropdownMenuSubTrigger>
              <DropdownMenuSubContent className="max-h-[60vh] overflow-y-auto min-w-[240px]">
                <DropdownMenuLabel className="font-mono text-[10px] uppercase tracking-[0.08em] text-[var(--color-text-mute)]">
                  Voice profile
                </DropdownMenuLabel>
                <DropdownMenuRadioGroup value={activeVoice} onValueChange={switchVoice}>
                  {voices.map((v) => {
                    const prov = inferProvider(v);
                    return (
                      <DropdownMenuRadioItem key={v.name} value={v.name}>
                        <div className="flex flex-col gap-0.5 min-w-0 flex-1">
                          <div className="flex items-center gap-2">
                            <span className="truncate">{v.description || v.name}</span>
                            {prov && (
                              <span className="font-mono text-[10px] uppercase tracking-wider opacity-50 shrink-0">
                                {prov}
                              </span>
                            )}
                          </div>
                          {(v.tts || v.stt) && (
                            <div className="flex flex-wrap gap-x-2 text-[10px] text-muted-foreground font-mono">
                              {v.tts && <span><span className="opacity-60">TTS</span> {v.tts}</span>}
                              {v.stt && <span><span className="opacity-60">STT</span> {v.stt}</span>}
                            </div>
                          )}
                        </div>
                      </DropdownMenuRadioItem>
                    );
                  })}
                </DropdownMenuRadioGroup>
              </DropdownMenuSubContent>
            </DropdownMenuSub>
            <DropdownMenuSeparator />
          </>
        )}
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

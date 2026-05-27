import { useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { usePipecatClient } from '@pipecat-ai/client-react';
import { SendIcon } from 'lucide-react';

type SlashCommand = {
  cmd: string;
  desc: string;
};

const SLASH_COMMANDS: SlashCommand[] = [
  { cmd: '/clear', desc: 'Clear the conversation' },
  { cmd: '/profile', desc: 'Switch profile' },
  { cmd: '/voice', desc: 'Switch voice' },
  { cmd: '/barge', desc: 'Toggle barge-in mode' },
  { cmd: '/save', desc: 'Download transcript' },
  { cmd: '/mute', desc: 'Toggle mic' },
];

interface Props {
  /** Show palette / accept commands only when connected (else disabled placeholder). */
  connected: boolean;
}

export const TalkyTextInput = ({ connected }: Props) => {
  const client = usePipecatClient();
  const [text, setText] = useState('');
  const [paletteIdx, setPaletteIdx] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const showPalette = connected && text.startsWith('/');

  const matches = useMemo(() => {
    if (!showPalette) return [];
    const q = text.toLowerCase();
    return SLASH_COMMANDS.filter((c) => c.cmd.startsWith(q)).slice(0, 6);
  }, [text, showPalette]);

  const submit = async () => {
    const value = text.trim();
    if (!value || !connected || !client) return;
    // Slash commands intentionally don't yet route — they pass through verbatim
    // so the daemon (or a future client handler) can pick them up.
    try {
      await client.sendText(value);
      setText('');
    } catch (err) {
      console.warn('sendText failed', err);
    }
  };

  const pickCommand = (cmd: string) => {
    setText(cmd + ' ');
    inputRef.current?.focus();
  };

  const handleKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (showPalette && matches.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setPaletteIdx((i) => Math.min(i + 1, matches.length - 1));
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setPaletteIdx((i) => Math.max(i - 1, 0));
        return;
      }
      if (e.key === 'Tab') {
        e.preventDefault();
        pickCommand(matches[paletteIdx].cmd);
        return;
      }
      if (e.key === 'Enter' && !text.includes(' ')) {
        e.preventDefault();
        pickCommand(matches[paletteIdx].cmd);
        return;
      }
    }
    if (e.key === 'Enter') {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div className="relative flex-1">
      {showPalette && matches.length > 0 && (
        <div
          className="absolute left-0 right-0 overflow-hidden z-50"
          style={{
            bottom: 'calc(100% + 6px)',
            border: '1px solid var(--color-border)',
            backgroundColor: 'var(--color-popover)',
            borderRadius: 'var(--radius)',
          }}
        >
          <div
            className="font-mono text-[10px] font-semibold tracking-widest uppercase"
            style={{
              padding: '6px 10px',
              color: 'var(--color-text-mute)',
              borderBottom: '1px solid var(--color-border-soft)',
            }}
          >
            Commands
          </div>
          {matches.map((c, i) => (
            <button
              key={c.cmd}
              type="button"
              onClick={() => pickCommand(c.cmd)}
              onMouseEnter={() => setPaletteIdx(i)}
              className="flex items-center gap-2.5 w-full text-left px-2.5 py-2 text-sm"
              style={{
                backgroundColor:
                  i === paletteIdx ? 'var(--color-panel-3, var(--color-muted))' : 'transparent',
                color: 'var(--color-foreground)',
              }}
            >
              <code
                className="font-mono text-xs"
                style={{ color: 'var(--color-accent)', minWidth: 70 }}
              >
                {c.cmd}
              </code>
              <span
                className="text-xs"
                style={{ color: 'var(--color-text-dim, var(--color-muted-foreground))' }}
              >
                {c.desc}
              </span>
            </button>
          ))}
        </div>
      )}

      <div
        className="flex items-center"
        style={{
          border: '1px solid var(--color-border)',
          backgroundColor: 'var(--color-card)',
          borderRadius: 'var(--radius)',
          padding: '2px 4px 2px 10px',
        }}
      >
        <input
          ref={inputRef}
          type="text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKey}
          disabled={!connected}
          placeholder={
            connected ? 'Type, or say "hey…" · / for commands' : 'Connect to start…'
          }
          className="flex-1 outline-none bg-transparent text-sm py-2"
          style={{ color: 'var(--color-foreground)' }}
        />
        <button
          type="button"
          onClick={submit}
          disabled={!text.trim() || !connected}
          className="inline-flex items-center justify-center transition-colors"
          style={{
            width: 28,
            height: 28,
            borderRadius: 'var(--radius)',
            backgroundColor:
              text.trim() && connected
                ? 'color-mix(in srgb, var(--color-accent) 12%, transparent)'
                : 'transparent',
            color:
              text.trim() && connected
                ? 'var(--color-accent)'
                : 'var(--color-text-mute, var(--color-muted-foreground))',
            cursor: text.trim() && connected ? 'pointer' : 'default',
          }}
        >
          <SendIcon size={14} />
        </button>
      </div>
    </div>
  );
};

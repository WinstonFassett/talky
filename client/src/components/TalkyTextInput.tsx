import { useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { SendIcon, SquareIcon } from 'lucide-react';

import { useComposer } from '../composer/ComposerProvider';
import { useTurn } from '../turn/TurnProvider';

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

const MAX_HEIGHT = 160;

// A touch keyboard has no Shift+Enter: Return is a newline and only Send sends.
const isTouch = () =>
  typeof window !== 'undefined' && window.matchMedia('(pointer: coarse)').matches;

export const TalkyTextInput = () => {
  const { text, setText, send, sending, error, inputRef } = useComposer();
  const turn = useTurn();
  const [paletteIdx, setPaletteIdx] = useState(0);
  const composing = useRef(false);
  const [touch] = useState(isTouch);

  const showPalette = text.startsWith('/') && !text.includes('\n');

  const matches = useMemo(() => {
    if (!showPalette) return [];
    const q = text.toLowerCase();
    return SLASH_COMMANDS.filter((c) => c.cmd.startsWith(q)).slice(0, 6);
  }, [text, showPalette]);

  // Grow with the text up to MAX_HEIGHT, then scroll inside.
  useLayoutEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = 'auto';
    const h = Math.min(el.scrollHeight, MAX_HEIGHT);
    el.style.height = `${h}px`;
    el.style.overflowY = el.scrollHeight > MAX_HEIGHT ? 'auto' : 'hidden';
  }, [text, inputRef]);

  const pickCommand = (cmd: string) => {
    setText(cmd + ' ');
    inputRef.current?.focus();
  };

  const handleKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
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
    if (e.key !== 'Enter') return;
    // Enter that confirms an IME candidate is not a send.
    if (composing.current || e.nativeEvent.isComposing || e.keyCode === 229) return;
    if (e.shiftKey || touch) return;
    e.preventDefault();
    send();
  };

  const hasText = !!text.trim();
  const busy = turn.running;

  return (
    <div className="relative flex-1 min-w-0">
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

      {error && (
        <div role="alert" data-testid="composer-error" className="mb-1.5 text-[12px]" style={{ color: 'var(--color-destructive)' }}>
          {error}
        </div>
      )}

      <div
        className="flex items-end"
        style={{
          border: '1px solid var(--color-border)',
          backgroundColor: 'var(--color-card)',
          borderRadius: 'var(--radius)',
          padding: '2px 4px 2px 10px',
        }}
      >
        <textarea
          ref={inputRef}
          data-testid="composer"
          aria-label="Message"
          aria-describedby="composer-hint"
          rows={1}
          autoFocus
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKey}
          onCompositionStart={() => (composing.current = true)}
          onCompositionEnd={() => (composing.current = false)}
          placeholder={'Type, or say "hey…" · / for commands'}
          className="flex-1 min-w-0 resize-none outline-none bg-transparent text-sm py-2 leading-5"
          style={{ color: 'var(--color-foreground)', maxHeight: MAX_HEIGHT }}
        />
        {busy ? (
          <button
            type="button"
            data-testid="composer-stop"
            aria-label="Cancel message"
            title="Cancel message (Esc)"
            onClick={turn.stop}
            className="inline-flex items-center justify-center shrink-0 transition-colors mb-1"
            style={{
              width: 28,
              height: 28,
              borderRadius: 'var(--radius)',
              backgroundColor: 'color-mix(in srgb, var(--color-accent) 12%, transparent)',
              color: 'var(--color-accent)',
            }}
          >
            <SquareIcon size={12} fill="currentColor" />
          </button>
        ) : (
          <button
            type="button"
            data-testid="composer-send"
            onClick={send}
            disabled={!hasText || sending}
            aria-label={hasText ? 'Send message' : 'Message requires text'}
            title={hasText ? 'Send message' : 'Message requires text'}
            className="inline-flex items-center justify-center shrink-0 transition-colors mb-1"
            style={{
              width: 28,
              height: 28,
              borderRadius: 'var(--radius)',
              backgroundColor: hasText ? 'color-mix(in srgb, var(--color-accent) 12%, transparent)' : 'transparent',
              color: hasText ? 'var(--color-accent)' : 'var(--color-text-mute, var(--color-muted-foreground))',
              cursor: hasText ? 'pointer' : 'default',
            }}
          >
            <SendIcon size={14} />
          </button>
        )}
      </div>
      <div
        id="composer-hint"
        data-testid="composer-hint"
        className="mt-1 px-0.5 text-[11px] truncate"
        style={{ color: 'var(--color-text-mute)' }}
      >
        {touch ? 'Return for new line · tap Send to send' : 'Enter to send, Shift + Enter for new line'}
      </div>
    </div>
  );
};

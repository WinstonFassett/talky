import { useState, useLayoutEffect } from 'react';
import { Loader2Icon, AudioLinesIcon } from 'lucide-react';
import { VoiceVisualizer } from '@pipecat-ai/voice-ui-kit';

import type { VoiceState } from './useVoiceState';

// The Converse button. Replaces the header ConnectButton — it lives in the
// conversation panel input row, to the right of the text input. One control
// that connects, shows live voice state inside its own bounds, and ends the
// call.
//
// States (handoff-locked):
//   disconnected / idle      → "Converse"        (connects on click)
//   listening (user talking) → mic visualizer inside the button bounds
//   thinking                 → "Thinking…" + spinner
//   speaking (bot talking)   → "End" + spinner
//   connecting               → "Connecting…" + spinner
//   error                    → "Retry" in destructive color (reconnects)
//
// A connected-but-quiet state still reads "End" so the user always has a way
// to hang up.

function readAccent(): string {
  if (typeof window === 'undefined') return '#8ab4d8';
  return (
    getComputedStyle(document.documentElement)
      .getPropertyValue('--color-accent')
      .trim() || '#8ab4d8'
  );
}

interface ConverseButtonProps {
  voiceState: VoiceState;
  connected: boolean;
  connecting: boolean;
  error: boolean;
  onConnect: () => void;
  onDisconnect: () => void;
}

export const ConverseButton = ({
  voiceState,
  connected,
  connecting,
  error,
  onConnect,
  onDisconnect,
}: ConverseButtonProps) => {
  const [accent, setAccent] = useState<string>(() => readAccent());
  useLayoutEffect(() => {
    setAccent(readAccent());
    const obs = new MutationObserver(() => setAccent(readAccent()));
    obs.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['class', 'data-theme'],
    });
    return () => obs.disconnect();
  }, []);

  const isActive = connected || connecting;
  const onClick = isActive && !connecting ? onDisconnect : onConnect;

  // Shared shell. Accent border when active so the live moment reads; default
  // border at rest. No shadow (DESIGN.md flat-by-default).
  const baseClass =
    'inline-flex items-center justify-center gap-2 shrink-0 rounded-md h-9 px-3.5 min-w-[112px] ' +
    'text-[13px] font-medium transition-colors cursor-pointer select-none ' +
    'disabled:cursor-not-allowed';

  let borderColor = 'var(--color-border)';
  let textColor = 'var(--color-foreground)';
  const bg = 'transparent';

  if (error) {
    borderColor = 'var(--color-destructive)';
    textColor = 'var(--color-destructive)';
  } else if (isActive) {
    borderColor = 'color-mix(in oklab, var(--color-accent) 40%, transparent)';
  }

  const style = {
    border: `1px solid ${borderColor}`,
    backgroundColor: bg,
    color: textColor,
  };

  // ── Error ──
  if (error) {
    return (
      <button type="button" onClick={onConnect} className={baseClass} style={style}>
        Retry
      </button>
    );
  }

  // ── Connecting ──
  if (connecting) {
    return (
      <button type="button" disabled className={baseClass} style={style} aria-busy="true">
        <Loader2Icon size={14} className="animate-spin" aria-hidden="true" />
        Connecting…
      </button>
    );
  }

  // ── Listening: mic visualizer inside the button ──
  if (connected && voiceState === 'listening') {
    return (
      <button
        type="button"
        onClick={onDisconnect}
        className={baseClass}
        style={style}
        aria-label="End conversation (listening)"
      >
        <div className="h-5 w-full overflow-hidden" aria-hidden="true">
          <VoiceVisualizer
            participantType="local"
            barColor={accent}
            backgroundColor="transparent"
            barCount={18}
            barGap={2}
            barWidth={2}
            barMaxHeight={20}
            barOrigin="center"
            barLineCap="round"
            className="w-full h-full"
          />
        </div>
      </button>
    );
  }

  // ── Thinking ──
  if (connected && voiceState === 'thinking') {
    return (
      <button type="button" onClick={onDisconnect} className={baseClass} style={style}>
        <Loader2Icon size={14} className="animate-spin" aria-hidden="true" />
        Thinking…
      </button>
    );
  }

  // ── Speaking: "End" + spinner ──
  if (connected && voiceState === 'speaking') {
    return (
      <button type="button" onClick={onDisconnect} className={baseClass} style={style}>
        <Loader2Icon size={14} className="animate-spin" aria-hidden="true" />
        End
      </button>
    );
  }

  // ── Connected & quiet (idle) ──
  if (connected) {
    return (
      <button type="button" onClick={onDisconnect} className={baseClass} style={style}>
        End
      </button>
    );
  }

  // ── Disconnected ──
  return (
    <button type="button" onClick={onClick} className={baseClass} style={style}>
      <AudioLinesIcon size={15} aria-hidden="true" />
      Converse
    </button>
  );
};

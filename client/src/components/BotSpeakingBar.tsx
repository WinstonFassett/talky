import { Volume2Icon, SquareIcon } from 'lucide-react';
import { VoiceVisualizer } from '@pipecat-ai/voice-ui-kit';

// The bot-speaking bar. Renders above the input row, only while the bot is
// producing TTS audio. Speaker icon + "Speaking response" + a live bar
// visualizer driven by the bot audio track + an optional Stop button.
//
// Stop cancels in-flight TTS by sending an 'interrupt' app-message over the
// WebRTC data channel (sendClientMessage); the daemon queues a single
// InterruptionFrame — the same frame a VAD-detected speech onset ultimately
// produces (no full disconnect, no fabricated user-speaking turn). The
// handler is passed via `onStop`; if a caller omits it the button is absent.
//
// Visibility is the caller's responsibility (App only mounts this when
// botSpeaking is true), but we also honor ?voiceState=speaking for fixture
// work so the bar can be inspected without a live bot.
export const BotSpeakingBar = ({ onStop }: { onStop?: () => void } = {}) => {
  // Accent for the bars — read once; the bar is short-lived so we don't
  // bother observing theme flips here (the visualizer remounts each time the
  // bot starts speaking).
  const barColor =
    typeof window !== 'undefined'
      ? getComputedStyle(document.documentElement)
          .getPropertyValue('--color-accent')
          .trim() || '#8ab4d8'
      : '#8ab4d8';

  // Under a fixture override we have no real bot track; the visualizer simply
  // renders idle bars. That's fine for layout inspection.
  return (
    <div
      className="flex items-center gap-3 rounded-md px-3 py-2 mb-2"
      style={{
        backgroundColor: 'var(--color-panel-2)',
        border: '1px solid var(--color-border)',
      }}
      role="status"
      aria-live="polite"
      data-testid="bot-speaking-bar"
    >
      <Volume2Icon
        size={15}
        className="shrink-0"
        style={{ color: 'var(--color-accent)' }}
        aria-hidden="true"
      />
      <span
        className="font-mono uppercase shrink-0"
        style={{
          fontSize: 10,
          fontWeight: 600,
          letterSpacing: '0.12em',
          color: 'var(--color-text-dim)',
        }}
      >
        Speaking response
      </span>

      <div className="flex-1 min-w-0 h-5 overflow-hidden" aria-hidden="true">
        <VoiceVisualizer
          participantType="bot"
          barColor={barColor}
          backgroundColor="transparent"
          barCount={28}
          barGap={2}
          barWidth={2}
          barMaxHeight={20}
          barOrigin="center"
          barLineCap="round"
          className="w-full h-full"
        />
      </div>

      {onStop && (
        <button
          type="button"
          onClick={onStop}
          aria-label="Stop speaking"
          title="Stop speaking"
          className="inline-flex items-center gap-1.5 shrink-0 rounded-md px-2 h-7 font-mono uppercase transition-colors cursor-pointer"
          style={{
            fontSize: 10,
            fontWeight: 600,
            letterSpacing: '0.12em',
            color: 'var(--color-text-dim)',
            backgroundColor: 'var(--color-panel-3)',
          }}
        >
          <SquareIcon size={11} aria-hidden="true" />
          Stop
        </button>
      )}
    </div>
  );
};

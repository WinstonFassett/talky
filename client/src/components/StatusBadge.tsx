import { type VoiceState, VOICE_STATE_LABELS } from './useVoiceState';

export const StatusBadge = ({ state }: { state: VoiceState }) => {
  const isActive = state === 'listening' || state === 'thinking' || state === 'speaking';
  const animation =
    state === 'thinking'
      ? 'pulse 1.2s ease-in-out infinite'
      : state === 'speaking'
        ? 'pulse 0.5s ease-in-out infinite'
        : state === 'listening'
          ? 'pulse 1.6s ease-in-out infinite'
          : 'none';

  return (
    <div
      aria-label={`Voice status: ${VOICE_STATE_LABELS[state]}`}
      className="flex items-center gap-2 w-[110px] shrink-0"
    >
      <div
        className="size-1.5 rounded-full shrink-0 transition-colors"
        style={{
          backgroundColor: isActive ? 'var(--color-accent)' : 'var(--color-text-mute)',
          boxShadow: isActive ? '0 0 6px color-mix(in srgb, var(--color-accent) 35%, transparent)' : 'none',
          animation,
        }}
      />
      <span
        className="font-mono text-[10px] font-semibold tracking-widest uppercase whitespace-nowrap overflow-hidden text-ellipsis transition-colors"
        style={{ color: isActive ? 'var(--color-accent)' : 'var(--color-text-mute)' }}
      >
        {VOICE_STATE_LABELS[state]}
      </span>
    </div>
  );
};

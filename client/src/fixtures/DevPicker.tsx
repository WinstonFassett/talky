import { useUrlParam, setUrlParam, type SimulatedVoiceState } from './harness';
import { FIXTURE_NAMES } from './messages';

const VOICE_STATES: (SimulatedVoiceState | '')[] = [
  '',
  'disconnected',
  'idle',
  'listening',
  'thinking',
  'speaking',
];

export const DevPicker = () => {
  const fixture = useUrlParam('fixture') ?? '';
  const voiceState = useUrlParam('voiceState') ?? '';

  return (
    <div
      style={{
        position: 'fixed',
        bottom: 12,
        right: 12,
        zIndex: 1000,
        display: 'flex',
        gap: 6,
        alignItems: 'center',
        padding: '6px 10px',
        background: 'var(--card, rgba(20,20,22,0.92))',
        color: 'var(--card-foreground, #ececec)',
        border: '1px solid var(--border, #262628)',
        borderRadius: 6,
        fontFamily: 'var(--font-mono, ui-monospace, monospace)',
        fontSize: 11,
        backdropFilter: 'blur(8px)',
      }}
    >
      <span style={{ opacity: 0.5, letterSpacing: '0.08em', textTransform: 'uppercase' }}>
        dev
      </span>
      <select
        value={fixture}
        onChange={(e) => setUrlParam('fixture', e.target.value || null)}
        style={pickerStyle}
        aria-label="Fixture"
      >
        <option value="">(no fixture)</option>
        {FIXTURE_NAMES.map((name) => (
          <option key={name} value={name}>
            {name}
          </option>
        ))}
      </select>
      <select
        value={voiceState}
        onChange={(e) => setUrlParam('voiceState', e.target.value || null)}
        style={pickerStyle}
        aria-label="Voice state"
      >
        {VOICE_STATES.map((s) => (
          <option key={s} value={s}>
            {s === '' ? '(real state)' : s}
          </option>
        ))}
      </select>
    </div>
  );
};

const pickerStyle: React.CSSProperties = {
  background: 'transparent',
  color: 'inherit',
  border: '1px solid var(--border, #262628)',
  borderRadius: 4,
  padding: '3px 6px',
  fontFamily: 'inherit',
  fontSize: 11,
};

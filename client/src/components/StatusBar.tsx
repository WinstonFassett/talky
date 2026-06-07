import { useEffect, useRef, useState } from 'react';

import { LLMProfileSelect } from './LLMProfileSelect';
import { VoiceProfileSelect } from './VoiceProfileSelect';
import { SessionSheet } from './SessionSheet';

// Footer chrome (h-8). Left: the two profile pickers (flat Label-typography
// variant). Right: the session timer + version chip. Both halves are
// Label typography throughout (Geist Mono 10px, 0.12em tracking).
//
// Disconnected: pickers stay (they set the profile for the next connect);
// the timer hides; the version stays. Mobile (<640px): collapse both pickers
// into the existing SessionSheet drawer.
//
// Version chip is wired from vite.config.ts (VITE_APP_VERSION = pyproject
// version, VITE_GIT_SHA = git short hash). Either being empty hides that
// part rather than rendering "v undefined".
const APP_VERSION = import.meta.env.VITE_APP_VERSION || '';
const GIT_SHA = import.meta.env.VITE_GIT_SHA || '';

function formatElapsed(totalSeconds: number): string {
  const s = totalSeconds % 60;
  const m = Math.floor(totalSeconds / 60) % 60;
  const h = Math.floor(totalSeconds / 3600);
  const pad = (n: number) => String(n).padStart(2, '0');
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

function SessionTimer({ connected }: { connected: boolean }) {
  const [elapsed, setElapsed] = useState(0);
  const startRef = useRef<number | null>(null);

  useEffect(() => {
    if (!connected) {
      startRef.current = null;
      setElapsed(0);
      return;
    }
    startRef.current = Date.now();
    setElapsed(0);
    const id = setInterval(() => {
      if (startRef.current != null) {
        setElapsed(Math.floor((Date.now() - startRef.current) / 1000));
      }
    }, 1000);
    return () => clearInterval(id);
  }, [connected]);

  if (!connected) return null;
  return (
    <span
      className="font-mono tabular-nums"
      style={{ fontSize: 10, letterSpacing: '0.12em', color: 'var(--color-text-dim)' }}
      aria-label={`Session length ${formatElapsed(elapsed)}`}
    >
      {formatElapsed(elapsed)}
    </span>
  );
}

function VersionChip() {
  if (!APP_VERSION && !GIT_SHA) return null;
  return (
    <span
      className="font-mono"
      style={{ fontSize: 10, letterSpacing: '0.12em', color: 'var(--color-text-mute)' }}
      title={GIT_SHA ? `commit ${GIT_SHA}` : undefined}
    >
      {APP_VERSION && `v${APP_VERSION}`}
      {APP_VERSION && GIT_SHA && ' '}
      {GIT_SHA}
    </span>
  );
}

export const StatusBar = ({
  connected,
  isNarrow,
  currentLabel,
}: {
  connected: boolean;
  isNarrow: boolean;
  currentLabel?: string;
}) => {
  return (
    <footer
      className="flex items-center shrink-0 border-t gap-2 px-2 sm:px-3 h-8"
      style={{
        borderColor: 'var(--color-border-soft)',
        backgroundColor: 'var(--color-card)',
      }}
    >
      {/* Left: profile + voice pickers. Mobile collapses into the SessionSheet. */}
      <div className="flex items-center min-w-0 flex-1">
        {isNarrow ? (
          <SessionSheet currentLabel={currentLabel} />
        ) : (
          <div className="flex items-center min-w-0">
            <LLMProfileSelect variant="footer" />
            <VoiceProfileSelect variant="footer" />
          </div>
        )}
      </div>

      {/* Right: timer + version. */}
      <div className="flex items-center gap-3 shrink-0">
        <SessionTimer connected={connected} />
        <VersionChip />
      </div>
    </footer>
  );
};

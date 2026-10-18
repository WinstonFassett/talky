import { PhoneIcon } from 'lucide-react';

import { SessionControls } from './SessionControls';
import { useComposer } from '../composer/ComposerProvider';

interface Props {
  onConnect?: () => void;
}

// One click sends the starter as the first message (typed, mic muted).
const STARTERS = [
  'What can you help me with?',
  'Help me plan my day.',
  'Talk me through an idea I have.',
  'Explain something I am curious about.',
];

const StartButton = ({ onConnect }: { onConnect: () => void }) => (
  <button
    onClick={onConnect}
    className="w-full inline-flex items-center justify-center gap-2 py-3 text-[15px] font-medium transition-colors rounded-lg hover:bg-[color-mix(in_srgb,var(--color-accent)_20%,transparent)]"
    style={{
      border: '1px solid var(--color-accent)',
      backgroundColor: 'color-mix(in srgb, var(--color-accent) 12%, transparent)',
      color: 'var(--color-accent)',
    }}
  >
    <PhoneIcon size={16} aria-hidden="true" />
    <span>Start call</span>
  </button>
);

// The first screen of a new session: a plain question, a few starters, the
// assistant / voice / audio pickers, and the call button. The composer sits
// below it, focused.
export const EmptyState = ({ onConnect }: Props) => {
  const composer = useComposer();
  return (
    <div className="flex flex-col items-center gap-6 py-8 sm:py-12" data-testid="empty-state">
      <h2
        className="text-xl text-center"
        style={{ fontWeight: 500, letterSpacing: '-0.01em', color: 'var(--color-foreground)' }}
      >
        What do you want to talk about?
      </h2>

      <div className="w-full max-w-[420px] grid grid-cols-1 sm:grid-cols-2 gap-2">
        {STARTERS.map((s) => (
          <button
            key={s}
            type="button"
            data-testid="starter"
            onClick={() => composer.submit(s)}
            className="rounded-lg px-3 py-2.5 text-left text-[13px] transition-colors hover:bg-[var(--color-panel-3)]"
            style={{ backgroundColor: 'var(--color-panel-2)', color: 'var(--color-text-dim)' }}
          >
            {s}
          </button>
        ))}
      </div>

      <div className="w-full max-w-[360px] flex flex-col gap-6">
        <SessionControls />
        {onConnect && <StartButton onConnect={onConnect} />}
      </div>
    </div>
  );
};

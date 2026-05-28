import { PhoneIcon } from 'lucide-react';

import { SessionControls } from './SessionControls';

interface Props {
  onConnect?: () => void;
}

const StartButton = ({
  onConnect,
  className,
}: {
  onConnect: () => void;
  className?: string;
}) => (
  <button
    onClick={onConnect}
    className={`w-full inline-flex items-center justify-center gap-2 py-3 text-[15px] font-medium transition-colors rounded-lg ${className ?? ''}`}
    style={{
      border: '1px solid var(--color-accent)',
      backgroundColor: 'color-mix(in srgb, var(--color-accent) 12%, transparent)',
      color: 'var(--color-accent)',
    }}
    onMouseEnter={(e) => {
      e.currentTarget.style.backgroundColor = 'color-mix(in srgb, var(--color-accent) 20%, transparent)';
    }}
    onMouseLeave={(e) => {
      e.currentTarget.style.backgroundColor = 'color-mix(in srgb, var(--color-accent) 12%, transparent)';
    }}
  >
    <PhoneIcon size={16} />
    <span>Start call</span>
  </button>
);

export const EmptyState = ({ onConnect }: Props) => {
  return (
    <div className="flex-1 flex flex-col w-full min-h-0">
      <div className="flex-1 flex flex-col items-center justify-center gap-6 p-6 sm:p-10 overflow-y-auto">
        <span
          className="text-3xl select-none"
          style={{
            fontWeight: 500,
            letterSpacing: '-0.02em',
            color: 'var(--color-foreground)',
          }}
        >
          talky
        </span>

        <div className="w-full max-w-[360px] flex flex-col gap-6">
          <SessionControls />
          {/* Desktop: inline start button below the controls. Mobile uses the pinned footer. */}
          {onConnect && (
            <div className="hidden sm:block">
              <StartButton onConnect={onConnect} />
            </div>
          )}
        </div>
      </div>

      {/* Mobile: pinned to the foot of the app */}
      {onConnect && (
        <div
          className="sm:hidden shrink-0 px-4 pt-3"
          style={{
            paddingBottom: 'max(0.75rem, env(safe-area-inset-bottom))',
            borderTop: '1px solid var(--color-border-soft)',
            backgroundColor: 'var(--color-card)',
          }}
        >
          <StartButton onConnect={onConnect} />
        </div>
      )}
    </div>
  );
};

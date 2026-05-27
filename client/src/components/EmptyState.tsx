import { PhoneIcon } from 'lucide-react';

interface Props {
  profileLabel?: string;
  onConnect?: () => void;
}

export const EmptyState = ({ profileLabel, onConnect }: Props) => {
  return (
    <div className="flex-1 flex flex-col items-center justify-center gap-5 p-10">
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
      <p
        className="text-sm text-center max-w-[320px] leading-relaxed"
        style={{ color: 'var(--color-text-dim)' }}
      >
        {profileLabel
          ? `Voice conversation with ${profileLabel}.`
          : 'Voice conversation, ready when you are.'}
      </p>
      {onConnect && (
        <button
          onClick={onConnect}
          className="inline-flex items-center gap-2 px-5 py-2.5 text-sm font-medium transition-colors rounded"
          style={{
            border: '1px solid var(--color-accent)',
            backgroundColor: 'color-mix(in srgb, var(--color-accent) 8%, transparent)',
            color: 'var(--color-accent)',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.backgroundColor = 'color-mix(in srgb, var(--color-accent) 16%, transparent)';
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.backgroundColor = 'color-mix(in srgb, var(--color-accent) 8%, transparent)';
          }}
        >
          <PhoneIcon size={14} />
          <span>Start call</span>
        </button>
      )}
    </div>
  );
};

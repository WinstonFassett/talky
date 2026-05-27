import { useEffect, useState } from 'react';

type SteerMode = 'steer' | 'interrupt';

interface Props {
  /** Only render when active profile is hermes. */
  activeProfile: string;
}

// Subtle barge-in chip — sits inside the input area.
// Click to toggle between steer (inject after current tool batch)
// and interrupt (hard-stop immediately).
export const SteerModeChip = ({ activeProfile }: Props) => {
  const [mode, setMode] = useState<SteerMode>('steer');
  const [switching, setSwitching] = useState(false);

  const isHermes = activeProfile === 'hermes';

  useEffect(() => {
    if (!isHermes) return;
    const es = new EventSource('/api/events');
    es.addEventListener('init', (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        if (data.steerMode) setMode(data.steerMode as SteerMode);
      } catch { /* ignore */ }
    });
    es.addEventListener('steerModeChanged', (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        if (data.mode) setMode(data.mode as SteerMode);
      } catch { /* ignore */ }
    });
    return () => es.close();
  }, [isHermes]);

  if (!isHermes) return null;

  const toggle = async () => {
    if (switching) return;
    const next: SteerMode = mode === 'steer' ? 'interrupt' : 'steer';
    setSwitching(true);
    try {
      await fetch('/api/steer-mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: next }),
      });
    } catch { /* ignore */ } finally {
      setSwitching(false);
    }
  };

  const title =
    mode === 'steer'
      ? 'Barge-in: gently steer mid-response — click to switch to interrupt'
      : 'Barge-in: cut off immediately — click to switch to steer';

  return (
    <button
      onClick={toggle}
      title={title}
      disabled={switching}
      className="font-mono text-[10px] font-semibold tracking-widest uppercase transition-colors px-2 h-[26px] rounded shrink-0"
      style={{
        backgroundColor: 'var(--color-panel-3, var(--color-muted))',
        border: '1px solid var(--color-border-soft, var(--color-border))',
        color: 'var(--color-text-dim, var(--color-muted-foreground))',
      }}
    >
      {mode}
    </button>
  );
};

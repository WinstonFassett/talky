import { useEffect, useState } from 'react';
import { Button } from './ui/button';

type SteerMode = 'steer' | 'interrupt';

interface SteerModeToggleProps {
  /** Only render when active profile is hermes. */
  activeProfile: string;
}

export const SteerModeToggle = ({ activeProfile }: SteerModeToggleProps) => {
  const [mode, setMode] = useState<SteerMode>('steer');
  const [switching, setSwitching] = useState(false);

  const isHermes = activeProfile === 'hermes';

  // Sync from SSE init + steerModeChanged events.
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

  const handleSwitch = async (next: SteerMode) => {
    if (next === mode || switching) return;
    setSwitching(true);
    try {
      await fetch('/api/steer-mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: next }),
      });
      // SSE steerModeChanged will update state.
    } catch { /* ignore */ } finally {
      setSwitching(false);
    }
  };

  return (
    <div className="flex items-center gap-1.5">
      <span className="text-sm font-medium text-gray-700">Barge-in:</span>
      <div className="flex rounded-lg border border-border overflow-hidden">
        <Button
          size="sm"
          variant={mode === 'steer' ? 'default' : 'ghost'}
          disabled={switching}
          onClick={() => handleSwitch('steer')}
          className="rounded-none border-0"
          title="Steer: inject after current tool batch, no hard stop"
        >
          Steer
        </Button>
        <Button
          size="sm"
          variant={mode === 'interrupt' ? 'default' : 'ghost'}
          disabled={switching}
          onClick={() => handleSwitch('interrupt')}
          className="rounded-none border-0 border-l border-border"
          title="Interrupt: hard-stop current tool batch immediately"
        >
          Interrupt
        </Button>
      </div>
    </div>
  );
};

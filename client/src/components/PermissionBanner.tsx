import { useEffect, useRef, useState } from 'react';

interface PermissionRequest {
  tool_name: string;
  tool_input: Record<string, unknown>;
}

function playPermissionAlert() {
  try {
    const ctx = new AudioContext();
    const now = ctx.currentTime;
    // Two ascending tones — distinct from the drop cue (descending)
    [0, 0.18].forEach((delay, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.frequency.value = i === 0 ? 880 : 1100;
      osc.type = 'sine';
      gain.gain.setValueAtTime(0, now + delay);
      gain.gain.linearRampToValueAtTime(0.35, now + delay + 0.02);
      gain.gain.linearRampToValueAtTime(0, now + delay + 0.14);
      osc.start(now + delay);
      osc.stop(now + delay + 0.16);
    });
    // Close after tones finish
    setTimeout(() => ctx.close(), 600);
  } catch {
    // AudioContext not available — silent fallback
  }
}

interface Props {
  onResolve: (allow: boolean) => void;
  request: PermissionRequest;
}

function Banner({ request, onResolve }: Props) {
  const inputLines = Object.entries(request.tool_input)
    .slice(0, 3)
    .map(([k, v]) => `${k}: ${JSON.stringify(v)}`)
    .join('\n');

  return (
    <div className="fixed top-4 left-1/2 -translate-x-1/2 z-50 flex flex-col gap-2 bg-background border border-border rounded-xl shadow-xl p-4 w-[420px] max-w-[calc(100vw-2rem)]">
      <div className="flex items-center gap-2">
        <span className="text-sm font-semibold text-foreground">Permission required</span>
        <span className="ml-auto text-xs font-mono text-muted-foreground bg-muted px-1.5 py-0.5 rounded">
          {request.tool_name}
        </span>
      </div>
      {inputLines && (
        <pre className="text-xs text-muted-foreground font-mono bg-muted rounded p-2 overflow-x-auto whitespace-pre-wrap break-all max-h-24">
          {inputLines}
        </pre>
      )}
      <div className="flex gap-2 justify-end mt-1">
        <button
          onClick={() => onResolve(false)}
          className="text-xs px-3 py-1.5 rounded-md border border-border hover:bg-muted transition-colors"
        >
          Deny
        </button>
        <button
          onClick={() => onResolve(true)}
          className="text-xs px-3 py-1.5 rounded-md bg-primary text-primary-foreground hover:opacity-90 transition-opacity"
        >
          Allow
        </button>
      </div>
    </div>
  );
}

export function PermissionBanner() {
  const [pending, setPending] = useState<PermissionRequest | null>(null);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    const es = new EventSource('/api/events');
    esRef.current = es;

    es.addEventListener('permissionRequest', (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data) as PermissionRequest;
        setPending(data);
        playPermissionAlert();
      } catch { /* ignore malformed */ }
    });

    return () => {
      es.close();
      esRef.current = null;
    };
  }, []);

  const handleResolve = async (allow: boolean) => {
    setPending(null);
    try {
      await fetch('/api/permission/grant', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ allow }),
      });
    } catch { /* daemon may be gone */ }
  };

  if (!pending) return null;
  return <Banner request={pending} onResolve={handleResolve} />;
}

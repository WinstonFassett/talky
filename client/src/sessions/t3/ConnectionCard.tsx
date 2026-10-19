// T3 connection card (WIN-575): shown only when Talky can't reach T3 Code. Talky connects by itself,
// so this appears on failure (CLI missing, T3 not running, session denied) or after Disconnect.
import { useState } from 'react';

import { t3Api, type T3Status } from '../api';

export function ConnectionCard({ status, onStatus }: { status: T3Status; onStatus: (s: T3Status) => void }) {
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  const connect = async () => {
    setBusy(true);
    try {
      const next = await t3Api.connect();
      setFailed(next.state !== 'connected');
      onStatus(next);
    } catch (e) {
      setFailed(true);
      onStatus({ ...status, detail: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  };

  const retry = failed || (status.code !== null && status.detail !== 'disconnected');
  return (
    <div
      data-testid="connection-card"
      data-code={status.code ?? ''}
      className="mx-1.5 mb-2 rounded-md px-2 py-2 text-[12px]"
      style={{ border: '1px solid var(--color-border-soft)', color: 'var(--color-text-dim)' }}
    >
      <div style={{ color: failed || status.code ? 'var(--color-destructive)' : 'var(--color-text-mute)' }} data-testid="connection-detail">
        {status.detail || 'Not connected to T3 Code.'}
      </div>
      <button
        type="button"
        data-testid={retry ? 'connection-retry' : 'connection-connect'}
        disabled={busy}
        className="mt-2 rounded px-2 py-1 text-[12px] hover:bg-[var(--color-muted)] disabled:opacity-60"
        style={{ border: '1px solid var(--color-border-soft)', color: 'var(--color-foreground)' }}
        onClick={() => void connect()}
      >
        {busy ? 'Connecting…' : retry ? 'Retry' : 'Connect'}
      </button>
    </div>
  );
}

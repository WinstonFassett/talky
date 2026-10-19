// T3 Code threads (WIN-564): pick an existing T3 thread and talk to it.
// Picking attaches — the daemon binds a Talky session to the thread (seeded with its
// recent messages) and the normal session flow opens it.
import { useCallback, useEffect, useState } from 'react';
import { ChevronDownIcon, ChevronRightIcon, RefreshCwIcon } from 'lucide-react';

import { t3Api, type T3Thread, type T3ThreadStatus } from './api';
import { relativeTime } from './groups';
import { useSessions } from './SessionsProvider';

const OPEN_KEY = 'talky.t3Open';
const COLOR: Record<T3ThreadStatus, string> = {
  running: 'var(--color-accent)',
  idle: 'var(--color-text-mute)',
  completed: 'var(--color-success)',
  failed: 'var(--color-destructive)',
};

export function T3Threads({ onPicked }: { onPicked?: () => void }) {
  const { open, rows } = useSessions();
  const [expanded, setExpanded] = useState(() => {
    try {
      return localStorage.getItem(OPEN_KEY) === '1';
    } catch {
      return false;
    }
  });
  const [threads, setThreads] = useState<T3Thread[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [attaching, setAttaching] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setThreads((await t3Api.threads()).threads);
      setError(null);
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (expanded && threads === null) void load();
  }, [expanded, threads, load]);

  const toggle = () => {
    const next = !expanded;
    setExpanded(next);
    try {
      localStorage.setItem(OPEN_KEY, next ? '1' : '0');
    } catch { /* private mode */ }
  };

  const pick = async (t: T3Thread) => {
    setAttaching(t.id);
    try {
      const out = await t3Api.attach(t.id);
      await open(out.convo_id, 't3');
      onPicked?.();
    } catch (e) {
      setError(`Couldn't attach: ${e instanceof Error ? e.message : e}`);
    } finally {
      setAttaching(null);
    }
  };

  const attachedIds = new Set(rows.filter((r) => r.backend === 't3').map((r) => r.native_session_id));

  return (
    <div className="shrink-0" style={{ borderTop: '1px solid var(--color-border-soft)' }} data-testid="t3-threads">
      <div className="flex h-8 items-center pr-1">
        <button
          type="button"
          data-testid="t3-toggle"
          aria-expanded={expanded}
          className="flex min-w-0 flex-1 items-center gap-1 pl-2 text-left font-mono uppercase"
          style={{ fontSize: 10, fontWeight: 600, letterSpacing: '0.12em', color: 'var(--color-text-mute)' }}
          onClick={toggle}
        >
          {expanded ? <ChevronDownIcon size={13} /> : <ChevronRightIcon size={13} />}
          T3 Code threads
        </button>
        {expanded && (
          <button
            type="button"
            data-testid="t3-refresh"
            title="Refresh T3 threads"
            aria-label="Refresh T3 threads"
            className="inline-flex size-6 items-center justify-center rounded hover:bg-[var(--color-muted)]"
            style={{ color: 'var(--color-text-dim)' }}
            onClick={() => void load()}
          >
            <RefreshCwIcon size={12} className={loading ? 'animate-spin' : ''} />
          </button>
        )}
      </div>
      {expanded && (
        <div className="max-h-64 overflow-y-auto px-1.5 pb-2" data-testid="t3-thread-list">
          {error && (
            <div className="px-2 pb-2 text-[12px]" style={{ color: 'var(--color-destructive)' }} data-testid="t3-error">
              {error}
              {error.includes('T3 unavailable') && (
                <div style={{ color: 'var(--color-text-mute)' }}>Run `talky t3 login`, and make sure T3 Code is open.</div>
              )}
            </div>
          )}
          {threads?.length === 0 && (
            <div className="px-2 py-3 text-center text-[12px]" style={{ color: 'var(--color-text-mute)' }}>
              No T3 threads.
            </div>
          )}
          {threads?.map((t) => (
            <button
              key={t.id}
              type="button"
              data-testid="t3-thread"
              data-thread-id={t.id}
              data-status={t.status}
              disabled={attaching !== null}
              className="flex w-full flex-col gap-0.5 rounded-md px-2 py-1.5 text-left hover:bg-[var(--color-muted)] disabled:opacity-60"
              onClick={() => void pick(t)}
            >
              <span className="flex items-center gap-1.5">
                <span
                  className={`inline-block size-1.5 shrink-0 rounded-full ${t.status === 'running' ? 'animate-pulse' : ''}`}
                  style={{ backgroundColor: COLOR[t.status] }}
                  title={t.status}
                />
                <span className="min-w-0 flex-1 truncate text-[13px]" style={{ color: 'var(--color-foreground)' }}>
                  {t.title}
                </span>
                {attachedIds.has(t.id) && (
                  <span className="shrink-0 text-[10px]" style={{ color: 'var(--color-text-mute)' }}>
                    attached
                  </span>
                )}
              </span>
              <span className="truncate pl-3 text-[11px]" style={{ color: 'var(--color-text-mute)' }}>
                {attaching === t.id ? 'Attaching…' : `${relativeTime(Date.parse(t.last_activity) / 1000, Date.now())} · ${t.status} · ${t.project} · ${t.model || '—'}`}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

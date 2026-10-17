import type { ConvoRow } from './api';

export type SessionGroup = { label: string; rows: ConvoRow[] };

const DAY = 86_400_000;

function startOfDay(ms: number): number {
  const d = new Date(ms);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

/** Pinned first, then Today / Yesterday / Last 7 days / Older by last activity. */
export function groupSessions(rows: ConvoRow[], now = Date.now()): SessionGroup[] {
  const today = startOfDay(now);
  const buckets: SessionGroup[] = [
    { label: 'Pinned', rows: [] },
    { label: 'Today', rows: [] },
    { label: 'Yesterday', rows: [] },
    { label: 'Last 7 days', rows: [] },
    { label: 'Older', rows: [] },
  ];
  for (const r of rows) {
    const t = r.last_active_at * 1000;
    const i = r.pinned ? 0 : t >= today ? 1 : t >= today - DAY ? 2 : t >= today - 7 * DAY ? 3 : 4;
    buckets[i].rows.push(r);
  }
  for (const b of buckets) b.rows.sort((a, z) => z.last_active_at - a.last_active_at);
  return buckets.filter((b) => b.rows.length > 0);
}

export function relativeTime(unixSecs: number, now = Date.now()): string {
  const diff = Math.max(0, now - unixSecs * 1000);
  if (diff < 60_000) return 'now';
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m`;
  if (diff < DAY) return `${Math.floor(diff / 3_600_000)}h`;
  if (diff < 7 * DAY) return `${Math.floor(diff / DAY)}d`;
  return new Date(unixSecs * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

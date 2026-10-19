// "In tools" tab (WIN-577, design C): other tools' own threads in one flat list. A thread already in a Chat
// opens that Chat (`in a chat ↔`); any other attaches one first (`chat ›`). No project headers: the project is a
// small tag on each row and a filter chip above the list.
import { useState } from 'react';
import { MoreHorizontalIcon, PinIcon, RefreshCwIcon, SearchIcon, XIcon } from 'lucide-react';

import { nativeApi, t3Api, type NativeTool, type T3Status, type T3Thread, type T3ThreadStatus } from './api';
import { relativeTime } from './groups';
import { useSessions } from './SessionsProvider';
import { ProjectCombobox } from './ProjectCombobox';
import { ConnectionCard } from './t3/ConnectionCard';
import { useNativeSessions } from './useNativeSessions';
import type { T3Sessions } from './useT3Sessions';

const COLOR: Record<T3ThreadStatus, string> = {
  running: 'var(--color-accent)',
  idle: 'var(--color-text-mute)',
  completed: 'var(--color-success)',
  failed: 'var(--color-destructive)',
};
const LEGEND_KEY = 'talky.inToolsLegend';
const mute = { color: 'var(--color-text-mute)' } as const;
const SOURCES = [
  { id: 't3', label: 'T3' },
  { id: 'claude', label: 'Claude' },
  { id: 'hermes', label: 'Hermes' },
] as const;
type Source = (typeof SOURCES)[number]['id'];
const RECENT_MAX = 5;
const RECENT_MS = 3 * 3600_000;
const NATIVE_WHERE: Record<NativeTool, string> = { claude: '~/.claude/projects', hermes: '~/.hermes/state.db' };
const NATIVE_NAME: Record<NativeTool, string> = { claude: 'Claude Code', hermes: 'Hermes' };

function GroupLabel({ children }: { children: string }) {
  return (
    <div className="px-2 pb-0.5 pt-2 font-mono text-[10px] uppercase tracking-wide" style={mute} data-testid="intools-group">
      {children}
    </div>
  );
}

function ThreadRow({ t, busy, disabled, onPick }: { t: T3Thread; busy: boolean; disabled: boolean; onPick: (t: T3Thread) => void }) {
  const linked = !!t.convo_id;
  return (
    <button
      type="button"
      data-testid="t3-thread"
      data-thread-id={t.id}
      data-status={t.status}
      data-linked={linked ? '1' : '0'}
      data-pinned={t.pinned ? '1' : '0'}
      disabled={disabled}
      className="flex w-full flex-col gap-0.5 rounded-md px-2 py-1.5 text-left hover:bg-[var(--color-muted)] disabled:opacity-60"
      onClick={() => onPick(t)}
    >
      <span className="flex items-center gap-1.5">
        <span
          className={`inline-block size-1.5 shrink-0 rounded-full ${t.status === 'running' ? 'animate-pulse' : ''}`}
          style={{ backgroundColor: COLOR[t.status] }}
          title={t.status}
        />
        {t.pinned && <PinIcon size={11} className="shrink-0" style={mute} aria-label="Pinned in T3" />}
        <span className="min-w-0 flex-1 truncate text-[13px]" style={{ color: 'var(--color-foreground)' }} title={t.title}>
          {t.title}
        </span>
        <span className="shrink-0 font-mono text-[10px]" style={mute}>
          {relativeTime(Date.parse(t.last_activity) / 1000)}
        </span>
      </span>
      <span className="flex items-center gap-1.5 pl-3 text-[11px]">
        <span
          data-testid="t3-project-tag"
          className="max-w-[45%] shrink-0 truncate rounded px-1 font-mono text-[10px] leading-4"
          style={{ ...mute, border: '1px solid var(--color-border-soft)' }}
        >
          {t.project}
        </span>
        <span className="flex-1" />
        <span
          data-testid="t3-marker"
          className="shrink-0"
          style={{ color: linked ? 'var(--color-accent)' : 'var(--color-text-dim)' }}
        >
          {busy ? 'Attaching…' : linked ? 'in a chat ↔' : 'chat ›'}
        </span>
      </span>
    </button>
  );
}

export function InTools({ s, onPicked }: { s: T3Sessions; onPicked?: () => void }) {
  const { open, error: openError } = useSessions();
  const [source, setSource] = useState<Source>('t3');
  const [nativeProject, setNativeProject] = useState<string | null>(null);
  const [showAutomated, setShowAutomated] = useState(false);
  const tool: NativeTool | null = source === 't3' ? null : source;
  const native = useNativeSessions(tool);
  const [nativeQuery, setNativeQuery] = useState('');
  const [attaching, setAttaching] = useState<string | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [legend, setLegend] = useState(() => {
    try {
      return localStorage.getItem(LEGEND_KEY) !== 'hidden';
    } catch {
      return true;
    }
  });
  const connected = s.status?.state === 'connected';

  const pick = async (t: T3Thread) => {
    setAttaching(t.id);
    try {
      const convoId = t.convo_id ?? (await (tool ? nativeApi.attach(tool, t.id) : t3Api.attach(t.id))).convo_id;
      await open(convoId, tool ? (tool === 'claude' ? 'claude' : 'hermes') : 't3');
      onPicked?.();
      if (!t.convo_id) void (tool ? native.refresh() : s.refresh(true));
    } catch (e) {
      (tool ? native.setError : s.setError)(`Couldn't open: ${e instanceof Error ? e.message : e}`);
    } finally {
      setAttaching(null);
    }
  };

  const automatedCount = native.sessions?.filter((t) => t.automated).length ?? 0;
  const nativeVisible = native.sessions?.filter((t) => showAutomated || !t.automated) ?? null;
  const needle = nativeQuery.trim().toLowerCase();
  const nativeRows =
    nativeVisible?.filter((t) => (!nativeProject || t.project_id === nativeProject) && (!needle || t.title.toLowerCase().includes(needle))) ?? null;
  const nativeOptions = Object.values(
    (nativeVisible ?? []).reduce<Record<string, { id: string; title: string; count: number }>>((acc, t) => {
      const k = t.project_id ?? t.project;
      (acc[k] ??= { id: k, title: t.project, count: 0 }).count++;
      return acc;
    }, {}),
  ).sort((a, b) => b.count - a.count);
  const options = tool ? nativeOptions : s.projects.filter((p) => p.thread_count > 0).map((p) => ({ id: p.id, title: p.title, count: p.thread_count }));
  const rows = tool ? nativeRows : s.threads;
  const query = tool ? nativeQuery : s.query;
  // Recent (running or touched in the last few hours, max 5) → Pinned → the rest; a thread shows in the first group it fits.
  const recent = tool
    ? [...(nativeRows ?? [])]
        .filter((t) => t.status === 'running' || Date.now() - Date.parse(t.last_activity) < RECENT_MS)
        .sort((a, b) => Date.parse(b.last_activity) - Date.parse(a.last_activity))
        .slice(0, RECENT_MAX)
    : s.recent;
  const recentIds = new Set(recent.map((t) => t.id));
  const rest = rows?.filter((t) => !recentIds.has(t.id)) ?? null;
  const pinned = rest?.filter((t) => t.pinned) ?? [];
  const others = rest?.filter((t) => !t.pinned) ?? [];
  const grouped = recent.length > 0 || pinned.length > 0;
  const err = (tool ? native.error : s.error) ?? openError;
  const showList = tool ? true : connected;
  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="in-tools">
      <div className="flex shrink-0 items-center gap-1 px-2 pt-2">
        <div role="group" aria-label="Tool" className="flex flex-1 gap-1">
          {SOURCES.map((src) => (
            <button
              key={src.id}
              type="button"
              data-testid={`intools-source-${src.id}`}
              aria-pressed={source === src.id}
              className="rounded px-2 py-0.5 font-mono text-[11px]"
              style={{
                border: '1px solid var(--color-border-soft)',
                backgroundColor: source === src.id ? 'var(--color-muted)' : undefined,
                color: source === src.id ? 'var(--color-foreground)' : 'var(--color-text-mute)',
              }}
              onClick={() => {
                setSource(src.id);
                setNativeProject(null);
                setNativeQuery('');
              }}
            >
              {src.label}
            </button>
          ))}
        </div>
        {!tool && connected && (
          <div className="relative">
            <button
              type="button"
              data-testid="t3-menu"
              title="T3 options"
              aria-label="T3 options"
              aria-expanded={menuOpen}
              className="inline-flex size-6 items-center justify-center rounded hover:bg-[var(--color-muted)]"
              style={{ color: 'var(--color-text-dim)' }}
              onClick={() => setMenuOpen((o) => !o)}
            >
              <MoreHorizontalIcon size={13} />
            </button>
            {menuOpen && (
              <button
                type="button"
                data-testid="t3-disconnect"
                className="absolute right-0 top-7 z-10 whitespace-nowrap rounded-md px-3 py-1.5 text-[12px] hover:bg-[var(--color-muted)]"
                style={{ background: 'var(--color-background)', border: '1px solid var(--color-border-soft)', color: 'var(--color-foreground)' }}
                onClick={() => {
                  setMenuOpen(false);
                  void s.disconnect();
                }}
              >
                Disconnect T3
              </button>
            )}
          </div>
        )}
        <button
          type="button"
          data-testid="t3-refresh"
          title="Refresh"
          aria-label="Refresh"
          className="inline-flex size-6 items-center justify-center rounded hover:bg-[var(--color-muted)]"
          style={{ color: 'var(--color-text-dim)' }}
          onClick={() => void (tool ? native.refresh() : s.refresh(true))}
        >
          <RefreshCwIcon size={12} className={(tool ? native.loading : s.loading) ? 'animate-spin' : ''} />
        </button>
      </div>

      {showList && legend && (
        <div
          data-testid="intools-legend"
          className="relative mx-2 mt-2 shrink-0 rounded-md py-1.5 pl-2 pr-6 text-[11px] leading-snug"
          style={{ ...mute, border: '1px solid var(--color-border-soft)' }}
        >
          Threads these tools already have. Open one and Talky creates a Chat linked to it.
          <button
            type="button"
            data-testid="intools-legend-dismiss"
            aria-label="Dismiss"
            className="absolute right-1 top-1 inline-flex size-4 items-center justify-center rounded hover:bg-[var(--color-muted)]"
            onClick={() => {
              setLegend(false);
              try {
                localStorage.setItem(LEGEND_KEY, 'hidden');
              } catch { /* private mode */ }
            }}
          >
            <XIcon size={11} />
          </button>
        </div>
      )}

      {showList && options.length > 0 && (
        <div className="shrink-0 px-2 pt-2" data-testid="intools-chips">
          <ProjectCombobox
            options={options}
            value={tool ? nativeProject : s.filter}
            onChange={(id) => (tool ? setNativeProject(id) : s.setFilter(id))}
          />
        </div>
      )}

      {showList && (
        <div className="relative shrink-0 px-2 pt-2">
          <SearchIcon size={12} className="pointer-events-none absolute left-4 top-1/2 mt-1 -translate-y-1/2" style={mute} />
          <input
            type="search"
            data-testid="intools-search"
            aria-label="Search threads by title"
            placeholder="Search threads"
            value={query}
            className="w-full rounded-md bg-transparent py-1 pl-7 pr-2 text-[13px]"
            style={{ border: '1px solid var(--color-border-soft)', color: 'var(--color-foreground)' }}
            onChange={(e) => (tool ? setNativeQuery(e.target.value) : s.setQuery(e.target.value))}
          />
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto px-1.5 py-2" data-testid="t3-thread-list">
        {!tool && s.status && !connected && <ConnectionCard status={s.status} onStatus={(st: T3Status) => s.onStatus(st)} />}
        {err && (
          <div className="px-2 pb-2 text-[12px]" style={{ color: 'var(--color-destructive)' }} data-testid="t3-error">
            {err}
          </div>
        )}
        {tool && automatedCount > 0 && (
          <label className="flex items-center gap-1.5 px-2 pb-1.5 text-[11px]" style={mute}>
            <input
              type="checkbox"
              data-testid="intools-show-automated"
              checked={showAutomated}
              onChange={(e) => setShowAutomated(e.target.checked)}
            />
            Show automated ({automatedCount})
          </label>
        )}
        {tool && native.sessions && native.sessions.length > 0 && nativeRows?.length === 0 && !native.error && (
          <div className="px-3 py-6 text-center text-[12px] leading-snug" style={mute} data-testid="intools-empty-filtered">
            Only automated sessions here. Tick “Show automated” to see them.
          </div>
        )}
        {tool && native.sessions?.length === 0 && !native.error && (
          <div className="px-3 py-6 text-center text-[12px] leading-snug" style={mute} data-testid="intools-empty">
            No {NATIVE_NAME[tool]} sessions found.
            <br />
            Talky reads them from {NATIVE_WHERE[tool]}.
          </div>
        )}
        {!tool && connected && s.threads?.length === 0 && (
          <div className="px-3 py-6 text-center text-[12px] leading-snug" style={mute} data-testid="intools-empty">
            No T3 threads yet.
            <br />
            Start one in T3 Code and it shows up here; open it to get a Chat.
          </div>
        )}
        {query.trim() && rows?.length === 0 && recent.length === 0 && (
          <div className="px-3 py-6 text-center text-[12px]" style={mute} data-testid="intools-no-match">
            No threads match “{query.trim()}”.
          </div>
        )}
        {[
          ['Recent', recent],
          ['Pinned', pinned],
          [grouped ? 'All threads' : '', others],
        ].map(([label, list]) =>
          (list as T3Thread[]).length === 0 ? null : (
            <div key={label as string}>
              {label && <GroupLabel>{label as string}</GroupLabel>}
              {(list as T3Thread[]).map((t) => (
                <ThreadRow key={t.id} t={t} busy={attaching === t.id} disabled={attaching !== null} onPick={(x) => void pick(x)} />
              ))}
            </div>
          ),
        )}
        {!tool && s.cursor && (
          <button
            type="button"
            data-testid="t3-more"
            className="mt-1 w-full rounded-md px-2 py-1.5 text-center text-[12px] hover:bg-[var(--color-muted)]"
            style={mute}
            onClick={() => void s.loadMore()}
          >
            Show more{s.total > 0 && s.threads ? ` (${s.total - s.threads.length})` : ''}
          </button>
        )}
      </div>
    </div>
  );
}

// Session list (WIN-535): a left rail on desktop, a left sheet on mobile.
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { Drawer } from 'vaul';
import {
  ArchiveIcon,
  ArchiveRestoreIcon,
  ChevronLeftIcon,
  MoreHorizontalIcon,
  PanelLeftIcon,
  PencilIcon,
  PinIcon,
  PinOffIcon,
  PlusIcon,
  RepeatIcon,
  SearchIcon,
  Trash2Icon,
  XIcon,
} from 'lucide-react';

import { Popover, PopoverContent, PopoverTrigger } from '../components/ui/popover';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../components/ui/dialog';
import type { ConvoRow } from './api';
import { groupSessions, relativeTime } from './groups';
import { useSessions } from './SessionsProvider';

const label = {
  fontSize: 10,
  fontWeight: 600,
  letterSpacing: '0.12em',
  color: 'var(--color-text-mute)',
} as const;

// ─── ROW ACTIONS ───────────────────────────────────────────────────────
function RowMenu({
  row,
  onRename,
  onDelete,
  onPicked,
}: {
  row: ConvoRow;
  onRename: () => void;
  onDelete: () => void;
  onPicked?: () => void;
}) {
  const { profiles, open, setPinned, setArchived } = useSessions();
  const [menuOpen, setMenuOpen] = useState(false);
  const [picking, setPicking] = useState(false);
  const choices = profiles.filter((p) => p.name !== '__mcp__');

  const item = (
    key: string,
    icon: React.ReactNode,
    text: string,
    action: () => void,
    danger = false,
  ) => (
    <button
      key={key}
      type="button"
      data-testid={`session-action-${key}`}
      className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-[13px] hover:bg-[var(--color-muted)]"
      style={{ color: danger ? 'var(--color-destructive)' : 'var(--color-foreground)' }}
      onClick={(e) => {
        e.stopPropagation();
        setMenuOpen(false);
        setPicking(false);
        action();
      }}
    >
      {icon}
      <span className="truncate">{text}</span>
    </button>
  );

  return (
    <Popover
      open={menuOpen}
      onOpenChange={(o) => {
        setMenuOpen(o);
        if (!o) setPicking(false);
      }}
    >
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label="Session actions"
          data-testid="session-actions"
          className="session-row-actions inline-flex size-6 shrink-0 items-center justify-center rounded hover:bg-[var(--color-muted)]"
          style={{ color: 'var(--color-text-dim)' }}
          onClick={(e) => e.stopPropagation()}
        >
          <MoreHorizontalIcon size={14} />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-52 p-1" onClick={(e) => e.stopPropagation()}>
        {picking ? (
          <>
            <div className="px-2 pb-1 pt-1.5 font-mono uppercase" style={label}>
              Continue on
            </div>
            {choices.map((p) =>
              item(
                `profile-${p.name}`,
                <span className="size-3.5" />,
                p.label + (p.name === row.profile ? ' (its own)' : ''),
                () => {
                  void open(row.id, p.name);
                  onPicked?.();
                },
              ),
            )}
          </>
        ) : (
          <>
            {item('continue', <RepeatIcon size={14} />, 'Continue on…', () => {
              setMenuOpen(true);
              setPicking(true);
            })}
            {item('rename', <PencilIcon size={14} />, 'Rename', onRename)}
            {item(
              'pin',
              row.pinned ? <PinOffIcon size={14} /> : <PinIcon size={14} />,
              row.pinned ? 'Unpin' : 'Pin',
              () => void setPinned(row.id, !row.pinned),
            )}
            {item(
              'archive',
              row.archived ? <ArchiveRestoreIcon size={14} /> : <ArchiveIcon size={14} />,
              row.archived ? 'Unarchive' : 'Archive',
              () => void setArchived(row.id, !row.archived),
            )}
            {item('delete', <Trash2Icon size={14} />, 'Delete…', onDelete, true)}
          </>
        )}
      </PopoverContent>
    </Popover>
  );
}

// ─── ROW ───────────────────────────────────────────────────────────────
function StatusDot({ status }: { status: 'idle' | 'working' | 'failed' }) {
  const color =
    status === 'working'
      ? 'var(--color-accent)'
      : status === 'failed'
        ? 'var(--color-destructive)'
        : 'var(--color-success)';
  return (
    <span
      data-testid="session-status"
      data-status={status}
      title={status === 'working' ? 'Working…' : status === 'failed' ? 'Failed' : 'Current session'}
      className={`inline-block size-1.5 shrink-0 rounded-full ${status === 'working' ? 'animate-pulse' : ''}`}
      style={{ backgroundColor: color }}
    />
  );
}

function SessionRow({
  row,
  onPicked,
  onDelete,
}: {
  row: ConvoRow;
  onPicked?: () => void;
  onDelete: (row: ConvoRow) => void;
}) {
  const { openId, current, open, rename } = useSessions();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(row.title);
  const inputRef = useRef<HTMLInputElement>(null);
  const isOpen = openId === row.id;
  const isCurrent = current?.id === row.id;

  useEffect(() => {
    if (editing) inputRef.current?.select();
  }, [editing]);

  const commit = () => {
    setEditing(false);
    const t = draft.trim();
    if (t && t !== row.title) void rename(row.id, t);
  };
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') commit();
    if (e.key === 'Escape') {
      setDraft(row.title);
      setEditing(false);
    }
  };

  return (
    <div
      role="button"
      tabIndex={0}
      data-testid="session-row"
      data-convo-id={row.id}
      data-open={isOpen || undefined}
      aria-current={isOpen || undefined}
      className="session-row group flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 outline-none focus-visible:ring-1 focus-visible:ring-[var(--color-ring)]"
      style={{
        backgroundColor: isOpen ? 'var(--color-muted)' : undefined,
      }}
      onClick={() => {
        if (editing) return;
        if (!isOpen) void open(row.id);
        onPicked?.();
      }}
      onKeyDown={(e) => {
        if (editing) return;
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          if (!isOpen) void open(row.id);
          onPicked?.();
        }
      }}
    >
      <div className="min-w-0 flex-1">
        {editing ? (
          <input
            ref={inputRef}
            data-testid="session-rename-input"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKey}
            onBlur={commit}
            onClick={(e) => e.stopPropagation()}
            className="w-full rounded bg-transparent px-1 text-[13px] outline-none"
            style={{ border: '1px solid var(--color-border)' }}
          />
        ) : (
          <div
            className="truncate text-[13px]"
            data-testid="session-title"
            style={{ color: isOpen ? 'var(--color-foreground)' : 'var(--color-text-dim)' }}
            title={row.title || 'Untitled'}
          >
            {row.title || 'Untitled'}
          </div>
        )}
        <div className="mt-0.5 flex items-center gap-1.5">
          {isCurrent && <StatusDot status={current!.status} />}
          <span
            data-testid="session-backend"
            className="rounded px-1 font-mono text-[10px] leading-4"
            style={{ color: 'var(--color-text-mute)', border: '1px solid var(--color-border-soft)' }}
          >
            {row.profile}
          </span>
          {row.snippet && (
            <span className="truncate text-[11px]" style={{ color: 'var(--color-text-mute)' }} data-testid="session-snippet">
              {row.snippet}
            </span>
          )}
        </div>
      </div>
      <span className="shrink-0 self-start pt-0.5 font-mono text-[10px]" style={{ color: 'var(--color-text-mute)' }}>
        {relativeTime(row.last_active_at)}
      </span>
      <RowMenu
        row={row}
        onRename={() => {
          setDraft(row.title);
          setEditing(true);
        }}
        onDelete={() => onDelete(row)}
        onPicked={onPicked}
      />
    </div>
  );
}

// ─── LIST ──────────────────────────────────────────────────────────────
function SessionList({ onPicked, onCollapse }: { onPicked?: () => void; onCollapse?: () => void }) {
  const { rows, query, setQuery, showArchived, setShowArchived, newChat, remove, error } = useSessions();
  const [confirming, setConfirming] = useState<ConvoRow | null>(null);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 60_000);
    return () => clearInterval(t);
  }, []);
  const groups = useMemo(() => groupSessions(rows, now), [rows, now]);

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="session-sidebar">
      <div className="flex h-9 shrink-0 items-center gap-1 pl-3 pr-1" style={{ borderBottom: '1px solid var(--color-border-soft)' }}>
        <span className="flex-1 font-mono uppercase" style={label}>
          Sessions
        </span>
        <button
          type="button"
          data-testid="session-new"
          title="New session"
          aria-label="New session"
          className="inline-flex size-7 items-center justify-center rounded hover:bg-[var(--color-muted)]"
          style={{ color: 'var(--color-text-dim)' }}
          onClick={() => {
            void newChat();
            onPicked?.();
          }}
        >
          <PlusIcon size={15} />
        </button>
        {onCollapse && (
          <button
            type="button"
            title="Hide sessions"
            aria-label="Hide sessions"
            className="inline-flex size-7 items-center justify-center rounded hover:bg-[var(--color-muted)]"
            style={{ color: 'var(--color-text-dim)' }}
            onClick={onCollapse}
          >
            <ChevronLeftIcon size={15} />
          </button>
        )}
      </div>

      <div className="shrink-0 px-2 pt-2">
        <div
          className="flex items-center gap-1.5 rounded-md px-2"
          style={{ border: '1px solid var(--color-border)', backgroundColor: 'var(--color-background)' }}
        >
          <SearchIcon size={13} style={{ color: 'var(--color-text-mute)' }} className="shrink-0" />
          <input
            data-testid="session-search"
            type="search"
            placeholder="Search sessions"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="h-8 w-full min-w-0 bg-transparent text-[13px] outline-none"
          />
          {query && (
            <button type="button" aria-label="Clear search" onClick={() => setQuery('')} style={{ color: 'var(--color-text-mute)' }}>
              <XIcon size={13} />
            </button>
          )}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-1.5 py-2" data-testid="session-list">
        {error && (
          <div className="px-2 pb-2 text-[12px]" style={{ color: 'var(--color-destructive)' }} data-testid="session-error">
            {error}
          </div>
        )}
        {groups.length === 0 && (
          <div className="px-2 py-6 text-center text-[12px]" style={{ color: 'var(--color-text-mute)' }} data-testid="session-empty">
            {query ? 'No sessions match.' : showArchived ? 'No archived sessions.' : 'No sessions yet. Start a call to begin one.'}
          </div>
        )}
        {groups.map((g) => (
          <div key={g.label} className="mb-2" data-testid="session-group" data-group={g.label}>
            <div className="px-2 pb-1 pt-1 font-mono uppercase" style={label}>
              {g.label}
            </div>
            {g.rows.map((r) => (
              <SessionRow key={r.id} row={r} onPicked={onPicked} onDelete={setConfirming} />
            ))}
          </div>
        ))}
      </div>

      <div className="shrink-0 px-3 py-2" style={{ borderTop: '1px solid var(--color-border-soft)' }}>
        <button
          type="button"
          data-testid="session-show-archived"
          className="text-[12px] hover:underline"
          style={{ color: 'var(--color-text-mute)' }}
          onClick={() => setShowArchived(!showArchived)}
        >
          {showArchived ? '← Back to sessions' : 'Archived sessions'}
        </button>
      </div>

      <Dialog open={!!confirming} onOpenChange={(o) => !o && setConfirming(null)}>
        <DialogContent data-testid="session-delete-dialog">
          <DialogHeader>
            <DialogTitle>Delete session?</DialogTitle>
            <DialogDescription>
              “{confirming?.title || 'Untitled'}” and its transcript will be removed from this machine. This can’t be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <button
              type="button"
              className="rounded-md px-3 py-1.5 text-[13px]"
              style={{ border: '1px solid var(--color-border)' }}
              onClick={() => setConfirming(null)}
            >
              Cancel
            </button>
            <button
              type="button"
              data-testid="session-delete-confirm"
              className="rounded-md px-3 py-1.5 text-[13px] font-medium"
              style={{ backgroundColor: 'var(--color-destructive)', color: 'var(--color-destructive-foreground)' }}
              onClick={() => {
                if (confirming) void remove(confirming.id);
                setConfirming(null);
              }}
            >
              Delete
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ─── SHELLS ────────────────────────────────────────────────────────────
const RAIL_KEY = 'talky.sessionsRail';

export function useRailOpen(): [boolean, (v: boolean) => void] {
  const [open, setOpen] = useState(() => {
    try {
      return localStorage.getItem(RAIL_KEY) !== 'closed';
    } catch {
      return true;
    }
  });
  const set = (v: boolean) => {
    setOpen(v);
    try {
      localStorage.setItem(RAIL_KEY, v ? 'open' : 'closed');
    } catch { /* private mode */ }
  };
  return [open, set];
}

/** Desktop: a fixed-width left rail. */
export function SessionRail({ onCollapse }: { onCollapse: () => void }) {
  return (
    <aside
      className="hidden h-full w-64 shrink-0 sm:flex sm:flex-col"
      style={{ borderRight: '1px solid var(--color-border-soft)', backgroundColor: 'var(--color-card)' }}
      data-testid="session-rail"
    >
      <SessionList onCollapse={onCollapse} />
    </aside>
  );
}

/** Mobile: a left sheet. */
export function SessionDrawer({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  return (
    <Drawer.Root open={open} onOpenChange={onOpenChange} direction="left">
      <Drawer.Portal>
        <Drawer.Overlay className="fixed inset-0 z-50 bg-black/50" />
        <Drawer.Content
          className="fixed inset-y-0 left-0 z-50 flex w-[85vw] max-w-80 flex-col outline-none"
          style={{ backgroundColor: 'var(--color-card)' }}
          data-testid="session-drawer"
        >
          <Drawer.Title className="sr-only">Sessions</Drawer.Title>
          <Drawer.Description className="sr-only">Browse, search and reopen past sessions.</Drawer.Description>
          <SessionList onPicked={() => onOpenChange(false)} />
        </Drawer.Content>
      </Drawer.Portal>
    </Drawer.Root>
  );
}

export function SessionsToggle({ onClick, className }: { onClick: () => void; className?: string }) {
  return (
    <button
      type="button"
      data-testid="sessions-toggle"
      title="Sessions"
      aria-label="Sessions"
      className={`inline-flex size-7 shrink-0 items-center justify-center rounded hover:bg-[var(--color-muted)] ${className ?? ''}`}
      style={{ color: 'var(--color-text-dim)' }}
      onClick={onClick}
    >
      <PanelLeftIcon size={15} />
    </button>
  );
}

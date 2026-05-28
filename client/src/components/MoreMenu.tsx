import { useEffect, useState } from 'react';
import {
  Button,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  useTheme,
} from '@pipecat-ai/voice-ui-kit';
import { Drawer } from 'vaul';
import {
  ArrowLeftIcon,
  CheckIcon,
  ChevronRightIcon,
  ClipboardIcon,
  DownloadIcon,
  MoonIcon,
  MoreVerticalIcon,
  SunIcon,
} from 'lucide-react';

import { useTalkyMessages } from '../messages/useTalkyMessages';
import {
  downloadBlob,
  timestamp,
  toCsv,
  toJsonl,
  toMarkdown,
} from '../messages/export';

const itemReset =
  'focus:bg-[var(--color-panel-3)] focus:text-foreground ' +
  'data-[highlighted]:bg-[var(--color-panel-3)] data-[highlighted]:text-foreground ' +
  'data-[state=open]:bg-[var(--color-panel-3)] data-[state=open]:text-foreground ' +
  'py-3 text-sm';

function useMediaQuery(query: string) {
  const [match, setMatch] = useState(() =>
    typeof window === 'undefined' ? false : window.matchMedia(query).matches,
  );
  useEffect(() => {
    const mq = window.matchMedia(query);
    const handler = (e: MediaQueryListEvent) => setMatch(e.matches);
    mq.addEventListener('change', handler);
    setMatch(mq.matches);
    return () => mq.removeEventListener('change', handler);
  }, [query]);
  return match;
}

type SheetView = 'root' | 'download';

interface Actions {
  copied: boolean;
  hasMessages: boolean;
  isDark: boolean;
  onCopy: () => void;
  onDownload: (fmt: 'md' | 'jsonl' | 'csv') => void;
  onToggleTheme: () => void;
}

// ---------- Mobile: drill-in bottom sheet ----------

const MobileSheet = ({
  open,
  onOpenChange,
  actions,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  actions: Actions;
}) => {
  const [view, setView] = useState<SheetView>('root');

  useEffect(() => {
    if (!open) {
      // Reset to root after close animation
      const t = setTimeout(() => setView('root'), 250);
      return () => clearTimeout(t);
    }
  }, [open]);

  const close = () => onOpenChange(false);

  return (
    <Drawer.Root open={open} onOpenChange={onOpenChange}>
      <Drawer.Portal>
        <Drawer.Overlay className="fixed inset-0 z-50 bg-black/50" />
        <Drawer.Content
          className="fixed inset-x-0 bottom-0 z-50 flex max-h-[85vh] flex-col rounded-t-2xl outline-none"
          style={{ backgroundColor: 'var(--color-card)' }}
        >
          <div
            aria-hidden
            className="mx-auto my-3 h-1.5 w-12 shrink-0 rounded-full"
            style={{ backgroundColor: 'var(--color-border-soft)' }}
          />
          <Drawer.Title className="sr-only">
            {view === 'root' ? 'More options' : 'Download transcript'}
          </Drawer.Title>
          <Drawer.Description className="sr-only">
            {view === 'root'
              ? 'Copy, download, or change theme.'
              : 'Choose a transcript format.'}
          </Drawer.Description>

          {view === 'root' && (
            <RootView
              actions={actions}
              onDrillDownload={() => setView('download')}
              onAfter={close}
            />
          )}

          {view === 'download' && (
            <DownloadView
              actions={actions}
              onBack={() => setView('root')}
              onAfter={close}
            />
          )}
        </Drawer.Content>
      </Drawer.Portal>
    </Drawer.Root>
  );
};

const sheetRowClass =
  'w-full text-left flex items-center gap-3 px-5 py-4 ' +
  'active:bg-[var(--color-panel-3)] disabled:opacity-40';

const RootView = ({
  actions,
  onDrillDownload,
  onAfter,
}: {
  actions: Actions;
  onDrillDownload: () => void;
  onAfter: () => void;
}) => {
  const { copied, hasMessages, isDark, onCopy, onToggleTheme } = actions;
  return (
    <div
      className="overflow-y-auto"
      style={{ paddingBottom: 'max(0.5rem, env(safe-area-inset-bottom))' }}
    >
      <button
        type="button"
        onClick={() => {
          onCopy();
          // Stay open briefly so user sees the "Copied" state — don't auto-close
        }}
        disabled={!hasMessages}
        className={sheetRowClass}
      >
        {copied ? (
          <CheckIcon size={18} style={{ color: 'var(--color-success)' }} />
        ) : (
          <ClipboardIcon size={18} />
        )}
        <span className="flex-1 text-[15px]">
          {copied ? 'Copied transcript' : 'Copy transcript'}
        </span>
      </button>

      <button
        type="button"
        onClick={onDrillDownload}
        disabled={!hasMessages}
        className={sheetRowClass}
      >
        <DownloadIcon size={18} />
        <span className="flex-1 text-[15px]">Download</span>
        <ChevronRightIcon
          size={18}
          style={{ color: 'var(--color-text-mute)' }}
        />
      </button>

      <div
        className="my-1"
        style={{ borderTop: '1px solid var(--color-border-soft)' }}
      />

      <button
        type="button"
        onClick={() => {
          onToggleTheme();
          onAfter();
        }}
        className={sheetRowClass}
      >
        {isDark ? <SunIcon size={18} /> : <MoonIcon size={18} />}
        <span className="flex-1 text-[15px]">
          Switch to {isDark ? 'light' : 'dark'}
        </span>
      </button>
    </div>
  );
};

const DownloadView = ({
  actions,
  onBack,
  onAfter,
}: {
  actions: Actions;
  onBack: () => void;
  onAfter: () => void;
}) => {
  const pick = (fmt: 'md' | 'jsonl' | 'csv') => {
    actions.onDownload(fmt);
    onAfter();
  };
  return (
    <div
      className="overflow-y-auto"
      style={{ paddingBottom: 'max(0.5rem, env(safe-area-inset-bottom))' }}
    >
      <button
        type="button"
        onClick={onBack}
        className={`${sheetRowClass} font-mono uppercase`}
        style={{
          fontSize: 11,
          letterSpacing: '0.08em',
          color: 'var(--color-text-mute)',
        }}
      >
        <ArrowLeftIcon size={16} />
        <span>Back</span>
      </button>
      <div
        className="px-5 pb-2 font-mono uppercase"
        style={{
          fontSize: 10,
          fontWeight: 600,
          letterSpacing: '0.08em',
          color: 'var(--color-text-mute)',
        }}
      >
        Download transcript
      </div>

      <button type="button" onClick={() => pick('md')} className={sheetRowClass}>
        <DownloadIcon size={18} />
        <span className="flex-1 text-[15px]">Markdown</span>
        <span className="font-mono text-[11px] opacity-50">.md</span>
      </button>
      <button type="button" onClick={() => pick('jsonl')} className={sheetRowClass}>
        <DownloadIcon size={18} />
        <span className="flex-1 text-[15px]">JSON Lines</span>
        <span className="font-mono text-[11px] opacity-50">.jsonl</span>
      </button>
      <button type="button" onClick={() => pick('csv')} className={sheetRowClass}>
        <DownloadIcon size={18} />
        <span className="flex-1 text-[15px]">CSV</span>
        <span className="font-mono text-[11px] opacity-50">.csv</span>
      </button>
    </div>
  );
};

// ---------- Desktop: dropdown ----------

const DesktopMenu = ({ actions }: { actions: Actions }) => {
  const { copied, hasMessages, isDark, onCopy, onDownload, onToggleTheme } = actions;
  return (
    <DropdownMenuContent
      align="end"
      className="min-w-[260px] max-h-[80vh] overflow-y-auto"
    >
      <DropdownMenuItem
        onClick={onCopy}
        disabled={!hasMessages}
        className={itemReset}
      >
        {copied ? (
          <CheckIcon size={14} style={{ color: 'var(--color-success)' }} />
        ) : (
          <ClipboardIcon size={14} />
        )}
        <span>{copied ? 'Copied transcript' : 'Copy transcript'}</span>
      </DropdownMenuItem>

      <DropdownMenuLabel
        className="font-mono uppercase pt-2"
        style={{
          fontSize: 10,
          fontWeight: 600,
          letterSpacing: '0.08em',
          color: 'var(--color-text-mute)',
        }}
      >
        Download
      </DropdownMenuLabel>
      <DropdownMenuItem
        onClick={() => onDownload('md')}
        disabled={!hasMessages}
        className={itemReset}
      >
        <DownloadIcon size={14} />
        <span className="flex-1">Markdown</span>
        <span className="font-mono text-[10px] opacity-50">.md</span>
      </DropdownMenuItem>
      <DropdownMenuItem
        onClick={() => onDownload('jsonl')}
        disabled={!hasMessages}
        className={itemReset}
      >
        <DownloadIcon size={14} />
        <span className="flex-1">JSON Lines</span>
        <span className="font-mono text-[10px] opacity-50">.jsonl</span>
      </DropdownMenuItem>
      <DropdownMenuItem
        onClick={() => onDownload('csv')}
        disabled={!hasMessages}
        className={itemReset}
      >
        <DownloadIcon size={14} />
        <span className="flex-1">CSV</span>
        <span className="font-mono text-[10px] opacity-50">.csv</span>
      </DropdownMenuItem>

      <DropdownMenuSeparator />

      <DropdownMenuItem onClick={onToggleTheme} className={itemReset}>
        {isDark ? <SunIcon size={14} /> : <MoonIcon size={14} />}
        <span>Switch to {isDark ? 'light' : 'dark'}</span>
      </DropdownMenuItem>
    </DropdownMenuContent>
  );
};

// ---------- Public component ----------

export const MoreMenu = (_props: { showVoiceProfile?: boolean } = {}) => {
  const messages = useTalkyMessages();
  const { resolvedTheme, setTheme } = useTheme();
  const [copied, setCopied] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);
  const isNarrow = useMediaQuery('(max-width: 640px)');

  const isDark = resolvedTheme === 'dark';
  const hasMessages = messages.length > 0;

  const handleCopy = () => {
    navigator.clipboard.writeText(toMarkdown(messages)).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    });
  };

  const dl = (fmt: 'md' | 'jsonl' | 'csv') => {
    const ts = timestamp();
    if (fmt === 'md') downloadBlob(toMarkdown(messages), `transcript-${ts}.md`, 'text/markdown');
    if (fmt === 'jsonl') downloadBlob(toJsonl(messages), `transcript-${ts}.jsonl`, 'application/jsonl');
    if (fmt === 'csv') downloadBlob(toCsv(messages), `transcript-${ts}.csv`, 'text/csv');
  };

  const actions: Actions = {
    copied,
    hasMessages,
    isDark,
    onCopy: handleCopy,
    onDownload: dl,
    onToggleTheme: () => setTheme(isDark ? 'light' : 'dark'),
  };

  if (isNarrow) {
    return (
      <>
        <Button
          variant="secondary"
          size="lg"
          title="More options"
          aria-label="More options"
          onClick={() => setSheetOpen(true)}
        >
          <MoreVerticalIcon size={16} />
        </Button>
        <MobileSheet
          open={sheetOpen}
          onOpenChange={setSheetOpen}
          actions={actions}
        />
      </>
    );
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="secondary"
          size="lg"
          title="More options"
          aria-label="More options"
        >
          <MoreVerticalIcon size={16} />
        </Button>
      </DropdownMenuTrigger>
      <DesktopMenu actions={actions} />
    </DropdownMenu>
  );
};

import { useEffect, useState } from 'react';
import { Drawer } from 'vaul';
import { ChevronDownIcon, SlidersHorizontalIcon } from 'lucide-react';

import { SessionControls } from './SessionControls';

// Header trigger + bottom-sheet panel containing the existing LLM and Voice
// pickers stacked vertically. Mobile only — desktop renders the pickers inline.
export const SessionSheet = ({ currentLabel }: { currentLabel?: string }) => {
  const [open, setOpen] = useState(false);

  // Close the sheet on viewport widen (the desktop layout shows pickers inline).
  useEffect(() => {
    const mq = window.matchMedia('(min-width: 641px)');
    const handler = (e: MediaQueryListEvent) => e.matches && setOpen(false);
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  }, []);

  return (
    <Drawer.Root open={open} onOpenChange={setOpen}>
      <Drawer.Trigger asChild>
        <button
          type="button"
          aria-label="Session settings"
          title="Session settings"
          className="flex items-center gap-1.5 px-2.5 h-9 w-full min-w-0 text-[13px] font-medium text-foreground bg-transparent transition-colors disabled:opacity-50 cursor-pointer rounded-md"
          style={{ border: '1px solid var(--color-border)' }}
        >
          <span className="truncate flex-1 text-left">{currentLabel || 'Session'}</span>
          <SlidersHorizontalIcon
            size={13}
            style={{ color: 'var(--color-text-mute)' }}
            className="shrink-0"
          />
        </button>
      </Drawer.Trigger>
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
          <Drawer.Title
            className="px-5 pb-3 font-mono uppercase shrink-0"
            style={{
              fontSize: 11,
              fontWeight: 600,
              letterSpacing: '0.08em',
              color: 'var(--color-text-mute)',
              borderBottom: '1px solid var(--color-border-soft)',
            }}
          >
            Session
          </Drawer.Title>
          <Drawer.Description className="sr-only">
            Configure the assistant and voice for this session.
          </Drawer.Description>

          <div
            className="overflow-y-auto overscroll-contain px-5 py-4"
            style={{ paddingBottom: 'max(1.25rem, env(safe-area-inset-bottom))' }}
          >
            <SessionControls />
          </div>
        </Drawer.Content>
      </Drawer.Portal>
    </Drawer.Root>
  );
};


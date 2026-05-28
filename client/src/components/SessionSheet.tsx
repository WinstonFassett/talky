import { useEffect, useState } from 'react';
import { Drawer } from 'vaul';
import { ChevronDownIcon, SlidersHorizontalIcon } from 'lucide-react';

import { UserAudioControl } from '@pipecat-ai/voice-ui-kit';

import { LLMProfileSelect } from './LLMProfileSelect';
import { VoiceProfileSelect } from './VoiceProfileSelect';

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
            className="overflow-y-auto overscroll-contain px-5 py-4 flex flex-col gap-4"
            style={{ paddingBottom: 'max(1.25rem, env(safe-area-inset-bottom))' }}
          >
            <Row label="Assistant">
              <LLMProfileSelect />
            </Row>
            <Row label="Voice">
              <VoiceProfileSelect />
            </Row>
            <Row label="Audio" stretch={false}>
              <div className="flex w-full [&>div]:w-full">
                <UserAudioControl
                  size="md"
                  variant="ghost"
                  noVisualizer={false}
                  classNames={{ button: 'flex-1' }}
                  visualizerProps={{ barCount: 32 }}
                />
              </div>
            </Row>
          </div>
        </Drawer.Content>
      </Drawer.Portal>
    </Drawer.Root>
  );
};

const Row = ({
  label,
  children,
  stretch = true,
}: {
  label: string;
  children: React.ReactNode;
  stretch?: boolean;
}) => (
  <div className="flex flex-col gap-1.5">
    <div
      className="font-mono uppercase"
      style={{
        fontSize: 10,
        fontWeight: 600,
        letterSpacing: '0.08em',
        color: 'var(--color-text-mute)',
      }}
    >
      {label}
    </div>
    <div
      className={
        stretch
          ? 'flex [&>*]:flex-1 [&_button]:w-full [&_button]:justify-between'
          : 'flex'
      }
    >
      {children}
    </div>
  </div>
);

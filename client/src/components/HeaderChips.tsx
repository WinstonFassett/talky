import { useState, type ReactNode } from 'react';
import { Drawer } from 'vaul';
import { ChevronDownIcon, Link2Icon } from 'lucide-react';

import { LLMProfileSelect } from './LLMProfileSelect';
import { VoiceProfileSelect } from './VoiceProfileSelect';
import { PickerTrigger } from './PickerTrigger';
import { useVoiceProfiles } from './useVoiceProfiles';

// Header row 2 (WIN-578): `by <backend> ▾` · `<model> ▾` · `🎙 <voice> ▾` + a link
// chip. Desktop: each chip is the existing picker as a popover. Phone (isNarrow):
// each chip opens a bottom sheet holding the same existing picker.

export type ActiveProfileInfo = { label: string; model?: string | null };

function ChipSheet({
  testId,
  title,
  chip,
  children,
}: {
  testId: string;
  title: string;
  chip: ReactNode;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Drawer.Root open={open} onOpenChange={setOpen}>
      <Drawer.Trigger asChild>
        <PickerTrigger variant="chip" open={open} data-testid={testId} title={title} className="shrink-0" style={{ maxWidth: '60vw' }}>
          {chip}
          <ChevronDownIcon size={11} style={{ color: 'var(--color-text-mute)' }} className="shrink-0" />
        </PickerTrigger>
      </Drawer.Trigger>
      <Drawer.Portal>
        <Drawer.Overlay className="fixed inset-0 z-50 bg-black/50" />
        <Drawer.Content
          data-testid="chip-sheet"
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
            {title}
          </Drawer.Title>
          <Drawer.Description className="sr-only">{title}</Drawer.Description>
          <div
            className="overflow-y-auto overscroll-contain px-5 py-4 [&_button]:w-full [&_button]:justify-between"
            style={{ paddingBottom: 'max(1.25rem, env(safe-area-inset-bottom))' }}
          >
            {children}
          </div>
        </Drawer.Content>
      </Drawer.Portal>
    </Drawer.Root>
  );
}

export const HeaderChips = ({
  isNarrow,
  profile,
  linkLabel,
}: {
  isNarrow: boolean;
  profile?: ActiveProfileInfo;
  /** Set when the open chat is linked to a tool thread, e.g. "T3". */
  linkLabel?: string | null;
}) => {
  const { voices, activeVoice } = useVoiceProfiles();
  const voice = voices.find((v) => v.name === activeVoice) ?? voices[0];
  const voiceLabel = voice ? voice.description || voice.name : '';

  return (
    <div
      data-testid="header-chips"
      className="flex items-center gap-1.5 shrink-0 px-2 h-8 overflow-x-auto border-b"
      style={{ borderColor: 'var(--color-border-soft)', backgroundColor: 'var(--color-card)' }}
    >
      {isNarrow ? (
        <>
          {profile && (
            <ChipSheet testId="chip-backend" title="Answered by" chip={<span className="truncate">by {profile.label}</span>}>
              <LLMProfileSelect />
            </ChipSheet>
          )}
          {profile?.model && (
            <ChipSheet testId="chip-model" title="Model" chip={<span className="truncate">{profile.model}</span>}>
              <LLMProfileSelect />
            </ChipSheet>
          )}
          {voiceLabel && (
            <ChipSheet
              testId="chip-voice"
              title="Voice"
              chip={
                <>
                  <span aria-hidden="true">🎙</span>
                  <span className="truncate">{voiceLabel}</span>
                </>
              }
            >
              <VoiceProfileSelect />
            </ChipSheet>
          )}
        </>
      ) : (
        <>
          <LLMProfileSelect variant="chip" chipKind="backend" />
          <LLMProfileSelect variant="chip" chipKind="model" />
          <VoiceProfileSelect variant="chip" />
        </>
      )}
      {linkLabel && (
        <span
          data-testid="chip-link"
          className="flex items-center gap-1 px-2.5 h-6 font-mono rounded-full shrink-0"
          style={{ fontSize: 11, letterSpacing: '0.04em', color: 'var(--color-text-dim)', backgroundColor: 'var(--color-panel-2)' }}
        >
          <Link2Icon size={11} aria-hidden="true" />
          {linkLabel}
        </span>
      )}
    </div>
  );
};

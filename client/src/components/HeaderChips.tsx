import { useState, type ReactNode } from 'react';
import { Drawer } from 'vaul';
import { CheckIcon, ChevronDownIcon, Link2Icon } from 'lucide-react';

import { Popover, PopoverContent, PopoverTrigger } from './ui/popover';
import { PickerTrigger } from './PickerTrigger';
import { profileTitle, useLLMProfiles } from './useLLMProfiles';
import { useVoiceProfiles } from './useVoiceProfiles';

// Header row 2 (WIN-579): ONE profile chip (name ▾) + the link chip. The chip opens
// the profile: switch it, and see its backend, model and voice (read-only). Desktop:
// popover. Phone (isNarrow): bottom sheet.

const label = { fontSize: 10, fontWeight: 600, letterSpacing: '0.08em', color: 'var(--color-text-mute)' } as const;

function Detail({ name, value, testId }: { name: string; value: string; testId: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1">
      <span className="font-mono uppercase shrink-0" style={label}>
        {name}
      </span>
      <span data-testid={testId} className="truncate text-[13px]" style={{ color: 'var(--color-foreground)' }}>
        {value}
      </span>
    </div>
  );
}

const ready = (st: { status: string; reason: string } | undefined, what?: string) =>
  `${what ?? ''} ${st ? (st.status === 'ready' ? '✓ ready' : `✗ ${st.reason || st.status}`) : ''}`.trim();

function ProfilePanel({ onDone, linkLabel }: { onDone: () => void; linkLabel?: string | null }) {
  const { profiles, current, switching, error, switchTo } = useLLMProfiles();
  const { voices, activeVoice } = useVoiceProfiles();
  const voice = voices.find((v) => v.name === activeVoice) ?? voices[0];
  return (
    <div data-testid="profile-panel" className="flex flex-col gap-3">
      {voice && (
        <div className="border-b pb-2" style={{ borderColor: 'var(--color-border-soft)' }} data-testid="profile-voice-block">
          <Detail name="Voice" value={voice.description || voice.name} testId="profile-voice" />
          <Detail name="STT" value={ready(voice.sttStatus, voice.stt)} testId="profile-stt" />
          <Detail name="TTS" value={ready(voice.ttsStatus, voice.tts)} testId="profile-tts" />
        </div>
      )}
      <div className="flex max-h-[50vh] flex-col gap-0.5 overflow-y-auto" role="listbox" aria-label="Talky profile">
        {profiles.map((p) => {
          const selected = p.name === current?.name;
          const unhealthy = p.healthy === false;
          return (
            <button
              key={p.name}
              type="button"
              role="option"
              aria-selected={selected}
              data-testid="profile-option"
              data-profile={p.name}
              disabled={unhealthy || switching}
              onClick={() => {
                void switchTo(p.name);
                onDone();
              }}
              className="flex items-center gap-2 rounded-md px-2 py-2 text-left text-[13px] hover:bg-[var(--color-muted)] disabled:opacity-50"
              style={{ color: 'var(--color-foreground)' }}
            >
              <span
                className="inline-block size-1.5 shrink-0 rounded-full"
                style={{ backgroundColor: unhealthy ? 'var(--color-warning)' : p.healthy === null ? 'var(--color-text-mute)' : 'var(--color-success)' }}
              />
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium">{profileTitle(p)}</span>
                <span data-testid="profile-summary" className="block truncate font-mono text-[11px]" style={{ color: 'var(--color-text-mute)' }}>
                  {[p.backend, p.model].filter(Boolean).join(' · ') || p.description}
                </span>
                {p.voice_profile && (
                  <span data-testid="profile-row-voice" className="block truncate font-mono text-[11px]" style={{ color: 'var(--color-text-mute)' }}>
                    voice: {p.voice?.description || p.voice_profile} {p.voice ? (p.voice.ready ? '✓' : '✗') : ''}
                  </span>
                )}
              </span>
              {selected && <CheckIcon size={13} style={{ color: 'var(--color-accent)' }} />}
            </button>
          );
        })}
      </div>
      {error && (
        <div className="text-[12px]" style={{ color: 'var(--color-destructive)' }}>
          {error}
        </div>
      )}
      {linkLabel && (
        <div className="text-[11px]" data-testid="profile-link-note" style={{ color: 'var(--color-text-mute)' }}>
          This chat is linked to {linkLabel}. Switching profile does not re-link it.
        </div>
      )}
    </div>
  );
}

function ProfileChip({ isNarrow, linkLabel }: { isNarrow: boolean; linkLabel?: string | null }) {
  const [open, setOpen] = useState(false);
  const { current } = useLLMProfiles();
  const { voices, activeVoice } = useVoiceProfiles();
  const voice = voices.find((v) => v.name === activeVoice) ?? voices[0];
  if (!current) return null;
  const trigger: ReactNode = (
    <PickerTrigger variant="chip" open={open} data-testid="chip-profile" title={`Talky profile: ${profileTitle(current)}`} className="shrink min-w-0">
      <span className="truncate" data-testid="chip-profile-label">
        {profileTitle(current)}
      </span>
      {voice && (
        <span className="truncate font-normal" data-testid="chip-voice" style={{ color: 'var(--color-text-mute)' }}>
          · {voice.description || voice.name}
        </span>
      )}
      <ChevronDownIcon size={11} style={{ color: 'var(--color-text-mute)' }} className="shrink-0" />
    </PickerTrigger>
  );
  if (!isNarrow) {
    return (
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>{trigger}</PopoverTrigger>
        <PopoverContent align="start" className="w-80 p-2" data-testid="chip-popover">
          <ProfilePanel onDone={() => setOpen(false)} linkLabel={linkLabel} />
        </PopoverContent>
      </Popover>
    );
  }
  return (
    <Drawer.Root open={open} onOpenChange={setOpen}>
      <Drawer.Trigger asChild>{trigger}</Drawer.Trigger>
      <Drawer.Portal>
        <Drawer.Overlay className="fixed inset-0 z-50 bg-black/50" />
        <Drawer.Content
          data-testid="chip-sheet"
          className="fixed inset-x-0 bottom-0 z-50 flex max-h-[85vh] flex-col rounded-t-2xl outline-none"
          style={{ backgroundColor: 'var(--color-card)' }}
        >
          <div aria-hidden className="mx-auto my-3 h-1.5 w-12 shrink-0 rounded-full" style={{ backgroundColor: 'var(--color-border-soft)' }} />
          <Drawer.Title
            className="px-5 pb-3 font-mono uppercase shrink-0"
            style={{ ...label, fontSize: 11, borderBottom: '1px solid var(--color-border-soft)' }}
          >
            Talky profile
          </Drawer.Title>
          <Drawer.Description className="sr-only">Switch profile; shows its backend, model and voice.</Drawer.Description>
          <div className="overflow-y-auto overscroll-contain px-3 py-3" style={{ paddingBottom: 'max(1.25rem, env(safe-area-inset-bottom))' }}>
            <ProfilePanel onDone={() => setOpen(false)} linkLabel={linkLabel} />
          </div>
        </Drawer.Content>
      </Drawer.Portal>
    </Drawer.Root>
  );
}

export const HeaderChips = ({
  isNarrow,
  linkLabel,
}: {
  isNarrow: boolean;
  /** Set when the open chat is linked to a tool thread, e.g. "T3". */
  linkLabel?: string | null;
}) => (
  <div
    data-testid="header-chips"
    className="flex items-center gap-1.5 shrink-0 px-2 h-8 border-b"
    style={{ borderColor: 'var(--color-border-soft)', backgroundColor: 'var(--color-card)' }}
  >
    <ProfileChip isNarrow={isNarrow} linkLabel={linkLabel} />
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

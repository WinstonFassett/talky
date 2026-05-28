import { useState } from 'react';
import { Drawer } from 'vaul';
import { CheckIcon, ChevronDownIcon, VolumeIcon } from 'lucide-react';

import { PickerTrigger } from './PickerTrigger';
import { useVoiceProfiles } from './useVoiceProfiles';

export const VoicePickerSheet = () => {
  const { voices, activeVoice, switching, error, switchVoice } = useVoiceProfiles();
  const [open, setOpen] = useState(false);

  if (error && voices.length === 0) {
    return <span className="text-xs text-destructive">{error}</span>;
  }
  if (voices.length === 0) return null;

  const current = voices.find((v) => v.name === activeVoice) ?? voices[0];

  const handleSelect = (name: string) => {
    switchVoice(name);
    setOpen(false);
  };

  return (
    <Drawer.Root open={open} onOpenChange={setOpen}>
      <Drawer.Trigger asChild>
        <PickerTrigger
          open={open}
          disabled={switching}
          title={`Switch voice · current: ${current.description || current.name}`}
        >
          <VolumeIcon
            size={14}
            style={{ color: 'var(--color-text-mute)' }}
            className="shrink-0"
          />
          <ChevronDownIcon
            size={11}
            style={{ color: 'var(--color-text-mute)' }}
            className="shrink-0"
          />
        </PickerTrigger>
      </Drawer.Trigger>
      <Drawer.Portal>
        <Drawer.Overlay className="fixed inset-0 z-50 bg-black/50" />
        <Drawer.Content
          className="fixed inset-x-0 bottom-0 z-50 mt-24 flex max-h-[85vh] flex-col rounded-t-2xl outline-none"
          style={{ backgroundColor: 'var(--color-card)' }}
        >
          {/* Drag handle */}
          <div
            aria-hidden
            className="mx-auto my-3 h-1.5 w-12 shrink-0 rounded-full"
            style={{ backgroundColor: 'var(--color-border-soft)' }}
          />
          <div
            className="flex items-center justify-between px-5 pb-3 shrink-0"
            style={{ borderBottom: '1px solid var(--color-border-soft)' }}
          >
            <Drawer.Title
              className="font-mono uppercase"
              style={{
                fontSize: 11,
                fontWeight: 600,
                letterSpacing: '0.08em',
                color: 'var(--color-text-mute)',
              }}
            >
              Voice profile
            </Drawer.Title>
          </div>
          <Drawer.Description className="sr-only">
            Select a voice profile. Active voice is marked.
          </Drawer.Description>

          <div
            className="overflow-y-auto overscroll-contain px-2 py-2"
            style={{ paddingBottom: 'max(1rem, env(safe-area-inset-bottom))' }}
          >
            {voices.map((v) => {
              const selected = v.name === activeVoice;
              return (
                <button
                  key={v.name}
                  type="button"
                  onClick={() => handleSelect(v.name)}
                  disabled={switching}
                  className="w-full text-left rounded-lg px-3 py-3 flex flex-col gap-1 active:bg-[var(--color-panel-3)] disabled:opacity-50"
                  style={
                    selected
                      ? { backgroundColor: 'var(--color-panel-2)' }
                      : undefined
                  }
                >
                  <div className="flex items-center gap-2 w-full">
                    <span
                      className="font-medium truncate flex-1 min-w-0"
                      style={{
                        fontSize: 15,
                        color: 'var(--color-foreground)',
                      }}
                    >
                      {v.description || v.name}
                    </span>
                    {selected && (
                      <CheckIcon
                        size={16}
                        className="shrink-0"
                        style={{ color: 'var(--color-accent)' }}
                      />
                    )}
                  </div>
                  {(v.tts || v.stt) && (
                    <div
                      className="flex flex-wrap gap-x-3"
                      style={{ fontSize: 12, color: 'var(--color-text-mute)' }}
                    >
                      {v.tts && <span className="truncate">TTS: {v.tts}</span>}
                      {v.stt && <span className="truncate">STT: {v.stt}</span>}
                    </div>
                  )}
                </button>
              );
            })}
          </div>
        </Drawer.Content>
      </Drawer.Portal>
    </Drawer.Root>
  );
};

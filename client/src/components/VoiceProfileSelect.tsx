import { useState } from 'react';
import { CheckIcon, ChevronDownIcon, VolumeIcon } from 'lucide-react';

import {
  Combobox,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxGroup,
  ComboboxInput,
  ComboboxItem,
  ComboboxList,
  ComboboxTrigger,
} from './kibo-ui/combobox';
import { PickerTrigger } from './PickerTrigger';
import { useVoiceProfiles } from './useVoiceProfiles';

export const VoiceProfileSelect = ({ compact = false }: { compact?: boolean } = {}) => {
  const { voices, activeVoice, switching, error, switchVoice } = useVoiceProfiles();
  const [open, setOpen] = useState(false);

  if (error && voices.length === 0) {
    return <span className="text-xs text-destructive">{error}</span>;
  }
  if (voices.length === 0) return null;

  const current = voices.find((v) => v.name === activeVoice) ?? voices[0];
  const comboData = voices.map((v) => ({ value: v.name, label: v.description || v.name }));

  return (
    <Combobox
      data={comboData}
      type="voice"
      value={activeVoice}
      onValueChange={switchVoice}
      open={open}
      onOpenChange={setOpen}
    >
      <ComboboxTrigger asChild>
        <PickerTrigger
          open={open}
          disabled={switching}
          title={`Switch voice profile · current: ${current.description || current.name}`}
        >
          <VolumeIcon
            size={12}
            style={{ color: 'var(--color-text-mute)' }}
            className="shrink-0"
          />
          {!compact && (
            <span className="truncate">
              {current.description || current.name}
            </span>
          )}
          <ChevronDownIcon
            size={11}
            style={{ color: 'var(--color-text-mute)' }}
            className="shrink-0"
          />
        </PickerTrigger>
      </ComboboxTrigger>
      <ComboboxContent className="min-w-[320px]" popoverOptions={{ align: 'start' }}>
        <ComboboxInput placeholder="Search voices, providers…" />
        <ComboboxList>
          <ComboboxEmpty />
          <ComboboxGroup heading="Voice profile">
            {voices.map((v) => {
              const selected = v.name === activeVoice;
              return (
                <ComboboxItem
                  key={v.name}
                  value={v.name}
                  className="flex flex-col items-stretch gap-1"
                >
                  {/* Row 1: description (fallback to name) · check */}
                  <div className="flex items-center gap-2 w-full">
                    <span
                      className="font-medium truncate flex-1 min-w-0"
                      style={{ fontSize: 13, color: 'var(--color-foreground)' }}
                    >
                      {v.description || v.name}
                    </span>
                    {selected && (
                      <CheckIcon
                        size={13}
                        className="shrink-0"
                        style={{ color: 'var(--color-accent)' }}
                      />
                    )}
                  </div>
                  {/* Row 2: providers only, all caps, quiet */}
                  {(v.tts || v.stt) && (
                    <div
                      className="truncate uppercase"
                      style={{
                        fontSize: 11,
                        letterSpacing: '0.04em',
                        color: 'var(--color-text-mute)',
                      }}
                    >
                      {v.tts && <>{v.tts.split('·')[0].trim()} TTS</>}
                      {v.tts && v.stt && <> · </>}
                      {v.stt && <>{v.stt.split('·')[0].trim()} STT</>}
                    </div>
                  )}
                </ComboboxItem>
              );
            })}
          </ComboboxGroup>
        </ComboboxList>
      </ComboboxContent>
    </Combobox>
  );
};

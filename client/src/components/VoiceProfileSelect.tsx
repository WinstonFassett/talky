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
import { inferProvider, useVoiceProfiles } from './useVoiceProfiles';

export const VoiceProfileSelect = ({ compact = false }: { compact?: boolean } = {}) => {
  const { voices, activeVoice, switching, error, switchVoice } = useVoiceProfiles();
  const [open, setOpen] = useState(false);

  if (error && voices.length === 0) {
    return <span className="text-xs text-destructive">{error}</span>;
  }
  if (voices.length === 0) return null;

  const current = voices.find((v) => v.name === activeVoice) ?? voices[0];
  const currentProvider = inferProvider(current);
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
            <>
              <span className="max-w-[140px] truncate">
                {current.description || current.name}
              </span>
              {currentProvider && (
                <span className="font-mono text-[10px] uppercase tracking-[0.04em] text-[var(--color-text-mute)]">
                  {currentProvider}
                </span>
              )}
            </>
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
              const provider = inferProvider(v);
              return (
                <ComboboxItem
                  key={v.name}
                  value={v.name}
                  className="flex items-start gap-2 py-2"
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-sm">{v.description || v.name}</span>
                      {provider && (
                        <span className="font-mono text-[10px] uppercase tracking-wider opacity-50">
                          {provider}
                        </span>
                      )}
                      {selected && (
                        <CheckIcon
                          size={13}
                          className="ml-auto"
                          style={{ color: 'var(--color-accent)' }}
                        />
                      )}
                    </div>
                    {(v.tts || v.stt) && (
                      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground font-mono">
                        {v.tts && (
                          <span>
                            <span className="opacity-60">TTS</span> {v.tts}
                          </span>
                        )}
                        {v.stt && (
                          <span>
                            <span className="opacity-60">STT</span> {v.stt}
                          </span>
                        )}
                      </div>
                    )}
                  </div>
                </ComboboxItem>
              );
            })}
          </ComboboxGroup>
        </ComboboxList>
      </ComboboxContent>
    </Combobox>
  );
};

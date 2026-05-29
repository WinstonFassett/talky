import { useState } from 'react';
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from '@pipecat-ai/voice-ui-kit';
import { ChevronDownIcon, MicIcon, MicOffIcon } from 'lucide-react';

import { PickerTrigger } from '../PickerTrigger';
import { useAudioDevices } from './useAudioDevices';

// Form-style mic picker. One button showing the chosen device, dropdown
// lists mics. Visually matches the other form rows (LLMProfileSelect,
// VoiceProfileSelect) via PickerTrigger.
export const MicSelector = () => {
  const [open, setOpen] = useState(false);
  const { isHot, mics, selectedMicId, pickMic } = useAudioDevices();

  const selectedLabel =
    mics.find((m) => m.deviceId === selectedMicId)?.label || 'Default mic';
  const active = isHot || open;

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <PickerTrigger open={open} className="justify-between w-full">
          <span className="flex items-center gap-2 min-w-0">
            {active ? (
              <MicIcon size={16} className="shrink-0" />
            ) : (
              <MicOffIcon size={16} className="shrink-0 opacity-50" />
            )}
            <span className="truncate">{selectedLabel}</span>
          </span>
          <ChevronDownIcon size={14} className="shrink-0 opacity-60" />
        </PickerTrigger>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-[220px]">
        <DropdownMenuLabel>Microphone</DropdownMenuLabel>
        {mics.length === 0 ? (
          <DropdownMenuLabel className="font-normal opacity-60">
            No microphones
          </DropdownMenuLabel>
        ) : (
          mics.map((mic) => (
            <DropdownMenuCheckboxItem
              key={mic.deviceId}
              checked={mic.deviceId === selectedMicId}
              onCheckedChange={() => pickMic(mic.deviceId)}
            >
              {mic.label || 'Microphone'}
            </DropdownMenuCheckboxItem>
          ))
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
};

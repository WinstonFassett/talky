import { useState } from 'react';
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from '@pipecat-ai/voice-ui-kit';
import { ChevronDownIcon, Volume2Icon } from 'lucide-react';

import { PickerTrigger } from '../PickerTrigger';
import { useAudioDevices } from './useAudioDevices';

// Form-style speaker picker. Visually matches MicSelector.
export const SpeakerSelector = () => {
  const [open, setOpen] = useState(false);
  const { speakers, selectedSpeakerId, pickSpeaker } = useAudioDevices();

  const selectedLabel =
    speakers.find((s) => s.deviceId === selectedSpeakerId)?.label ||
    'Default speaker';

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <PickerTrigger open={open} className="justify-between w-full">
          <span className="flex items-center gap-2 min-w-0">
            <Volume2Icon size={16} className="shrink-0" />
            <span className="truncate">{selectedLabel}</span>
          </span>
          <ChevronDownIcon size={14} className="shrink-0 opacity-60" />
        </PickerTrigger>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-[220px]">
        <DropdownMenuLabel>Speaker</DropdownMenuLabel>
        {speakers.length === 0 ? (
          <DropdownMenuLabel className="font-normal opacity-60">
            No speakers
          </DropdownMenuLabel>
        ) : (
          speakers.map((sp) => (
            <DropdownMenuCheckboxItem
              key={sp.deviceId}
              checked={sp.deviceId === selectedSpeakerId}
              onCheckedChange={() => pickSpeaker(sp.deviceId)}
            >
              {sp.label || 'Speaker'}
            </DropdownMenuCheckboxItem>
          ))
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
};

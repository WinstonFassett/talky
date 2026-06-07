import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from '@pipecat-ai/voice-ui-kit';
import { SlidersHorizontalIcon } from 'lucide-react';

import { AudioMenuItems } from './AudioMenuItems';
import { useAudioDevices } from './useAudioDevices';

// Header audio-device control for the connected desktop chrome. A ghost icon
// button (size-8, matching the adjacent mute + More buttons) that opens the
// same mic/speaker device dropdown as our UserAudioControl repro (AudioPill's
// cold path). The footer already shows the LLM + Voice pickers inline on
// desktop, so the only session config missing once connected was audio devices
// — this fills that gap without the heavier Assistant/Voice/Audio drawer.
export const AudioSettingsButton = () => {
  const { mics, speakers, selectedMicId, selectedSpeakerId, pickMic, pickSpeaker } =
    useAudioDevices();

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label="Audio devices"
          title="Audio devices"
          className="inline-flex items-center justify-center shrink-0 size-8 rounded-md bg-transparent transition-colors cursor-pointer hover:bg-[var(--color-panel-2)]"
          style={{ color: 'var(--color-text-dim)' }}
        >
          <SlidersHorizontalIcon size={16} />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" sideOffset={8} className="min-w-[240px]">
        <AudioMenuItems
          mics={mics}
          speakers={speakers}
          selectedMicId={selectedMicId}
          selectedSpeakerId={selectedSpeakerId}
          onPickMic={pickMic}
          onPickSpeaker={pickSpeaker}
        />
      </DropdownMenuContent>
    </DropdownMenu>
  );
};

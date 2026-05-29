import {
  PipecatClientMicToggle,
  usePipecatClientMediaDevices,
} from '@pipecat-ai/client-react';
import {
  Button,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
  UserAudioComponent,
} from '@pipecat-ai/voice-ui-kit';
import { ChevronDownIcon, MicIcon } from 'lucide-react';

import { AudioMenuItems } from './AudioMenuItems';
import { useAudioDevices } from './useAudioDevices';

interface AudioPillProps {
  size?: 'sm' | 'md' | 'lg';
  variant?: 'ghost' | 'secondary' | 'primary';
  classNames?: { button?: string; dropdownMenuTrigger?: string };
  visualizerProps?: Record<string, unknown>;
}

// Compact single-element audio control for the header. Hot mode delegates
// to voice-ui-kit's UserAudioComponent (mute toggle + live visualizer +
// device dropdown). Cold mode renders a simple "Audio" trigger that opens
// the same device dropdown — no mic stream, no visualizer, so the OS
// recording dot stays dark.
export const AudioPill = ({
  size = 'md',
  variant = 'ghost',
  classNames,
  visualizerProps,
}: AudioPillProps) => {
  const devices = useAudioDevices();

  if (devices.isHot) {
    return <HotAudioPill size={size} variant={variant} classNames={classNames} visualizerProps={visualizerProps} />;
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant={variant}
          size={size}
          className={classNames?.button}
          aria-label="Audio devices"
        >
          <MicIcon />
          <span className="flex-1 text-left">Audio</span>
          <ChevronDownIcon size={14} className="opacity-60" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" sideOffset={8}>
        <AudioMenuItems
          mics={devices.mics}
          speakers={devices.speakers}
          selectedMicId={devices.selectedMicId}
          selectedSpeakerId={devices.selectedSpeakerId}
          onPickMic={devices.pickMic}
          onPickSpeaker={devices.pickSpeaker}
        />
      </DropdownMenuContent>
    </DropdownMenu>
  );
};

// Hot variant uses the kit's full live control. Kept inline so the cold
// path doesn't pay the cost of mounting it. usePipecatClientMediaDevices
// is called here (not via the shared hook) because UserAudioComponent
// expects the OptionalMediaDeviceInfo shape directly.
const HotAudioPill = ({
  size,
  variant,
  classNames,
  visualizerProps,
}: Required<Pick<AudioPillProps, 'size' | 'variant'>> &
  Pick<AudioPillProps, 'classNames' | 'visualizerProps'>) => {
  const {
    availableMics,
    selectedMic,
    updateMic,
    availableSpeakers,
    selectedSpeaker,
    updateSpeaker,
  } = usePipecatClientMediaDevices();

  return (
    <PipecatClientMicToggle>
      {({ isMicEnabled, onClick }) => (
        <UserAudioComponent
          onClick={onClick}
          isMicEnabled={isMicEnabled}
          state={isMicEnabled ? 'default' : 'inactive'}
          availableMics={availableMics}
          selectedMic={selectedMic}
          updateMic={updateMic}
          availableSpeakers={availableSpeakers}
          selectedSpeaker={selectedSpeaker}
          updateSpeaker={updateSpeaker}
          size={size}
          variant={variant}
          classNames={classNames}
          visualizerProps={visualizerProps}
        />
      )}
    </PipecatClientMicToggle>
  );
};

import {
  PipecatClientMicToggle,
  usePipecatClientMediaDevices,
} from '@pipecat-ai/client-react';
import { UserAudioComponent } from '@pipecat-ai/voice-ui-kit';

interface AudioControlProps {
  size?: 'sm' | 'md' | 'lg';
  variant?: 'ghost' | 'secondary' | 'primary';
  noVisualizer?: boolean;
  classNames?: { button?: string; dropdownMenuTrigger?: string };
  visualizerProps?: Record<string, unknown>;
}

// Drop-in replacement for voice-ui-kit's UserAudioControl. The wrapper version
// forces a spinner whenever transport state is "disconnected" or "initializing",
// which means the control flips to a spinner after every disconnect even though
// the browser audio APIs are still perfectly available. We use the headless
// UserAudioComponent directly so the control stays live across the transport
// lifecycle. (Devices are populated once at mount via client.initDevices().)
export const AudioControl = ({
  size = 'md',
  variant = 'ghost',
  noVisualizer = false,
  classNames,
  visualizerProps,
}: AudioControlProps) => {
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
          noVisualizer={noVisualizer}
          classNames={classNames}
          visualizerProps={visualizerProps}
        />
      )}
    </PipecatClientMicToggle>
  );
};

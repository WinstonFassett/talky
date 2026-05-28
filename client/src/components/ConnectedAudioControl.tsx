import {
  PipecatClientMicToggle,
  usePipecatClientMediaDevices,
} from '@pipecat-ai/client-react';
import { UserAudioComponent } from '@pipecat-ai/voice-ui-kit';

interface ConnectedAudioControlProps {
  size?: 'sm' | 'md' | 'lg';
  variant?: 'ghost' | 'secondary' | 'primary';
  noVisualizer?: boolean;
  classNames?: { button?: string; dropdownMenuTrigger?: string };
  visualizerProps?: Record<string, unknown>;
}

// Audio control for the connected state. Pipecat owns the mic stream, so the
// visualizer is driven by the live capture — it doubles as the mute button.
// Uses the headless UserAudioComponent directly so the control stays live
// across transport state transitions (the wrapper UserAudioControl forces a
// spinner whenever transport is "disconnected" or "initializing").
export const ConnectedAudioControl = ({
  size = 'md',
  variant = 'ghost',
  noVisualizer = false,
  classNames,
  visualizerProps,
}: ConnectedAudioControlProps) => {
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

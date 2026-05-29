import {
  PipecatClientMicToggle,
  usePipecatClientMediaDevices,
} from '@pipecat-ai/client-react';
import { UserAudioComponent } from '@pipecat-ai/voice-ui-kit';

interface LiveAudioControlsProps {
  size?: 'sm' | 'md' | 'lg';
  variant?: 'ghost' | 'secondary' | 'primary';
  noVisualizer?: boolean;
  classNames?: { button?: string; dropdownMenuTrigger?: string };
  visualizerProps?: Record<string, unknown>;
}

// Hot audio controls — mute toggle + live visualizer, with mid-call mic and
// speaker switching. Belongs in the header (always shown when header shows).
// Pipecat owns the mic stream, so the visualizer is driven by the live
// capture — it doubles as the mute button. Uses the headless
// UserAudioComponent directly so the control stays live across transport
// state transitions (the wrapper UserAudioControl forces a spinner whenever
// transport is "disconnected" or "initializing").
export const LiveAudioControls = ({
  size = 'md',
  variant = 'ghost',
  noVisualizer = false,
  classNames,
  visualizerProps,
}: LiveAudioControlsProps) => {
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

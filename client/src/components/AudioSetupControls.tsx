import { usePipecatClient } from '@pipecat-ai/client-react';
import { MicPicker } from './MicPicker';
import { SpeakerPicker } from './SpeakerPicker';

interface AudioSetupControlsProps {
  size?: 'sm' | 'md' | 'lg';
}

// Cold audio controls — device pickers only, no live mic stream. Belongs in
// EmptyState (and the mobile session sheet pre-connect). Pipecat can't
// release a mic stream without an active peer connection, so the MicPicker
// owns its own MediaStream via the browser API — opened on dropdown open,
// released on dropdown close. Pipecat learns the chosen deviceId at connect
// time via client.updateMic / client.updateSpeaker. See ticket cf2f.
export const AudioSetupControls = ({ size = 'md' }: AudioSetupControlsProps) => {
  const client = usePipecatClient();
  if (!client) return null;
  return (
    <div className="flex flex-col gap-1.5 w-full">
      <MicPicker client={client} size={size} />
      <SpeakerPicker client={client} size={size} />
    </div>
  );
};

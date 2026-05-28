import { usePipecatClient } from '@pipecat-ai/client-react';
import { MicPicker } from './MicPicker';
import { SpeakerPicker } from './SpeakerPicker';

interface AudioControlProps {
  size?: 'sm' | 'md' | 'lg';
  // Legacy props from the prior voice-ui-kit-based AudioControl — accepted but ignored
  // (the new MicPicker / SpeakerPicker own their own styling).
  variant?: 'ghost' | 'secondary' | 'primary';
  noVisualizer?: boolean;
  classNames?: { button?: string; dropdownMenuTrigger?: string };
  visualizerProps?: Record<string, unknown>;
}

// Composes the mic + speaker pickers. The mic picker owns its own MediaStream
// via the browser API (pipecat is not asked to open a stream pre-call), so we
// avoid the "always listening" red dot when idle. See ticket cf2f.
export const AudioControl = ({ size = 'md' }: AudioControlProps) => {
  const client = usePipecatClient();
  if (!client) return null;
  return (
    <div className="flex items-center gap-1">
      <MicPicker client={client} size={size} />
      <SpeakerPicker client={client} size={size} />
    </div>
  );
};

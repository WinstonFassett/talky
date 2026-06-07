import { PipecatClientMicToggle } from '@pipecat-ai/client-react';
import { MicIcon, MicOffIcon } from 'lucide-react';

// Bare mic mute toggle for the header right cluster. lucide Mic / MicOff,
// ghost icon button — no device dropdown (device selection lives in the
// More menu / SessionSheet). Muted state reads in destructive tint so it's
// glanceable that the mic is off.
export const MuteButton = () => {
  return (
    <PipecatClientMicToggle>
      {({ isMicEnabled, onClick }) => (
        <button
          type="button"
          onClick={onClick}
          aria-label={isMicEnabled ? 'Mute microphone' : 'Unmute microphone'}
          aria-pressed={!isMicEnabled}
          title={isMicEnabled ? 'Mute microphone' : 'Unmute microphone'}
          className="inline-flex items-center justify-center shrink-0 size-8 rounded-md bg-transparent transition-colors cursor-pointer hover:bg-[var(--color-panel-2)]"
          style={{
            color: isMicEnabled ? 'var(--color-text-dim)' : 'var(--color-destructive)',
          }}
        >
          {isMicEnabled ? <MicIcon size={17} /> : <MicOffIcon size={17} />}
        </button>
      )}
    </PipecatClientMicToggle>
  );
};

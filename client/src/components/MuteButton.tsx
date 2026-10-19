import { PipecatClientMicToggle } from '@pipecat-ai/client-react';
import { useCall } from '../call/CallProvider';
import { MicIcon, MicOffIcon } from 'lucide-react';

// Bare mic mute toggle for the header right cluster. lucide Mic / MicOff,
// ghost icon button — no device dropdown (device selection lives in the
// More menu / SessionSheet). Muted state reads in destructive tint so it's
// glanceable that the mic is off.
export const MuteButton = () => {
  const { micIssue, retryMic } = useCall();
  const broken = micIssue?.kind === 'blocked' || micIssue?.kind === 'nodevice' || micIssue?.kind === 'ended';
  if (broken) {
    const tip =
      micIssue.kind === 'nodevice'
        ? 'No microphone found. Click to retry.'
        : micIssue.kind === 'ended'
          ? 'Microphone disconnected. Click to retry.'
          : 'Microphone blocked. Allow it for this site, then click to retry.';
    return (
      <button
        type="button"
        data-testid="mic-toggle"
        data-mic="unavailable"
        onClick={() => void retryMic()}
        aria-label={tip}
        title={tip}
        className="inline-flex items-center justify-center shrink-0 size-8 rounded-md bg-transparent cursor-pointer hover:bg-[var(--color-panel-2)]"
        style={{ color: 'var(--color-destructive)' }}
      >
        <MicOffIcon size={17} />
      </button>
    );
  }
  return (
    <PipecatClientMicToggle>
      {({ isMicEnabled, onClick }) => (
        <button
          type="button"
          onClick={onClick}
          data-testid="mic-toggle"
          data-mic={isMicEnabled ? 'on' : 'muted'}
          aria-label={isMicEnabled ? 'Mute microphone' : 'Unmute microphone'}
          aria-pressed={!isMicEnabled}
          title={isMicEnabled ? 'Mic on. Click to mute' : 'Mic muted. Click to unmute'}
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

import { Volume2Icon, VolumeXIcon } from 'lucide-react';

import { useSpeakerMute } from './useSpeakerMute';

// Header upper-right speaker mute (mirrors Hermes Desktop). Mutes the bot's
// output audio — what you hear — not the mic. lucide Volume2 / VolumeX, ghost
// icon button; muted reads destructive so it's glanceable that you've silenced
// the bot.
export const SpeakerMuteButton = () => {
  const { muted, toggle } = useSpeakerMute();
  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={muted ? 'Unmute speaker' : 'Mute speaker'}
      aria-pressed={muted}
      title={muted ? 'Unmute speaker' : 'Mute speaker'}
      className="inline-flex items-center justify-center shrink-0 size-8 rounded-md bg-transparent transition-colors cursor-pointer hover:bg-[var(--color-panel-2)]"
      style={{
        color: muted ? 'var(--color-destructive)' : 'var(--color-text-dim)',
      }}
    >
      {muted ? <VolumeXIcon size={17} /> : <Volume2Icon size={17} />}
    </button>
  );
};

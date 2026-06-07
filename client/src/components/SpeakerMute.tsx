import { usePipecatClientMediaTrack } from '@pipecat-ai/client-react';
import { useEffect, useRef } from 'react';

import { useSpeakerMute } from './useSpeakerMute';

// Our bot audio sink. Mirrors @pipecat-ai/client-react's PipecatClientAudio
// (usePipecatClientMediaTrack('audio','bot') → audio.srcObject), adding a
// `muted` flag we control from the header speaker button. Mount exactly once,
// inside SpeakerMuteProvider, with PipecatAppBase given `noAudioOutput` so the
// kit doesn't also render its own bot audio (which would double-play).
//
// Output only: "stop the bot from talking in my ears." Mic mute (input) is a
// separate control (PipecatClientMicToggle) that lives by the composer.
export const BotAudio = () => {
  const { muted } = useSpeakerMute();
  const ref = useRef<HTMLAudioElement>(null);
  const botAudioTrack = usePipecatClientMediaTrack('audio', 'bot');

  useEffect(() => {
    const el = ref.current;
    if (!el || !botAudioTrack) return;
    if (el.srcObject) {
      const oldTrack = (el.srcObject as MediaStream).getAudioTracks()[0];
      if (oldTrack && oldTrack.id === botAudioTrack.id) return;
    }
    el.srcObject = new MediaStream([botAudioTrack]);
  }, [botAudioTrack]);

  return <audio ref={ref} autoPlay muted={muted} />;
};

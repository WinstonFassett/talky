import type { PipecatClient } from '@pipecat-ai/client-js';
import { usePipecatClientMediaTrack } from '@pipecat-ai/client-react';
import { CircularWaveform } from '@pipecat-ai/voice-ui-kit';
import { useEffect, useState } from 'react';

import { useUrlParam } from '../fixtures/harness';

interface BotVisualizerProps {
  client: PipecatClient | null;
}

export const BotVisualizer = ({ client }: BotVisualizerProps) => {
  const [isBotThinking, setIsBotThinking] = useState(false);
  const [isBotSpeaking, setIsBotSpeaking] = useState(false);

  const botAudioTrack = usePipecatClientMediaTrack('audio', 'bot');
  const voiceStateOverride = useUrlParam('voiceState');

  useEffect(() => {
    if (!client) return;

    const handleBotLlmStarted = () => setIsBotThinking(true);
    const handleBotLlmStopped = () => setIsBotThinking(false);
    const handleBotTtsStarted = () => setIsBotThinking(false);
    const handleBotStartedSpeaking = () => {
      setIsBotThinking(false);
      setIsBotSpeaking(true);
    };
    const handleBotStoppedSpeaking = () => setIsBotSpeaking(false);

    client.on('botLlmStarted', handleBotLlmStarted);
    client.on('botLlmStopped', handleBotLlmStopped);
    client.on('botTtsStarted', handleBotTtsStarted);
    client.on('botStartedSpeaking', handleBotStartedSpeaking);
    client.on('botStoppedSpeaking', handleBotStoppedSpeaking);

    return () => {
      client.off('botLlmStarted', handleBotLlmStarted);
      client.off('botLlmStopped', handleBotLlmStopped);
      client.off('botTtsStarted', handleBotTtsStarted);
      client.off('botStartedSpeaking', handleBotStartedSpeaking);
      client.off('botStoppedSpeaking', handleBotStoppedSpeaking);
    };
  }, [client]);

  const thinking = voiceStateOverride === 'thinking' || (!voiceStateOverride && isBotThinking);
  const speaking = voiceStateOverride === 'speaking' || (!voiceStateOverride && isBotSpeaking);
  const audioTrack = voiceStateOverride ? null : (isBotSpeaking ? botAudioTrack : null);

  return (
    <CircularWaveform
      size={72}
      audioTrack={audioTrack}
      isThinking={thinking}
      color1="var(--color-accent)"
      color2="var(--color-accent)"
      backgroundColor="transparent"
      rotationEnabled={!speaking}
      numBars={32}
      barWidth={1}
      sensitivity={2}
    />
  );
};

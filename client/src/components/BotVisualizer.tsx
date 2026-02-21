import type { PipecatClient } from '@pipecat-ai/client-js';
import { usePipecatClientMediaTrack } from '@pipecat-ai/client-react';
import { CircularWaveform } from '@pipecat-ai/voice-ui-kit';
import { useEffect, useState } from 'react';


interface BotVisualizerProps {
  client: PipecatClient | null;
}

export const BotVisualizer = ({ client }: BotVisualizerProps) => {
  const [isBotThinking, setIsBotThinking] = useState(false);
  const [isBotSpeaking, setIsBotSpeaking] = useState(false);
  
  const botAudioTrack = usePipecatClientMediaTrack('audio', 'bot');

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

  return (
    <CircularWaveform 
      size={60}
      audioTrack={isBotSpeaking ? botAudioTrack : null}
      isThinking={isBotThinking}            
      color1="#615fff"
      color2="#EC4899"
      backgroundColor="transparent"
      rotationEnabled={!isBotSpeaking}
      numBars={32}
      barWidth={1}
      sensitivity={2}
    />
  );
};

import type { PipecatClient } from '@pipecat-ai/client-js';
import { usePipecatClientMediaTrack } from '@pipecat-ai/client-react';
import { CircularWaveform } from '@pipecat-ai/voice-ui-kit';
import { useEffect, useRef, useState } from 'react';

type BotState = 'idle' | 'thinking' | 'speaking';

interface BotVisualizerProps {
  client: PipecatClient | null;
}

export const BotVisualizer = ({ client }: BotVisualizerProps) => {
  const [botState, setBotState] = useState<BotState>('idle');
  const clientRef = useRef<PipecatClient | null>(null);
  
  // Update client ref when client changes
  useEffect(() => {
    clientRef.current = client;
  }, [client]);
  
  // Use the proper Pipecat hook to get the bot's audio track with error handling
  const botAudioTrack = usePipecatClientMediaTrack('audio', 'bot');
  
  // Listen for bot state changes with coordinated state management
  useEffect(() => {
    if (!client) return;

    const handleBotLlmStarted = () => {
      setBotState('thinking');
    };

    const handleBotLlmStopped = () => {
      setBotState('idle');
    };

    const handleBotTtsStarted = () => {
      // TTS starting doesn't mean speaking yet - wait for actual audio
      setBotState('idle');
    };

    const handleBotTtsStopped = () => {
      // TTS stopping doesn't mean speaking stopped - let audio track handle it
    };

    const handleBotStartedSpeaking = () => {
      setBotState('speaking');
    };

    const handleBotStoppedSpeaking = () => {
      setBotState('idle');
    };

    // Subscribe to client events
    client.on('botLlmStarted', handleBotLlmStarted);
    client.on('botLlmStopped', handleBotLlmStopped);
    client.on('botTtsStarted', handleBotTtsStarted);
    client.on('botTtsStopped', handleBotTtsStopped);
    client.on('botStartedSpeaking', handleBotStartedSpeaking);
    client.on('botStoppedSpeaking', handleBotStoppedSpeaking);

    return () => {
      // Use the stored client ref to ensure we clean up the same instance
      const currentClient = clientRef.current;
      if (currentClient) {
        currentClient.off('botLlmStarted', handleBotLlmStarted);
        currentClient.off('botLlmStopped', handleBotLlmStopped);
        currentClient.off('botTtsStarted', handleBotTtsStarted);
        currentClient.off('botTtsStopped', handleBotTtsStopped);
        currentClient.off('botStartedSpeaking', handleBotStartedSpeaking);
        currentClient.off('botStoppedSpeaking', handleBotStoppedSpeaking);
      }
    };
  }, [client]);

  const isBotThinking = botState === 'thinking';
  const isBotSpeaking = botState === 'speaking';

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

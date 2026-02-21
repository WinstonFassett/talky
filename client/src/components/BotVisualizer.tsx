import type { PipecatClient } from '@pipecat-ai/client-js';
import { usePipecatClientMediaTrack } from '@pipecat-ai/client-react';
import { CircularWaveform } from '@pipecat-ai/voice-ui-kit';
import { useEffect, useRef, useState } from 'react';

type BotState = 'idle' | 'thinking' | 'speaking';

interface BotVisualizerProps {
  client: PipecatClient | null;
}

export const BotVisualizer = ({ client }: BotVisualizerProps) => {
  const [isBotThinking, setIsBotThinking] = useState(false);
  const [isBotSpeaking, setIsBotSpeaking] = useState(false);
  const clientRef = useRef<PipecatClient | null>(null);
  
  // Update client ref when client changes
  useEffect(() => {
    clientRef.current = client;
  }, [client]);
  
  // Use the proper Pipecat hook to get the bot's audio track
  const botAudioTrack = usePipecatClientMediaTrack('audio', 'bot');

  // Listen for bot state changes - EXACT logic from working-bot-viz
  useEffect(() => {
    if (!client) return;

    const handleBotLlmStarted = () => {
      setIsBotThinking(true);
    };

    const handleBotLlmStopped = () => {
      setIsBotThinking(false);
    };

    const handleBotTtsStarted = () => {
      setIsBotThinking(false);
      // Don't set speaking yet - wait for actual audio to start
    };

    const handleBotTtsStopped = () => {
      // Don't clear speaking yet - wait for actual audio to stop
    };

    const handleBotStartedSpeaking = () => {
      setIsBotThinking(false);
      setIsBotSpeaking(true);
      // Audio track is automatically handled by usePipecatClientMediaTrack
    };

    const handleBotStoppedSpeaking = () => {
      setIsBotSpeaking(false);
      // Audio track is automatically handled by usePipecatClientMediaTrack
    };

    // Subscribe to client events using the correct event names
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

  return (
    <CircularWaveform 
      size={60}
      audioTrack={isBotSpeaking ? botAudioTrack : null}
      isThinking={isBotThinking}            
      color1="#615fff"
      color2="#EC4899"
      backgroundColor="transparent"
      rotationEnabled={!isBotSpeaking} // Rotation for idle and thinking, disabled when speaking
      numBars={32}
      barWidth={1}
      sensitivity={2}
    />
  );
};

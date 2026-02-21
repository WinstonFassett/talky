import { usePipecatClientMediaTrack } from '@pipecat-ai/client-react';
import { CircularWaveform } from '@pipecat-ai/voice-ui-kit';
import { useEffect, useState } from 'react';
import type { PipecatClient } from '@pipecat-ai/client-js';

interface BotVisualizerProps {
  client: PipecatClient | null;
}

export const BotVisualizer = ({ client }: BotVisualizerProps) => {
  const [isBotThinking, setIsBotThinking] = useState(false);
  const [isBotSpeaking, setIsBotSpeaking] = useState(false);
  
  // Use the proper Pipecat hook to get the bot's audio track
  const botAudioTrack = usePipecatClientMediaTrack('audio', 'bot');
  // Also try local audio as fallback for testing
  const localAudioTrack = usePipecatClientMediaTrack('audio', 'local');
  
  // Debug: Log the audio track
  useEffect(() => {
    console.log('Bot audio track changed:', botAudioTrack);
    console.log('Track enabled:', botAudioTrack?.enabled);
    console.log('Track state:', botAudioTrack?.readyState);
    console.log('Local audio track:', localAudioTrack);
  }, [botAudioTrack, localAudioTrack]);

  // Listen for bot state changes
  useEffect(() => {
    if (!client) return;

    const handleBotLlmStarted = () => {
      console.log('Bot LLM started - setting thinking to true');
      setIsBotThinking(true);
    };

    const handleBotLlmStopped = () => {
      console.log('Bot LLM stopped - setting thinking to false');
      setIsBotThinking(false);
    };

    const handleBotTtsStarted = () => {
      console.log('Bot TTS started - using synthetic audio visualization');
      setIsBotThinking(false);
      // Don't set speaking yet - wait for actual audio to start
    };

    const handleBotTtsStopped = () => {
      console.log('Bot TTS stopped - clearing bot audio track');
      // Don't clear speaking yet - wait for actual audio to stop
    };

    const handleBotStartedSpeaking = () => {
      console.log('Bot started speaking - using synthetic audio visualization');
      setIsBotThinking(false);
      setIsBotSpeaking(true);
      // Audio track is automatically handled by usePipecatClientMediaTrack
    };

    const handleBotStoppedSpeaking = () => {
      console.log('Bot stopped speaking - clearing bot audio track');
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
      client.off('botLlmStarted', handleBotLlmStarted);
      client.off('botLlmStopped', handleBotLlmStopped);
      client.off('botTtsStarted', handleBotTtsStarted);
      client.off('botTtsStopped', handleBotTtsStopped);
      client.off('botStartedSpeaking', handleBotStartedSpeaking);
      client.off('botStoppedSpeaking', handleBotStoppedSpeaking);
    };
  }, [client]);

  return (
    <CircularWaveform 
      size={60}
      audioTrack={isBotSpeaking ? (botAudioTrack || localAudioTrack) : null}
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

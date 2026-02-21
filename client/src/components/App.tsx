import { useEffect, useRef, useState } from 'react';

import type { PipecatBaseChildProps } from '@pipecat-ai/voice-ui-kit';
import { usePipecatClientMediaTrack } from '@pipecat-ai/client-react';
import {
  ConnectButton,
  ConversationPanel,
  CircularWaveform,
  // EventsPanel,
  UserAudioControl,
} from '@pipecat-ai/voice-ui-kit';

import type { TransportType } from '../config';
import { TransportSelect } from './TransportSelect';

interface AppProps extends PipecatBaseChildProps {
  transportType: TransportType;
  onTransportChange: (type: TransportType) => void;
  availableTransports: TransportType[];
  autoconnect?: boolean;
}

export const App = ({
  client,
  handleConnect,
  handleDisconnect,
  transportType,
  onTransportChange,
  availableTransports,
  autoconnect = false,
}: AppProps) => {
  const autoconnectAttempted = useRef(false);
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

  useEffect(() => {
    client?.initDevices();
  }, [client]);

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

  // Very conservative autoconnect - only after everything is loaded
  useEffect(() => {
    if (autoconnect && client && handleConnect && !autoconnectAttempted.current) {
      // Wait for UI to be fully rendered and stable
      const timer = setTimeout(() => {
        if (client && handleConnect && !autoconnectAttempted.current) {
          autoconnectAttempted.current = true;
          handleConnect();
        }
      }, 2000); // Very conservative 2-second delay
      return () => clearTimeout(timer);
    }
  }, [autoconnect, client, handleConnect]);

  const showTransportSelector = availableTransports.length > 1;

  return (
    <div className="flex flex-col w-full h-full">
      <div className="flex items-center justify-between gap-4 p-4">
        <div className="flex items-center gap-4">
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
          {showTransportSelector ? (
            <TransportSelect
              transportType={transportType}
              onTransportChange={onTransportChange}
              availableTransports={availableTransports}
            />
          ) : (
            <div /> /* Spacer */
          )}
        </div>
        <div className="flex items-center gap-4">
          <UserAudioControl size="lg" />
          <ConnectButton
            size="lg"
            onConnect={handleConnect}
            onDisconnect={handleDisconnect}
          />
        </div>
      </div>
      <div className="flex-1 overflow-hidden px-4">
        <div className="h-full overflow-hidden">
          <ConversationPanel />
        </div>
      </div>
      {/* <div className="h-96 overflow-hidden px-4 pb-4">
        <EventsPanel />
      </div> */}
    </div>
  );
};

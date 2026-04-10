import { useCallback, useEffect, useRef, useState } from 'react';

import type { PipecatBaseChildProps } from '@pipecat-ai/voice-ui-kit';
import {
  ConnectButton,
  ConversationPanel,
  // EventsPanel,
  UserAudioControl,
} from '@pipecat-ai/voice-ui-kit';
import { usePipecatClientTransportState } from '@pipecat-ai/client-react';

import type { TransportType } from '../config';
import { TransportSelect } from './TransportSelect';
import { BotVisualizer } from './BotVisualizer';
import { VoiceProfileSelect } from './VoiceProfileSelect';

// Pre-load the drop cue so it plays instantly on unexpected disconnect.
// The WAV is generated from shared/audio_cues.stop_cue_pcm (three
// descending beeps). Ticket 6b60 problem B.
const dropCueAudio = new Audio('/cues/drop.wav');
dropCueAudio.volume = 0.7;

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
  const userInitiatedDisconnect = useRef(false);

  const [devicesReady, setDevicesReady] = useState(false);

  const transportState = usePipecatClientTransportState();

  // Wrap handleDisconnect to flag user-initiated disconnects so the
  // drop-cue logic can distinguish them from unexpected drops.
  const wrappedDisconnect = useCallback(() => {
    userInitiatedDisconnect.current = true;
    handleDisconnect?.();
  }, [handleDisconnect]);

  // Play drop cue on unexpected disconnect (ticket 6b60 problem B).
  // "Unexpected" = transport went to disconnected/error without the
  // user clicking the disconnect button.
  useEffect(() => {
    if (
      (transportState === 'disconnected' || transportState === 'error') &&
      !userInitiatedDisconnect.current
    ) {
      dropCueAudio.currentTime = 0;
      dropCueAudio.play().catch(() => {});
    }
    // Reset the flag when we're back to a non-terminal state.
    if (transportState === 'connected' || transportState === 'ready') {
      userInitiatedDisconnect.current = false;
    }
  }, [transportState]);

  useEffect(() => {
    if (client) {
      client?.initDevices().then(() => {
        setDevicesReady(true);
      }).catch(err => {
        console.error('Failed to initialize devices:', err);
        setDevicesReady(true); // Still try to connect even if devices fail
      });
    }
  }, [client]);

  useEffect(() => {
    if (autoconnect && client && handleConnect && !autoconnectAttempted.current && devicesReady) {
      autoconnectAttempted.current = true;
      handleConnect();
    }
  }, [autoconnect, client, handleConnect, devicesReady]);

  const showTransportSelector = availableTransports.length > 1;

  return (
    <div className="flex flex-col w-full h-full">
      <div className="flex items-center justify-between gap-4 p-4">
        <div className="flex items-center gap-4">
          <BotVisualizer client={client} />
          <VoiceProfileSelect client={client} />
          {showTransportSelector ? (
            <TransportSelect
              transportType={transportType}
              onTransportChange={onTransportChange}
              availableTransports={availableTransports}
            />
          ) : null}
        </div>
        <div className="flex items-center gap-4">
          <UserAudioControl size="lg" />
          <ConnectButton
            size="lg"
            onConnect={handleConnect}
            onDisconnect={wrappedDisconnect}
          />
        </div>
      </div>
      <div className="flex-1 overflow-hidden px-4">
        <div className="h-full overflow-hidden">
          <ConversationPanel />
        </div>
      </div>
    </div>
  );
};

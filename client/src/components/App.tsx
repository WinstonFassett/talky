import { useEffect, useRef, useState } from 'react';

import type { PipecatBaseChildProps } from '@pipecat-ai/voice-ui-kit';
import {
  ConnectButton,
  ConversationPanel,
  // EventsPanel,
  UserAudioControl,
} from '@pipecat-ai/voice-ui-kit';

import type { TransportType } from '../config';
import { TransportSelect } from './TransportSelect';
import { BotVisualizer } from './BotVisualizer';
import { VoiceProfileSelect } from './VoiceProfileSelect';

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

  const [devicesReady, setDevicesReady] = useState(false);

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
            onDisconnect={handleDisconnect}
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

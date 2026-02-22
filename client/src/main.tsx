import { StrictMode, useState } from 'react';
import { createRoot } from 'react-dom/client';

import { ThemeProvider } from '@pipecat-ai/voice-ui-kit';

import type { PipecatBaseChildProps } from '@pipecat-ai/voice-ui-kit';
import {
  ErrorCard,
  FullScreenContainer,
  PipecatAppBase,
  SpinLoader,
} from '@pipecat-ai/voice-ui-kit';

import { App } from './components/App';
import type { TransportType } from './config';
import {
  AVAILABLE_TRANSPORTS,
  DEFAULT_TRANSPORT,
  TRANSPORT_CONFIG,
} from './config';
import './index.css';

// Parse URL parameters
const getSearchParams = () => {
  if (typeof window === 'undefined') return new URLSearchParams();
  return new URLSearchParams(window.location.search);
};

const getAutoconnect = () => {
  return getSearchParams().get('autoconnect') === 'true';
};

export const Main = () => {
  const [transportType, setTransportType] =
    useState<TransportType>(DEFAULT_TRANSPORT);

  const connectParams = TRANSPORT_CONFIG[transportType];
  const autoconnect = getAutoconnect();

  return (
    <ThemeProvider disableStorage>
      <FullScreenContainer>
        <PipecatAppBase
          connectParams={connectParams}
          transportType={transportType}>
          {({
            client,
            handleConnect,
            handleDisconnect,
            error,
          }: PipecatBaseChildProps) => {
            if (!client) {
              return <SpinLoader />;
            }
            if (error) {
              return <ErrorCard>{error}</ErrorCard>;
            }
            return (
              <App
                client={client}
                handleConnect={handleConnect}
                handleDisconnect={handleDisconnect}
                transportType={transportType}
                onTransportChange={setTransportType}
                availableTransports={AVAILABLE_TRANSPORTS}
                autoconnect={autoconnect}
              />
            );
          }}
        </PipecatAppBase>
      </FullScreenContainer>
    </ThemeProvider>
  );
};

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Main />
  </StrictMode>
);

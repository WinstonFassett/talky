import { useCallback, useEffect, useRef, useState } from 'react';

import type { PipecatBaseChildProps } from '@pipecat-ai/voice-ui-kit';
import { ConnectButton, UserAudioControl } from '@pipecat-ai/voice-ui-kit';
import { ConversationPanelWithReasoning } from './ConversationPanelWithReasoning';
import { usePipecatClientTransportState } from '@pipecat-ai/client-react';

import type { TransportType } from '../config';
import { TransportSelect } from './TransportSelect';
import { BotVisualizer } from './BotVisualizer';
import { LLMProfileSelect } from './LLMProfileSelect';
import { VoiceProfileSelect } from './VoiceProfileSelect';
import { TranscriptExport } from './TranscriptExport';

interface TransportWithDataChannel {
  dc?: RTCDataChannel;
}

// Pre-load the drop cue so it plays instantly on unexpected disconnect.
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
  const hasBeenConnected = useRef(false);
  const transportState = usePipecatClientTransportState();

  const wrappedDisconnect = useCallback(() => {
    userInitiatedDisconnect.current = true;
    handleDisconnect?.();
  }, [handleDisconnect]);

  // Drop cue on unexpected disconnect (ticket 6b60 problem B).
  const cuePlayedForThisSession = useRef(false);

  const playDropCue = useCallback(() => {
    if (cuePlayedForThisSession.current || userInitiatedDisconnect.current) return;
    if (!hasBeenConnected.current) return;
    cuePlayedForThisSession.current = true;
    dropCueAudio.currentTime = 0;
    dropCueAudio.play().catch((err) => {
      console.warn('Drop cue play failed (autoplay policy?):', err);
    });
  }, []);

  // Path 1: transport state change (slow but authoritative).
  // Requires at least 5s of being connected before firing.
  const connectedSince = useRef(0);

  useEffect(() => {
    if (transportState === 'connected' || transportState === 'ready') {
      if (!hasBeenConnected.current) connectedSince.current = Date.now();
      hasBeenConnected.current = true;
      userInitiatedDisconnect.current = false;
      cuePlayedForThisSession.current = false;
    }
    if (
      hasBeenConnected.current &&
      (transportState === 'disconnected' || transportState === 'error') &&
      !userInitiatedDisconnect.current &&
      connectedSince.current > 0 &&
      Date.now() - connectedSince.current > 5000
    ) {
      playDropCue();
    }
  }, [transportState, playDropCue]);

  // Path 2: data-channel pong tracking (fast — ticket 6b60).
  const lastPongRef = useRef(0);

  useEffect(() => {
    if (transportState !== 'connected' && transportState !== 'ready') return;

    let cleanupFn: (() => void) | null = null;
    let cancelled = false;

    const attach = () => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const transport = (client as unknown as { _transport?: TransportWithDataChannel })?._transport;
      const dc: RTCDataChannel | undefined = transport?.dc;
      if (!dc || dc.readyState !== 'open') return false;

      const handler = (ev: MessageEvent) => {
        if (typeof ev.data !== 'string') return;
        try {
          const msg = JSON.parse(ev.data);
          if (msg.type === 'pong') lastPongRef.current = Date.now();
        } catch { /* ignore */ }
      };
      dc.addEventListener('message', handler);
      cleanupFn = () => dc.removeEventListener('message', handler);
      return true;
    };

    let pollInterval: ReturnType<typeof setInterval> | null = null;
    if (!attach() && !cancelled) {
      pollInterval = setInterval(() => {
        if (cancelled || attach()) {
          if (pollInterval) clearInterval(pollInterval);
        }
      }, 200);
    }

    return () => {
      cancelled = true;
      if (pollInterval) clearInterval(pollInterval);
      cleanupFn?.();
      lastPongRef.current = 0;
    };
  }, [client, transportState]);

  useEffect(() => {
    if (transportState !== 'connected' && transportState !== 'ready') return;
    const interval = setInterval(() => {
      if (lastPongRef.current > 0 && Date.now() - lastPongRef.current > 3000) {
        playDropCue();
      }
    }, 1000);
    return () => clearInterval(interval);
  }, [transportState, playDropCue]);

  useEffect(() => {
    if (!client) return;
    const levels: Record<string, number> = { none: 0, error: 1, warn: 2, info: 3, debug: 4 };
    const level = levels[(import.meta.env.VITE_PIPECAT_LOG_LEVEL || 'warn').toLowerCase()] ?? 2;
    try { client.setLogLevel(level); } catch { /* older SDK */ }
  }, [client]);

  useEffect(() => {
    if (client) {
      client.initDevices().then(() => {
        setDevicesReady(true);
      }).catch(err => {
        console.error('Failed to initialize devices:', err);
        setDevicesReady(true);
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
          <LLMProfileSelect />
          <VoiceProfileSelect />
          {showTransportSelector ? (
            <TransportSelect
              transportType={transportType}
              onTransportChange={onTransportChange}
              availableTransports={availableTransports}
            />
          ) : null}
        </div>
        <div className="flex items-center gap-4">
          <TranscriptExport />
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
          <ConversationPanelWithReasoning />
        </div>
      </div>
    </div>
  );
};

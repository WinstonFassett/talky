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

  // Track whether we've ever been connected — the initial state is
  // "disconnected" before the user connects, and we must not fire
  // the drop cue for that initial state.
  const hasBeenConnected = useRef(false);

  const transportState = usePipecatClientTransportState();

  // Wrap handleDisconnect to flag user-initiated disconnects so the
  // drop-cue logic can distinguish them from unexpected drops.
  const wrappedDisconnect = useCallback(() => {
    userInitiatedDisconnect.current = true;
    handleDisconnect?.();
  }, [handleDisconnect]);

  // Play drop cue on unexpected disconnect (ticket 6b60 problem B).
  // Two detection paths:
  //   1. Transport state → disconnected/error (pipecat SDK, can be slow)
  //   2. HTTP heartbeat to the daemon (fast, 2s detection)
  // Either one fires the cue if the user didn't click disconnect.
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
  useEffect(() => {
    if (transportState === 'connected' || transportState === 'ready') {
      hasBeenConnected.current = true;
      userInitiatedDisconnect.current = false;
      cuePlayedForThisSession.current = false;
    }
    if (
      hasBeenConnected.current &&
      (transportState === 'disconnected' || transportState === 'error') &&
      !userInitiatedDisconnect.current
    ) {
      playDropCue();
    }
  }, [transportState, playDropCue]);

  // Path 2: HTTP heartbeat (fast). While connected, ping the daemon
  // every 2s. Two consecutive failures → fire the cue immediately
  // instead of waiting for ICE to notice.
  useEffect(() => {
    if (transportState !== 'connected' && transportState !== 'ready') return;

    let failures = 0;
    let alive = true;
    const interval = setInterval(async () => {
      if (!alive) return;
      try {
        const r = await fetch('/status', { method: 'HEAD', signal: AbortSignal.timeout(1500) });
        if (r.ok) { failures = 0; return; }
      } catch { /* network error or timeout */ }
      failures++;
      if (failures >= 2 && alive) {
        alive = false;
        playDropCue();
      }
    }, 2000);

    return () => { alive = false; clearInterval(interval); };
  }, [transportState, playDropCue]);

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

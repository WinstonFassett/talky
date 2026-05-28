import { useCallback, useEffect, useRef, useState } from 'react';

import type { PipecatBaseChildProps } from '@pipecat-ai/voice-ui-kit';
import { ConnectButton } from '@pipecat-ai/voice-ui-kit';
import { AudioControl } from './AudioControl';
import { ConversationPanelWithReasoning } from './ConversationPanelWithReasoning';
import { usePipecatClientTransportState } from '@pipecat-ai/client-react';

import type { TransportType } from '../config';
import { TransportSelect } from './TransportSelect';
import { BotVisualizer } from './BotVisualizer';
import { LLMProfileSelect } from './LLMProfileSelect';
import { VoiceProfileSelect } from './VoiceProfileSelect';
import { SessionSheet } from './SessionSheet';
import { PermissionBanner } from './PermissionBanner';
import { StatusBadge } from './StatusBadge';
import { useVoiceState } from './useVoiceState';
import { MoreMenu } from './MoreMenu';
import { EmptyState } from './EmptyState';
import { isDevRoute, useUrlParam } from '../fixtures/harness';
import { useTalkyMessages } from '../messages/useTalkyMessages';
import { PhoneIcon, PhoneOffIcon } from 'lucide-react';

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

function useMediaQuery(query: string): boolean {
  const [match, setMatch] = useState(() =>
    typeof window === 'undefined' ? false : window.matchMedia(query).matches,
  );
  useEffect(() => {
    const mq = window.matchMedia(query);
    const handler = (e: MediaQueryListEvent) => setMatch(e.matches);
    mq.addEventListener('change', handler);
    setMatch(mq.matches);
    return () => mq.removeEventListener('change', handler);
  }, [query]);
  return match;
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
  const [activeProfile, setActiveProfile] = useState('');
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

  // Track active profile (name + label).
  const [profileLabels, setProfileLabels] = useState<Record<string, string>>({});
  useEffect(() => {
    const es = new EventSource('/api/events');
    es.addEventListener('init', (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        const profiles = data.profiles as Array<{ name: string; label: string; active: boolean }> | undefined;
        if (profiles) {
          setProfileLabels(Object.fromEntries(profiles.map((p) => [p.name, p.label])));
          const active = profiles.find((p) => p.active);
          if (active) setActiveProfile(active.name);
        }
      } catch { /* ignore */ }
    });
    es.addEventListener('profileChanged', (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        if (data.type === 'llm' && data.profile) setActiveProfile(data.profile as string);
      } catch { /* ignore */ }
    });
    return () => es.close();
  }, []);
  const activeProfileLabel = profileLabels[activeProfile] ?? activeProfile;

  useEffect(() => {
    if (!client) return;
    setDevicesReady(true);
    // Populate availableMics / selectedMic so the audio control is live pre-connect.
    // initDevices() does getUserMedia + enumerateDevices and moves transport state to
    // "initialized", which also clears voice-ui-kit's spinner gate. connect() skips
    // its own initDevices when state is already past "disconnected".
    client.initDevices().catch((err) => {
      console.warn('initDevices failed (mic permission denied?):', err);
    });
  }, [client]);

  useEffect(() => {
    if (autoconnect && client && handleConnect && !autoconnectAttempted.current && devicesReady) {
      autoconnectAttempted.current = true;
      handleConnect();
    }
  }, [autoconnect, client, handleConnect, devicesReady]);

  const showTransportSelector = availableTransports.length > 1;
  const transportConnected = transportState === 'connected' || transportState === 'ready';
  const voiceState = useVoiceState(client, transportConnected);
  const isNarrow = useMediaQuery('(max-width: 640px)');

  // Show transcript (over EmptyState) whenever we're connected OR a dev fixture is mounted.
  const fixtureName = useUrlParam('fixture');
  const messages = useTalkyMessages();
  const showTranscript = transportConnected || messages.length > 0 || isDevRoute() || !!fixtureName;
  const showHeader = showTranscript;

  return (
    <div className="flex flex-col w-full h-full bg-background text-foreground">
      <PermissionBanner />
      {showHeader && (
      <header
        className="flex items-center shrink-0 border-b gap-2 pl-2 pr-2 sm:pr-4 min-h-12 sm:min-h-16"
        style={{
          borderColor: 'var(--color-border-soft)',
          backgroundColor: 'var(--color-card)',
        }}
      >
        {/* 1. Visualizer (+ status badge on desktop) — fixed left, doubles as MoreMenu trigger. */}
        <div className="flex items-center gap-2 shrink-0">
          <MoreMenu trigger={<BotVisualizer client={client} />} />
          {!isNarrow && <StatusBadge state={voiceState} />}
        </div>

        {/* 2. Session controls. Mobile: single Session button (sheet). Desktop: centered inline pickers. */}
        <div className="flex items-center gap-1.5 sm:gap-2 min-w-0 flex-1 sm:justify-center">
          {isNarrow ? (
            <div className="min-w-0 flex-1">
              <SessionSheet currentLabel={activeProfile ? activeProfileLabel : undefined} />
            </div>
          ) : (
            <>
              <div className="shrink-0">
                <LLMProfileSelect />
              </div>
              <div className="shrink-0">
                <VoiceProfileSelect />
              </div>
              {showTransportSelector ? (
                <TransportSelect
                  transportType={transportType}
                  onTransportChange={onTransportChange}
                  availableTransports={availableTransports}
                />
              ) : null}
            </>
          )}
        </div>

        {/* 3-5. Right cluster — audio (desktop only) · connect · more */}
        <div className="flex items-center gap-1.5 sm:gap-2 shrink-0">
          {!isNarrow && (
            <AudioControl
              size="md"
              variant="ghost"
              noVisualizer={false}
            />
          )}
          <ConnectButton
            size="md"
            onConnect={handleConnect}
            onDisconnect={wrappedDisconnect}
            stateContent={{
              disconnected: {
                children: isNarrow ? <PhoneIcon size={16} /> : 'Connect',
                variant: 'active',
                className: isNarrow ? 'connect-go aspect-square px-0' : 'connect-go',
              },
              initialized: {
                children: isNarrow ? <PhoneIcon size={16} /> : 'Connect',
                variant: 'active',
                className: isNarrow ? 'connect-go aspect-square px-0' : 'connect-go',
              },
              ready: {
                children: isNarrow ? <PhoneOffIcon size={16} /> : 'Disconnect',
                variant: 'destructive',
                className: isNarrow ? 'connect-stop aspect-square px-0' : 'connect-stop',
              },
              connected: {
                children: isNarrow ? <PhoneOffIcon size={16} /> : 'Disconnect',
                variant: 'destructive',
                className: isNarrow ? 'connect-stop aspect-square px-0' : 'connect-stop',
              },
              connecting: { children: isNarrow ? '…' : 'Connecting…', variant: 'secondary' },
              initializing: { children: isNarrow ? '…' : 'Initializing…', variant: 'secondary' },
              disconnecting: { children: isNarrow ? '…' : 'Disconnecting…', variant: 'secondary' },
              error: { children: 'Error', variant: 'destructive' },
            }}
          />
        </div>
      </header>
      )}

      <main className="flex-1 overflow-hidden flex flex-col">
        <div className="h-full mx-auto flex flex-col w-full" style={{ maxWidth: 600 }}>
          {showTranscript ? (
            <ConversationPanelWithReasoning activeProfile={activeProfile} />
          ) : (
            <EmptyState onConnect={handleConnect} />
          )}
        </div>
      </main>
    </div>
  );
};

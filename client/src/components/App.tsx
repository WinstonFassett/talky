import { useCallback, useEffect, useRef, useState } from 'react';

import type { PipecatBaseChildProps } from '@pipecat-ai/voice-ui-kit';
import { ConversationPanelWithReasoning } from './ConversationPanelWithReasoning';
import { usePipecatClientTransportState } from '@pipecat-ai/client-react';

import type { TransportType } from '../config';
import { LLMProfileSelect } from './LLMProfileSelect';
import { PermissionBanner } from './PermissionBanner';
import { StatusBadge } from './StatusBadge';
import { useVoiceState } from './useVoiceState';
import { MoreMenu } from './MoreMenu';
import { MuteButton } from './MuteButton';
import { StatusBar } from './StatusBar';
import { EmptyState } from './EmptyState';
import { isDevRoute, useUrlParam } from '../fixtures/harness';
import { useTalkyMessages } from '../messages/useTalkyMessages';

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
  autoconnect = false,
}: AppProps) => {
  const autoconnectAttempted = useRef(false);
  const userInitiatedDisconnect = useRef(false);
  const [activeProfile, setActiveProfile] = useState('');
  const hasBeenConnected = useRef(false);
  const transportState = usePipecatClientTransportState();

  // initDevices() opens the mic stream — gated on user intent to connect, so
  // the mic indicator doesn't stay lit between calls. Pipecat's disconnect()
  // releases the stream automatically.
  const wrappedConnect = useCallback(async () => {
    if (!client || !handleConnect) return;
    try {
      await client.initDevices();
    } catch (err) {
      console.warn('initDevices failed (mic permission denied?):', err);
    }
    handleConnect();
  }, [client, handleConnect]);

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
    if (autoconnect && client && !autoconnectAttempted.current) {
      autoconnectAttempted.current = true;
      wrappedConnect();
    }
  }, [autoconnect, client, wrappedConnect]);

  const transportConnected = transportState === 'connected' || transportState === 'ready';
  const transportConnecting =
    transportState === 'initializing' ||
    transportState === 'authenticating' ||
    transportState === 'connecting';
  const voiceState = useVoiceState(client, transportConnected, transportConnecting);
  const isNarrow = useMediaQuery('(max-width: 640px)');
  // Below ~900px the status label collapses to a dot; below 640px (isNarrow)
  // the StatusBadge is hidden entirely.
  const isMedium = useMediaQuery('(max-width: 900px)');

  // Show transcript (over EmptyState) whenever we're connected, mid-handshake,
  // OR a dev fixture is mounted. Including the connecting states means the
  // header (which renders the ConnectButton's "Connecting…" label) is visible
  // during autoconnect instead of a silent EmptyState gap (0f94).
  const fixtureName = useUrlParam('fixture');
  const messages = useTalkyMessages();
  const showTranscript =
    transportConnected || transportConnecting || messages.length > 0 || isDevRoute() || !!fixtureName;
  const showHeader = showTranscript;

  return (
    <div className="flex flex-col w-full h-full bg-background text-foreground">
      <PermissionBanner />
      {showHeader && (
      <header
        className="flex items-center shrink-0 border-b gap-2 pl-2 pr-2 sm:pr-3 h-10"
        style={{
          borderColor: 'var(--color-border-soft)',
          backgroundColor: 'var(--color-card)',
        }}
      >
        {/* Left: bare profile-label title trigger → opens the LLM picker. */}
        <div className="flex items-center min-w-0 flex-1">
          <LLMProfileSelect variant="title" />
        </div>

        {/* Right cluster (locked order): Status → Mute → More.
            Status label drops to dot-only below ~900px, hidden below 640px. */}
        <div className="flex items-center gap-0.5 shrink-0">
          {!isNarrow && <StatusBadge state={voiceState} compact={isMedium} />}
          <MuteButton />
          <MoreMenu />
        </div>
      </header>
      )}

      <main className="flex-1 overflow-hidden flex flex-col">
        <div className="h-full mx-auto flex flex-col w-full" style={{ maxWidth: 600 }}>
          {showTranscript ? (
            <ConversationPanelWithReasoning
              activeProfile={activeProfile}
              voiceState={voiceState}
              connected={transportConnected}
              connecting={transportConnecting}
              error={transportState === 'error'}
              onConnect={wrappedConnect}
              onDisconnect={wrappedDisconnect}
            />
          ) : (
            <EmptyState onConnect={wrappedConnect} />
          )}
        </div>
      </main>

      {showHeader && (
        <StatusBar
          connected={transportConnected}
          isNarrow={isNarrow}
          currentLabel={activeProfile ? activeProfileLabel : undefined}
        />
      )}
    </div>
  );
};

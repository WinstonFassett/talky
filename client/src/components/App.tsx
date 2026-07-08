import { useCallback, useEffect, useRef, useState } from 'react';

import type { PipecatBaseChildProps } from '@pipecat-ai/voice-ui-kit';
import { ConversationPanelWithReasoning } from './ConversationPanelWithReasoning';
import { usePipecatClientTransportState } from '@pipecat-ai/client-react';

import type { TransportType } from '../config';
import { PermissionBanner } from './PermissionBanner';
import { StatusBadge } from './StatusBadge';
import { useVoiceState } from './useVoiceState';
import { MoreMenu } from './MoreMenu';
import { SpeakerMuteButton } from './SpeakerMuteButton';
import { MuteButton } from './MuteButton';
import { AudioSettingsButton } from './audio/AudioSettingsButton';
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
    setBackendError(null);
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
  const [backendError, setBackendError] = useState<string | null>(null);
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
    es.addEventListener('backendError', (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        if (data.message) setBackendError(data.message as string);
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
  // Header status (dot + label) shows at ALL widths now — neither the old
  // ≤900px dot-only collapse nor a header hide earned their keep; both dropped
  // the label where the user expects it. (Restored per redesign feedback.)
  // isNarrow still drives StatusBar's own footer compaction below.
  const isNarrow = useMediaQuery('(max-width: 640px)');

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
        className="grid items-center shrink-0 border-b gap-2 pl-2 pr-1 sm:pr-2 h-9"
        style={{
          gridTemplateColumns: '1fr auto 1fr',
          borderColor: 'var(--color-border-soft)',
          backgroundColor: 'var(--color-card)',
        }}
      >
        {/* Left: voice status indicator (dot + label) — shown at all widths. */}
        <div className="flex items-center min-w-0 justify-self-start">
          <StatusBadge state={voiceState} />
        </div>

        {/* Center: session title — bare profile label, read-only. Switching
            lives in the footer picker now, so this is plain text, not a
            trigger. Dropped ≤640px: on small screens the status label keeps
            its spot and this vanity title yields the room. */}
        <div className="flex items-center min-w-0 justify-self-center">
          {!isNarrow && activeProfile && (
            <span
              className="truncate font-medium"
              style={{
                fontSize: '0.9375rem',
                letterSpacing: '-0.01em',
                color: 'var(--color-foreground)',
              }}
              title={activeProfileLabel}
            >
              {activeProfileLabel}
            </span>
          )}
        </div>

        {/* Right: Mic mute · Speaker mute · Settings · More. Both mutes show
            only when connected (no mic/speaker stream to mute otherwise). Mic
            (input) sits before speaker (output), reading left→right as the
            audio path. Mirrors Hermes Desktop's top-right cluster. */}
        <div className="flex items-center gap-0.5 shrink-0 justify-self-end">
          {transportConnected && <MuteButton />}
          {transportConnected && <SpeakerMuteButton />}
          {/* Audio device picker (mic + speaker). The footer shows LLM + Voice
              pickers inline on desktop but no audio devices, so this fills the
              one missing piece of session config once connected. Same dropdown
              as our UserAudioControl repro (AudioPill cold path). */}
          {transportConnected && <AudioSettingsButton />}
          <MoreMenu />
        </div>
      </header>
      )}

      <main className="flex-1 overflow-hidden flex flex-col">
        <div className="h-full mx-auto flex flex-col w-full" style={{ maxWidth: 600 }}>
          {backendError && transportConnected ? (
            <div className="flex flex-col items-center justify-center h-full gap-4 px-6 text-center">
              <div className="text-destructive text-sm font-medium">{backendError}</div>
              <button
                className="px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:opacity-90 transition-opacity"
                onClick={() => {
                  setBackendError(null);
                  wrappedDisconnect();
                  setTimeout(() => wrappedConnect(), 500);
                }}
              >
                Reconnect
              </button>
            </div>
          ) : showTranscript ? (
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

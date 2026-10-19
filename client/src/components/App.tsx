import { useCallback, useEffect, useRef, useState } from 'react';

import type { PipecatBaseChildProps } from '@pipecat-ai/voice-ui-kit';
import { ConversationPanelWithReasoning } from './ConversationPanelWithReasoning';
import { Loader2Icon, MicOffIcon, WifiOffIcon } from 'lucide-react';

import type { TransportType } from '../config';
import { PermissionBanner } from './PermissionBanner';
import { StatusBadge } from './StatusBadge';
import { useVoiceState } from './useVoiceState';
import { MoreMenu } from './MoreMenu';
import { SpeakerMuteButton } from './SpeakerMuteButton';
import { MuteButton } from './MuteButton';
import { AudioSettingsButton } from './audio/AudioSettingsButton';
import { StatusBar } from './StatusBar';
import { HeaderChips } from './HeaderChips';
import { useOptionalSessions } from '../sessions/SessionsProvider';
import { SessionDrawer, SessionRail, SessionsToggle, useRailOpen } from '../sessions/SessionSidebar';
import { CallProvider, useCall } from '../call/CallProvider';
import { TurnProvider } from '../turn/TurnProvider';
import { ComposerProvider } from '../composer/ComposerProvider';
import { useDaemonEvent } from '../lib/daemonEvents';

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

export const App = (props: AppProps) => {
  if (!props.client) return null;
  return (
    <CallProvider client={props.client} transportType={props.transportType}>
      <TurnProvider>
        <ComposerProvider>
          <AppShell {...props} />
        </ComposerProvider>
      </TurnProvider>
    </CallProvider>
  );
};

// Reconnecting / offline, under the header. Connected and disconnected need no
// banner: the header status and the Converse button say it.
function ConnectionBanner() {
  const call = useCall();
  if (call.state !== 'reconnecting' && call.state !== 'offline') return null;
  const offline = call.state === 'offline';
  return (
    <div
      role="status"
      data-testid="connection-status"
      data-state={call.state}
      className="flex items-center justify-center gap-2 px-3 py-1.5 text-[12px] shrink-0"
      style={{
        borderBottom: '1px solid var(--color-border-soft)',
        backgroundColor: 'var(--color-panel-2)',
        color: offline ? 'var(--color-destructive)' : 'var(--color-text-dim)',
      }}
    >
      {offline ? (
        <>
          <WifiOffIcon size={13} aria-hidden="true" />
          <span>Offline: can't reach the Talky daemon.</span>
          <button
            type="button"
            data-testid="connection-retry"
            onClick={call.retry}
            className="rounded px-2 py-0.5 font-medium"
            style={{ border: '1px solid var(--color-border)', color: 'var(--color-foreground)' }}
          >
            Retry
          </button>
        </>
      ) : (
        <>
          <Loader2Icon size={13} className="animate-spin" aria-hidden="true" />
          <span>Reconnecting…</span>
        </>
      )}
    </div>
  );
}

// Mic trouble and why a call ended, under the header. Blocked/no-device show
// Retry; a call that connected but hears nothing names the selected device.
function MicBanner() {
  const call = useCall();
  const { micIssue: m, dropReason } = call;
  let text: string | null = null;
  let retry = false;
  if (m && (call.state === 'connected' || call.state === 'connecting')) {
    retry = true;
    text =
      m.kind === 'blocked'
        ? 'Microphone blocked — allow it for this site, then retry.'
        : m.kind === 'nodevice'
          ? 'No microphone found — plug one in, then retry.'
          : m.kind === 'ended'
            ? `Microphone disconnected${m.label ? `: ${m.label}` : ''}. Retry to pick it up again.`
            : `No mic audio detected${m.label ? ` from ${m.label}` : ''}.`;
    if (m.kind === 'silent') retry = false;
  } else if (dropReason && (call.state === 'disconnected' || call.state === 'offline')) {
    text = `Call ended: ${dropReason}`;
  }
  if (!text) return null;
  return (
    <div
      role="alert"
      data-testid="mic-banner"
      data-kind={m?.kind ?? 'dropped'}
      className="flex items-center justify-center gap-2 px-3 py-1.5 text-[12px] shrink-0"
      style={{
        borderBottom: '1px solid var(--color-border-soft)',
        backgroundColor: 'var(--color-panel-2)',
        color: 'var(--color-destructive)',
      }}
    >
      <MicOffIcon size={13} className="shrink-0" aria-hidden="true" />
      <span className="min-w-0">{text}</span>
      {retry && (
        <button
          type="button"
          data-testid="mic-retry"
          onClick={() => void call.retryMic()}
          className="rounded px-2 py-0.5 font-medium shrink-0"
          style={{ border: '1px solid var(--color-border)', color: 'var(--color-foreground)' }}
        >
          Retry
        </button>
      )}
    </div>
  );
}

const AppShell = ({ client, autoconnect = false }: AppProps) => {
  const call = useCall();
  const autoconnectAttempted = useRef(false);
  const [activeProfile, setActiveProfile] = useState('');

  const connect = useCallback(() => {
    void call.connect();
  }, [call]);

  useEffect(() => {
    if (!client) return;
    const levels: Record<string, number> = { none: 0, error: 1, warn: 2, info: 3, debug: 4 };
    const level = levels[(import.meta.env.VITE_PIPECAT_LOG_LEVEL || 'warn').toLowerCase()] ?? 2;
    try { client.setLogLevel(level); } catch { /* older SDK */ }
  }, [client]);

  // Track active profile (name + label).
  const [profileInfo, setProfileInfo] = useState<Record<string, { label: string; model?: string | null }>>({});
  useDaemonEvent('init', (data: { profiles?: Array<{ name: string; label: string; active: boolean; model?: string | null }> }) => {
    const profiles = data?.profiles;
    if (!profiles) return;
    setProfileInfo(Object.fromEntries(profiles.map((p) => [p.name, { label: p.label, model: p.model }])));
    const active = profiles.find((p) => p.active);
    if (active) setActiveProfile(active.name);
  });
  useDaemonEvent('profileChanged', (data: { type?: string; profile?: string }) => {
    if (data?.type === 'llm' && data.profile) setActiveProfile(data.profile);
  });
  const activeProfileLabel = profileInfo[activeProfile]?.label ?? activeProfile;
  // Chat title for the header: the open chat's title, else the profile label.
  const sessions = useOptionalSessions();
  const openRow = sessions?.rows.find((r) => r.id === sessions.openId);
  const chatTitle = openRow?.title || activeProfileLabel;
  const LINK_LABEL: Record<string, string> = { t3: 'T3', 'claude-code': 'Claude', hermes: 'Hermes' };
  const linkLabel = (openRow?.native_session_id && openRow.backend && LINK_LABEL[openRow.backend]) || null;

  useEffect(() => {
    if (autoconnect && client && !autoconnectAttempted.current) {
      autoconnectAttempted.current = true;
      connect();
    }
  }, [autoconnect, client, connect]);

  const transportConnected = call.state === 'connected';
  const transportConnecting = call.state === 'connecting' || call.state === 'reconnecting';
  const voiceState = useVoiceState(client, transportConnected, transportConnecting);
  // Header status (dot + label) shows at all widths. isNarrow drives the
  // session sheet and StatusBar's own footer compaction.
  const isNarrow = useMediaQuery('(max-width: 640px)');

  const [railOpen, setRailOpen] = useRailOpen();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const showRail = railOpen && !isNarrow;
  const toggleSessions = () => (isNarrow ? setDrawerOpen(true) : setRailOpen(!railOpen));

  return (
    <div className="flex w-full h-full bg-background text-foreground">
      {showRail && <SessionRail onCollapse={() => setRailOpen(false)} />}
      {isNarrow && <SessionDrawer open={drawerOpen} onOpenChange={setDrawerOpen} />}
    <div className="relative flex flex-col flex-1 min-w-0 h-full">
      <PermissionBanner />
      <header
        data-testid="app-header"
        className="flex items-center shrink-0 border-b gap-2 pl-2 pr-1 sm:pr-2 h-9"
        style={{
          borderColor: 'var(--color-border-soft)',
          backgroundColor: 'var(--color-card)',
        }}
      >
        {/* Left: sessions toggle (when the rail is hidden) + voice status. */}
        <div className="flex items-center gap-1 min-w-0 shrink-0">
          {!showRail && <SessionsToggle onClick={toggleSessions} />}
          <StatusBadge state={voiceState} compact={isNarrow} />
        </div>

        {/* Chat title: always visible, also on phone. */}
        <div className="flex items-center min-w-0 flex-1">
          <span
            data-testid="chat-title"
            className="truncate font-medium"
            style={{
              fontSize: '0.9375rem',
              letterSpacing: '-0.01em',
              color: 'var(--color-foreground)',
            }}
            title={chatTitle}
          >
            {chatTitle}
          </span>
        </div>

        {/* Right: Mic mute · Speaker mute · Settings · More. Mutes and
            devices show only when connected. */}
        <div className="flex items-center gap-0.5 shrink-0">
          {transportConnected && <MuteButton />}
          {transportConnected && <SpeakerMuteButton />}
          {transportConnected && <AudioSettingsButton />}
          <MoreMenu />
        </div>
      </header>
      <HeaderChips isNarrow={isNarrow} linkLabel={linkLabel} />
      <ConnectionBanner />
      <MicBanner />

      <main className="flex-1 min-h-0 overflow-hidden flex flex-col">
        <div className="h-full mx-auto flex flex-col w-full min-w-0" style={{ maxWidth: 600 }}>
          <ConversationPanelWithReasoning
            activeProfile={activeProfile}
            voiceState={voiceState}
            connected={transportConnected}
            connecting={transportConnecting}
            error={call.state === 'offline'}
            onConnect={connect}
            onDisconnect={call.disconnect}
          />
        </div>
      </main>

      <StatusBar connected={transportConnected} isNarrow={isNarrow} />
    </div>
    </div>
  );
};

import { useCallback, useEffect, useRef, useState } from 'react';

import type { AggregationMetadata, PipecatBaseChildProps } from '@pipecat-ai/voice-ui-kit';
import {
  ConnectButton,
  ConversationPanel,
  // EventsPanel,
  UserAudioControl,
} from '@pipecat-ai/voice-ui-kit';
import type { JSX } from 'react';
import { usePipecatClientTransportState } from '@pipecat-ai/client-react';

// Internal transport interface for data channel access (not exposed in public API)
interface TransportWithDataChannel {
  dc?: RTCDataChannel;
}

import type { TransportType } from '../config';
import { TransportSelect } from './TransportSelect';
import { BotVisualizer } from './BotVisualizer';
import { LLMProfileSelect } from './LLMProfileSelect';
import { VoiceProfileSelect } from './VoiceProfileSelect';
import { TranscriptExport } from './TranscriptExport';

function splitEventContent(content: string): { summary: string; payload: unknown | null } {
  const i = content.indexOf('\x00');
  if (i < 0) return { summary: content, payload: null };
  const summary = content.slice(0, i);
  const rest = content.slice(i + 1);
  try {
    return { summary, payload: JSON.parse(rest) };
  } catch {
    return { summary, payload: rest };
  }
}

function ToolStart({ content }: { content: string }) {
  const { summary } = splitEventContent(content);
  return (
    <div className="flex items-center gap-1.5 text-xs text-muted-foreground font-mono py-0.5">
      <span className="opacity-50">⟳</span>
      <span>{summary}</span>
    </div>
  );
}

function ToolEnd({ content }: { content: string }) {
  const { summary, payload } = splitEventContent(content);
  const p = typeof payload === 'object' && payload !== null ? payload as Record<string, unknown> : {};
  const isError = !!p.is_error;
  const lines = typeof p.result_lines === 'number' ? p.result_lines : null;
  return (
    <div className={`flex items-center gap-1.5 text-xs font-mono py-0.5 ${isError ? 'text-destructive' : 'text-muted-foreground'}`}>
      <span>{isError ? '✗' : '✓'}</span>
      <span>{summary}{lines != null ? ` (${lines} lines)` : ''}</span>
    </div>
  );
}


function ThinkingBlock({ content }: { content: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="text-xs text-muted-foreground opacity-60 my-0.5">
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-1 cursor-pointer hover:opacity-100 transition-opacity select-none"
      >
        <span>{open ? '▾' : '▸'}</span>
        <span>thinking</span>
      </button>
      {open && (
        <div className="pl-3 border-l border-muted/40 italic whitespace-pre-wrap mt-0.5">
          {content}
        </div>
      )}
    </div>
  );
}

const BOT_OUTPUT_RENDERERS: Record<string, (content: string) => JSX.Element> = {
  tool_start: (content) => <ToolStart content={content} />,
  tool_end:   (content) => <ToolEnd content={content} />,
  thinking: (content) => <ThinkingBlock content={content} />,
  error: (content) => {
    const { summary, payload } = splitEventContent(content);
    return (
      <div className="text-xs font-mono text-destructive py-0.5" title={payload ? JSON.stringify(payload) : undefined}>
        ✗ {summary}
      </div>
    );
  },
  info: (content) => {
    const { summary } = splitEventContent(content);
    return (
      <div className="text-xs text-muted-foreground opacity-50 py-0.5">
        {summary}
      </div>
    );
  },
};

const AGGREGATION_METADATA: Record<string, AggregationMetadata> = {
  tool_start: { isSpoken: false, displayMode: 'block' },
  tool_end:   { isSpoken: false, displayMode: 'block' },
  thinking:   { isSpoken: false, displayMode: 'inline' },
  error:      { isSpoken: false, displayMode: 'block' },
  info:       { isSpoken: false, displayMode: 'block' },
};

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
  // Requires at least 5s of being connected before firing — avoids
  // false positives from transient state changes during initial
  // WebRTC negotiation.
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

  // Path 2: data-channel pong tracking (fast, on the actual WebRTC
  // channel — not a side-channel HTTP request). The server echoes
  // {"type":"pong"} on the data channel for every client ping (1/s).
  // If pongs stop arriving for >3s, the server is dead. Ticket 6b60.
  const lastPongRef = useRef(0);

  // Listen for pong messages directly on the WebRTC data channel.
  // The pipecat SDK routes pongs through its RTVI handler but drops
  // them as "Unrecognized message type" without emitting an event.
  // So we add our own listener on the underlying data channel.
  // Polls for the data channel to appear since it's created async
  // during WebRTC negotiation.
  useEffect(() => {
    if (transportState !== 'connected' && transportState !== 'ready') return;

    let cleanupFn: (() => void) | null = null;
    let cancelled = false;

    const attach = () => {
      // Access the data channel through the transport internals.
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const transport = (client as unknown as { _transport?: TransportWithDataChannel })?._transport;
      const dc: RTCDataChannel | undefined = transport?.dc;
      if (!dc || dc.readyState !== 'open') return false;

      const handler = (ev: MessageEvent) => {
        if (typeof ev.data !== 'string') return;
        try {
          const msg = JSON.parse(ev.data);
          if (msg.type === 'pong') {
            lastPongRef.current = Date.now();
          }
        } catch {
          // Ignore non-JSON messages
        }
      };
      dc.addEventListener('message', handler);
      cleanupFn = () => dc.removeEventListener('message', handler);
      return true;
    };

    // Try immediately, then poll briefly if not ready yet.
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
      // Reset pong tracking to prevent stale values on reconnect
      lastPongRef.current = 0;
    };
  }, [client, transportState]);

  useEffect(() => {
    if (transportState !== 'connected' && transportState !== 'ready') return;

    // Don't start checking until the first pong arrives — avoids
    // false positives during initial connection when the pong path
    // may not be established yet.
    const interval = setInterval(() => {
      if (lastPongRef.current > 0 && Date.now() - lastPongRef.current > 3000) {
        playDropCue();
      }
    }, 1000);

    return () => clearInterval(interval);
  }, [transportState, playDropCue]);

  // Pipecat SDK log level. Default WARN to suppress per-second
  // "received message" noise. Override with VITE_PIPECAT_LOG_LEVEL
  // (none/error/warn/info/debug) for troubleshooting.
  useEffect(() => {
    if (!client) return;
    const levels: Record<string, number> = { none: 0, error: 1, warn: 2, info: 3, debug: 4 };
    const level = levels[(import.meta.env.VITE_PIPECAT_LOG_LEVEL || 'warn').toLowerCase()] ?? 2;
    try { client.setLogLevel(level); } catch { /* older SDK */ }
  }, [client]);

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
      <div className="flex-1 overflow-hidden flex flex-col">
        <div className="flex-1 overflow-hidden px-4">
          <ConversationPanel conversationElementProps={{ botOutputRenderers: BOT_OUTPUT_RENDERERS, aggregationMetadata: AGGREGATION_METADATA }} />
        </div>
      </div>
    </div>
  );
};

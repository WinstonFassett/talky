// The call's connection state, owned by talky rather than voice-ui-kit (WIN-536).
//
// voice-ui-kit's handleConnect turns any failed connect into a page-wide
// ErrorCard, and the transport retries ICE quietly for a long time. Neither
// tells the user the truth. This provider connects with startBotAndConnect
// itself and reports one of:
//
//   disconnected → connecting → connected
//                       ↘ reconnecting (an unexpected drop; a few tries with backoff)
//                              ↘ offline (gave up; Retry starts over, and so
//                                does the daemon coming back)
//
// A drop is noticed from the transport state, or sooner when the daemon's
// event stream drops and the daemon doesn't answer a request. Every
// attempt carries an epoch, so a stale close or a slow attempt from an earlier
// round can never overwrite the state of a newer one.
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import type { PipecatClient } from '@pipecat-ai/client-js';
import { usePipecatClientTransportState } from '@pipecat-ai/client-react';

import { TRANSPORT_CONFIG, type TransportType } from '../config';
import { onDaemonEvent } from '../lib/daemonEvents';

export type CallState = 'disconnected' | 'connecting' | 'connected' | 'reconnecting' | 'offline';

type CallValue = {
  state: CallState;
  /** Start a call. `muted` keeps the mic off (a call started by typing). */
  connect: (opts?: { muted?: boolean }) => Promise<boolean>;
  /** End the call on purpose. */
  disconnect: () => void;
  /** After giving up: try again now. */
  retry: () => void;
};

const CallContext = createContext<CallValue | null>(null);

const RETRY_DELAYS = [0, 1000, 2000, 4000, 8000];
const ATTEMPT_TIMEOUT = 10000;

/** Is the daemon answering at all? Cheap, and fails fast when it's gone. */
const reachable = () =>
  fetch('/api/profiles', { signal: AbortSignal.timeout(2000) })
    .then((r) => r.ok)
    .catch(() => false);

// Pre-load the drop cue so it plays instantly on an unexpected disconnect (6b60).
const dropCue = typeof Audio !== 'undefined' ? new Audio('/cues/drop.wav') : null;
if (dropCue) dropCue.volume = 0.7;

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export function CallProvider({
  client,
  transportType,
  children,
}: {
  client: PipecatClient;
  transportType: TransportType;
  children: ReactNode;
}) {
  const transportState = usePipecatClientTransportState();
  const [mode, setMode] = useState<'normal' | 'reconnecting' | 'offline'>('normal');
  const modeRef = useRef(mode);
  modeRef.current = mode;
  const epoch = useRef(0);
  // True while talky itself tears the transport down (user End, or a drop it noticed).
  const leaving = useRef(false);
  // The user wants to be in a call; cleared by End.
  const wanted = useRef(false);
  const muted = useRef(false);
  const transportRef = useRef(transportState);
  transportRef.current = transportState;

  const connectOnce = useCallback(async (): Promise<boolean> => {
    if (!(await reachable())) return false;
    if (!['initialized', 'disconnected', 'error'].includes(client.state)) {
      leaving.current = true;
      try {
        await client.disconnect();
      } catch { /* already gone */ }
      leaving.current = false;
    }
    try {
      await client.initDevices();
    } catch (err) {
      console.warn('initDevices failed (mic permission denied?):', err);
    }
    try {
      await Promise.race([
        client.startBotAndConnect(TRANSPORT_CONFIG[transportType]),
        sleep(ATTEMPT_TIMEOUT).then(() => {
          throw new Error('timed out');
        }),
      ]);
      if (muted.current) client.enableMic(false);
      return true;
    } catch (err) {
      console.warn('connect failed:', err);
      return false;
    }
  }, [client, transportType]);

  const reconnect = useCallback(async () => {
    const mine = ++epoch.current;
    setMode('reconnecting');
    for (const delay of RETRY_DELAYS) {
      await sleep(delay);
      if (epoch.current !== mine) return;
      const ok = await connectOnce();
      if (epoch.current !== mine) return;
      if (ok) {
        setMode('normal');
        return;
      }
    }
    setMode('offline');
  }, [connectOnce]);

  const connect = useCallback(
    async (opts: { muted?: boolean } = {}) => {
      const mine = ++epoch.current;
      wanted.current = true;
      muted.current = !!opts.muted;
      setMode('normal');
      const ok = await connectOnce();
      return ok && epoch.current === mine;
    },
    [connectOnce],
  );

  const disconnect = useCallback(() => {
    epoch.current++;
    wanted.current = false;
    leaving.current = true;
    setMode('normal');
    void Promise.resolve(client.disconnect())
      .catch(() => {})
      .finally(() => {
        leaving.current = false;
      });
  }, [client]);

  const dropped = useCallback(() => {
    if (!wanted.current || modeRef.current !== 'normal') return;
    if (dropCue) {
      dropCue.currentTime = 0;
      dropCue.play().catch(() => {});
    }
    void reconnect();
  }, [reconnect]);

  // The transport says the call ended, and talky didn't end it.
  const wasConnected = useRef(false);
  useEffect(() => {
    const up = transportState === 'connected' || transportState === 'ready';
    if (up) {
      wasConnected.current = true;
      return;
    }
    if ((transportState === 'disconnected' || transportState === 'error') && wasConnected.current) {
      wasConnected.current = false;
      if (!leaving.current) dropped();
    }
  }, [transportState, dropped]);

  // Faster: the daemon's event stream dropped and the daemon doesn't answer.
  // (The transport keeps reporting "ready" while it retries ICE for a long time.)
  useEffect(
    () =>
      onDaemonEvent('error', () => {
        const t = transportRef.current;
        if (!wanted.current || modeRef.current !== 'normal' || (t !== 'ready' && t !== 'connected')) return;
        void reachable().then((alive) => {
            if (alive || !wanted.current || modeRef.current !== 'normal') return;
            leaving.current = true;
            wasConnected.current = false;
            void Promise.resolve(client.disconnect())
              .catch(() => {})
              .finally(() => {
                leaving.current = false;
              });
            dropped();
          });
      }),
    [client, dropped],
  );

  // Offline, and the daemon is back (its event stream reconnected and sent
  // `init`): reconnect without waiting for Retry.
  useEffect(
    () =>
      onDaemonEvent('init', () => {
        if (modeRef.current === 'offline' && wanted.current) void reconnect();
      }),
    [reconnect],
  );

  // Coming back to a backgrounded tab: if the call died while it was hidden, reconnect.
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState !== 'visible' || !wanted.current || modeRef.current !== 'normal') return;
      const t = transportRef.current;
      if (t !== 'connected' && t !== 'ready' && t !== 'connecting' && t !== 'initializing' && t !== 'authenticating') {
        void reconnect();
      }
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [reconnect]);

  const retry = useCallback(() => {
    wanted.current = true;
    void reconnect();
  }, [reconnect]);

  const state: CallState = useMemo(() => {
    if (mode === 'reconnecting') return 'reconnecting';
    if (mode === 'offline') return 'offline';
    // The bot can take text only once it said it's ready.
    if (transportState === 'ready') return 'connected';
    if (['initializing', 'authenticating', 'authenticated', 'connecting', 'connected'].includes(transportState)) return 'connecting';
    return 'disconnected';
  }, [mode, transportState]);

  const value = useMemo(() => ({ state, connect, disconnect, retry }), [state, connect, disconnect, retry]);
  return <CallContext.Provider value={value}>{children}</CallContext.Provider>;
}

export function useCall(): CallValue {
  const v = useContext(CallContext);
  if (!v) throw new Error('useCall must be used inside <CallProvider>');
  return v;
}

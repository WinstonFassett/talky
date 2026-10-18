// One shared EventSource on the daemon's /api/events for the whole page
// (WIN-536). Browsers allow ~6 HTTP/1.1 connections per origin; one stream per
// component used them up and stalled plain fetches. Each listener gets the
// parsed `data` of its event type. EventSource reconnects by itself when the
// daemon restarts. A listener that subscribes late still gets the last `init`;
// `error` fires (with no data) when the stream drops.
import { useEffect, useRef } from 'react';

type Listener = (data: any) => void; // eslint-disable-line @typescript-eslint/no-explicit-any

let source: EventSource | null = null;
const listeners = new Map<string, Set<Listener>>();
const REPLAYED = new Set(['init']);
const last = new Map<string, unknown>();

function emit(type: string, data: unknown) {
  if (REPLAYED.has(type)) last.set(type, data);
  for (const fn of listeners.get(type) ?? []) fn(data);
}

function ensureSource(): EventSource {
  if (source) return source;
  source = new EventSource('/api/events');
  source.onerror = () => emit('error', undefined);
  return source;
}

function attach(type: string) {
  if (type === 'error') return;
  ensureSource().addEventListener(type, (e: MessageEvent) => {
    let data: unknown;
    try {
      data = JSON.parse(e.data);
    } catch {
      return;
    }
    emit(type, data);
  });
}

export function onDaemonEvent(type: string, fn: Listener): () => void {
  if (!listeners.has(type)) {
    listeners.set(type, new Set());
    attach(type);
  }
  ensureSource();
  listeners.get(type)!.add(fn);
  if (last.has(type)) queueMicrotask(() => listeners.get(type)?.has(fn) && fn(last.get(type)));
  return () => listeners.get(type)?.delete(fn);
}

/** Subscribe for the component's lifetime; the latest `fn` is always called. */
export function useDaemonEvent(type: string, fn: Listener): void {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => onDaemonEvent(type, (d) => ref.current(d)), [type]);
}

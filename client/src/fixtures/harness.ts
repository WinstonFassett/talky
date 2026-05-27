import { useEffect, useState } from 'react';

export type SimulatedVoiceState =
  | 'disconnected'
  | 'idle'
  | 'listening'
  | 'thinking'
  | 'speaking';

const VOICE_STATES: SimulatedVoiceState[] = [
  'disconnected',
  'idle',
  'listening',
  'thinking',
  'speaking',
];

export function isDevRoute(): boolean {
  if (typeof window === 'undefined') return false;
  return window.location.pathname.startsWith('/dev');
}

function readParam(name: string): string | null {
  if (typeof window === 'undefined') return null;
  return new URLSearchParams(window.location.search).get(name);
}

export function readFixtureParam(): string | null {
  return readParam('fixture');
}

export function readVoiceStateParam(): SimulatedVoiceState | null {
  const raw = readParam('voiceState');
  if (raw && (VOICE_STATES as string[]).includes(raw)) {
    return raw as SimulatedVoiceState;
  }
  return null;
}

// Reactive URL params — re-reads on history navigation so the picker can
// update both the URL and dependent components without a full reload.
export function useUrlParam(name: string): string | null {
  const [value, setValue] = useState<string | null>(() => readParam(name));
  useEffect(() => {
    const handler = () => setValue(readParam(name));
    window.addEventListener('popstate', handler);
    window.addEventListener('talky:url-changed', handler);
    return () => {
      window.removeEventListener('popstate', handler);
      window.removeEventListener('talky:url-changed', handler);
    };
  }, [name]);
  return value;
}

export function setUrlParam(name: string, value: string | null): void {
  const url = new URL(window.location.href);
  if (value == null) url.searchParams.delete(name);
  else url.searchParams.set(name, value);
  window.history.replaceState({}, '', url.toString());
  window.dispatchEvent(new Event('talky:url-changed'));
}

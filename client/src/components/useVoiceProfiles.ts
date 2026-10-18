import { useEffect, useState } from 'react';

import { useDaemonEvent } from '../lib/daemonEvents';

export interface VoiceProviderStatus {
  status: 'ready' | 'installable' | 'misconfigured' | 'blocked';
  reason: string;
}

export interface VoiceProfile {
  name: string;
  description: string;
  active: boolean;
  provider?: string;
  tts?: string;
  stt?: string;
  ttsStatus?: VoiceProviderStatus;
  sttStatus?: VoiceProviderStatus;
  ready?: boolean;
}

export function inferProvider(v: VoiceProfile): string {
  if (v.provider) return v.provider;
  const dash = v.name.indexOf('-');
  if (dash > 0) return v.name.slice(0, dash);
  return '';
}

export function useVoiceProfiles() {
  const [voices, setVoices] = useState<VoiceProfile[]>([]);
  const [activeVoice, setActiveVoice] = useState<string>('');
  const [switching, setSwitching] = useState(false);
  const [error, setError] = useState('');

  const applyVoices = (data: VoiceProfile[] | undefined) => {
    if (!Array.isArray(data)) return;
    setVoices(data);
    const active = data.find((v) => v.active);
    if (active) setActiveVoice(active.name);
  };

  useEffect(() => {
    let mounted = true;
    fetch('/api/voices')
      .then((r) => r.json())
      .then((data) => mounted && applyVoices(data.voices))
      .catch(() => mounted && setError('Cannot reach daemon'));
    return () => {
      mounted = false;
    };
  }, []);

  useDaemonEvent('init', (data: { voices?: VoiceProfile[] }) => {
    applyVoices(data?.voices);
    setError('');
  });
  useDaemonEvent('peerConnected', (data: { voices?: VoiceProfile[] }) => applyVoices(data?.voices));
  useDaemonEvent('voiceChanged', (data: { profile?: string }) => {
    if (!data?.profile) return;
    setActiveVoice(data.profile);
    setVoices((prev) => prev.map((v) => ({ ...v, active: v.name === data.profile })));
  });
  useDaemonEvent('error', () => setError('Connection lost - reconnecting...'));

  const switchVoice = async (next: string) => {
    if (!next || next === activeVoice || switching) return;
    setSwitching(true);
    setError('');
    try {
      const resp = await fetch('/api/voices/switch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ profile: next }),
      });
      const data = await resp.json();
      if (!resp.ok) setError(data.error || 'Switch failed');
    } catch {
      setError('Switch request failed');
    } finally {
      setSwitching(false);
    }
  };

  return { voices, activeVoice, switching, error, switchVoice };
}

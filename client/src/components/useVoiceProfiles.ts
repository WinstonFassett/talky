import { useEffect, useState } from 'react';

export interface VoiceProfile {
  name: string;
  description: string;
  active: boolean;
  provider?: string;
  tts?: string;
  stt?: string;
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

  useEffect(() => {
    let mounted = true;
    let eventSource: EventSource | null = null;

    const applyVoices = (data: VoiceProfile[]) => {
      setVoices(data);
      const active = data.find((v) => v.active);
      if (active) setActiveVoice(active.name);
    };

    fetch('/api/voices')
      .then((r) => r.json())
      .then((data) => mounted && applyVoices(data.voices))
      .catch(() => mounted && setError('Cannot reach daemon'));

    eventSource = new EventSource('/api/events');
    eventSource.addEventListener('init', (e: MessageEvent) => {
      if (!mounted) return;
      try {
        const data = JSON.parse(e.data);
        if (data.voices) applyVoices(data.voices);
        setError('');
      } catch {
        setError('Invalid server response');
      }
    });
    eventSource.addEventListener('peerConnected', (e: MessageEvent) => {
      if (!mounted) return;
      try {
        const data = JSON.parse(e.data);
        if (data.voices) applyVoices(data.voices);
      } catch { /* ignore */ }
    });
    eventSource.addEventListener('voiceChanged', (e: MessageEvent) => {
      if (!mounted) return;
      try {
        const data = JSON.parse(e.data);
        setActiveVoice(data.profile);
        setVoices((prev) => prev.map((v) => ({ ...v, active: v.name === data.profile })));
      } catch { /* ignore */ }
    });
    eventSource.onerror = () => mounted && setError('Connection lost - reconnecting...');

    return () => {
      mounted = false;
      eventSource?.close();
    };
  }, []);

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

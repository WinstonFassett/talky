import { useEffect, useState } from 'react';

import { useDaemonEvent } from '../lib/daemonEvents';

export interface LLMProfile {
  name: string;
  backend?: string;
  model?: string | null;
  label: string;
  description: string;
  active: boolean;
  healthy: boolean | null;
  /** Voice profile this profile applies on switch; null/absent = keeps the current voice. */
  voice_profile?: string | null;
  voice?: { name: string; description: string; ready: boolean; tts: string; stt: string } | null;
}

interface ProfilesResponse {
  profiles: LLMProfile[];
  live: boolean;
}

/** The profile's display name: its human label ("T3 Code"), not the id it is keyed by ("t3"). */
export const profileTitle = (p: Pick<LLMProfile, 'name' | 'label' | 'description'>) =>
  p.label !== p.name ? p.label : p.description || p.label;

// A profile is one backend + model (+ the voice active alongside it). Listed by
// /api/profiles, switched with POST /api/profiles/switch.
export function useLLMProfiles() {
  const [profiles, setProfiles] = useState<LLMProfile[]>([]);
  const [activeProfile, setActiveProfile] = useState('');
  const [switching, setSwitching] = useState(false);
  const [error, setError] = useState('');

  const applyProfiles = (data: ProfilesResponse) => {
    if (!Array.isArray(data?.profiles)) return;
    setProfiles(data.profiles);
    const active = data.profiles.find((p) => p.active);
    if (active) setActiveProfile(active.name);
  };

  useEffect(() => {
    let mounted = true;
    fetch('/api/profiles')
      .then((r) => r.json())
      .then((data: ProfilesResponse) => mounted && applyProfiles(data))
      .catch(() => mounted && setError('Cannot reach daemon'));
    return () => {
      mounted = false;
    };
  }, []);

  useDaemonEvent('init', (data: ProfilesResponse) => {
    applyProfiles(data);
    setError('');
  });
  useDaemonEvent('profileChanged', (data: { type?: string; profile?: string }) => {
    if (data?.type !== 'llm' || !data.profile) return;
    setActiveProfile(data.profile);
    setProfiles((prev) => prev.map((p) => ({ ...p, active: p.name === data.profile })));
  });
  useDaemonEvent('peerConnected', (data: ProfilesResponse) => applyProfiles(data));
  useDaemonEvent('healthChanged', (data: { backend?: string; healthy?: boolean }) => {
    setProfiles((prev) => prev.map((p) => (p.name === data?.backend ? { ...p, healthy: !!data.healthy } : p)));
  });
  useDaemonEvent('error', () => setError('Connection lost - reconnecting...'));

  const switchTo = async (next: string) => {
    if (!next || next === activeProfile || switching) return;
    if (profiles.find((p) => p.name === next)?.healthy === false) return;
    setSwitching(true);
    setError('');
    try {
      const resp = await fetch('/api/profiles/switch', {
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

  const current = profiles.find((p) => p.name === activeProfile) ?? profiles[0];
  return { profiles, current, switching, error, switchTo };
}

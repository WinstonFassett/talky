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
}

interface ProfilesResponse {
  profiles: LLMProfile[];
  live: boolean;
}

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

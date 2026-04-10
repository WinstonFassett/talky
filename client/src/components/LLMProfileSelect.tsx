import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@pipecat-ai/voice-ui-kit';
import { useEffect, useState } from 'react';

interface LLMProfile {
  name: string;
  label: string;
  description: string;
  active: boolean;
  healthy: boolean | null;
}

interface ProfilesResponse {
  profiles: LLMProfile[];
  live: boolean;
}

export const LLMProfileSelect = () => {
  const [profiles, setProfiles] = useState<LLMProfile[]>([]);
  const [activeProfile, setActiveProfile] = useState<string>('');
  const [switching, setSwitching] = useState(false);
  const [error, setError] = useState('');

  // Fetch profiles on mount, subscribe to SSE for live updates.
  useEffect(() => {
    let eventSource: EventSource | null = null;
    let mounted = true;

    const applyProfiles = (data: ProfilesResponse) => {
      setProfiles(data.profiles);
      const active = data.profiles.find((p) => p.active);
      if (active) setActiveProfile(active.name);
    };

    // Initial REST fetch (fast, reliable).
    fetch('/api/profiles')
      .then((r) => r.json())
      .then((data: ProfilesResponse) => {
        if (mounted) applyProfiles(data);
      })
      .catch(() => {
        if (mounted) setError('Cannot reach daemon');
      });

    // SSE subscription for live push.
    eventSource = new EventSource('/api/events');

    eventSource.addEventListener('init', (e: MessageEvent) => {
      if (!mounted) return;
      applyProfiles(JSON.parse(e.data));
      setError('');
    });

    eventSource.addEventListener('profileChanged', (e: MessageEvent) => {
      if (!mounted) return;
      const data = JSON.parse(e.data);
      if (data.type === 'llm') {
        setActiveProfile(data.profile);
        setProfiles((prev) =>
          prev.map((p) => ({ ...p, active: p.name === data.profile }))
        );
      }
    });

    eventSource.addEventListener('peerConnected', (e: MessageEvent) => {
      if (!mounted) return;
      applyProfiles(JSON.parse(e.data));
    });

    eventSource.addEventListener('healthChanged', (e: MessageEvent) => {
      if (!mounted) return;
      const data = JSON.parse(e.data);
      setProfiles((prev) =>
        prev.map((p) =>
          p.name === data.backend ? { ...p, healthy: data.healthy } : p
        )
      );
    });

    eventSource.onerror = () => {
      // EventSource auto-reconnects.
      if (mounted) setError('');
    };

    return () => {
      mounted = false;
      eventSource?.close();
    };
  }, []);

  const handleSwitch = async (profileName: string) => {
    if (profileName === activeProfile || switching) return;

    setSwitching(true);
    setError('');

    try {
      const resp = await fetch('/api/profiles/switch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ profile: profileName }),
      });
      const data = await resp.json();

      if (!resp.ok) {
        setError(data.error || 'Switch failed');
      }
      // On success the SSE profileChanged event updates state.
    } catch {
      setError('Switch request failed');
    } finally {
      setSwitching(false);
    }
  };

  if (profiles.length === 0 && !error) return null;

  if (error) {
    return (
      <div className="flex items-center gap-2 text-red-500 text-sm">
        <span>{error}</span>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <label htmlFor="llm-profile-select" className="text-sm font-medium text-gray-700">
        Profile:
      </label>
      <Select
        value={activeProfile}
        onValueChange={handleSwitch}
        disabled={switching}
      >
        <SelectTrigger className="w-48" id="llm-profile-select">
          <SelectValue placeholder="Select profile" />
        </SelectTrigger>
        <SelectContent>
          {profiles.map((profile) => (
            <SelectItem
              key={profile.name}
              value={profile.name}
              disabled={profile.healthy === false}
              textValue={profile.label}
            >
              <div className="flex flex-col">
                <span className="font-medium">{profile.label}</span>
                <span className="text-xs text-gray-500">{profile.description}</span>
              </div>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
};

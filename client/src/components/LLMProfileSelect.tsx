import { useEffect, useState } from 'react';
import { CheckIcon, ChevronsUpDownIcon } from 'lucide-react';

import {
  Combobox,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxGroup,
  ComboboxInput,
  ComboboxItem,
  ComboboxList,
  ComboboxTrigger,
} from './kibo-ui/combobox';

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
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let mounted = true;
    let eventSource: EventSource | null = null;

    const applyProfiles = (data: ProfilesResponse) => {
      setProfiles(data.profiles);
      const active = data.profiles.find((p) => p.active);
      if (active) setActiveProfile(active.name);
    };

    fetch('/api/profiles')
      .then((r) => r.json())
      .then((data: ProfilesResponse) => {
        if (mounted) applyProfiles(data);
      })
      .catch(() => mounted && setError('Cannot reach daemon'));

    eventSource = new EventSource('/api/events');
    eventSource.addEventListener('init', (e: MessageEvent) => {
      if (!mounted) return;
      try {
        applyProfiles(JSON.parse(e.data));
        setError('');
      } catch {
        setError('Invalid server response');
      }
    });
    eventSource.addEventListener('profileChanged', (e: MessageEvent) => {
      if (!mounted) return;
      try {
        const data = JSON.parse(e.data);
        if (data.type === 'llm') {
          setActiveProfile(data.profile);
          setProfiles((prev) =>
            prev.map((p) => ({ ...p, active: p.name === data.profile })),
          );
        }
      } catch { /* ignore */ }
    });
    eventSource.addEventListener('peerConnected', (e: MessageEvent) => {
      if (!mounted) return;
      try { applyProfiles(JSON.parse(e.data)); } catch { /* ignore */ }
    });
    eventSource.addEventListener('healthChanged', (e: MessageEvent) => {
      if (!mounted) return;
      try {
        const data = JSON.parse(e.data);
        setProfiles((prev) =>
          prev.map((p) => (p.name === data.backend ? { ...p, healthy: data.healthy } : p)),
        );
      } catch { /* ignore */ }
    });
    eventSource.onerror = () => mounted && setError('Connection lost - reconnecting...');

    return () => {
      mounted = false;
      eventSource?.close();
    };
  }, []);

  const handleSwitch = async (next: string) => {
    if (!next || next === activeProfile || switching) return;
    const target = profiles.find((p) => p.name === next);
    if (target?.healthy === false) return;
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

  if (error && profiles.length === 0) {
    return <span className="text-xs text-destructive">{error}</span>;
  }
  if (profiles.length === 0) return null;

  const current = profiles.find((p) => p.name === activeProfile) ?? profiles[0];
  const comboData = profiles.map((p) => ({ value: p.name, label: p.label }));

  return (
    <Combobox
      data={comboData}
      type="profile"
      value={activeProfile}
      onValueChange={handleSwitch}
      open={open}
      onOpenChange={setOpen}
    >
      <ComboboxTrigger
        size="sm"
        disabled={switching}
        className="h-8 gap-2 font-medium"
        title="Switch profile"
      >
        <StatusDot healthy={current.healthy} />
        <span className="max-w-[140px] truncate">{current.label}</span>
        <ChevronsUpDownIcon size={12} className="shrink-0 opacity-50" />
      </ComboboxTrigger>
      <ComboboxContent className="min-w-[280px]" popoverOptions={{ align: 'start' }}>
        <ComboboxInput placeholder="Search profiles…" />
        <ComboboxList>
          <ComboboxEmpty />
          <ComboboxGroup heading="Profile">
            {profiles.map((p) => {
              const unhealthy = p.healthy === false;
              const selected = p.name === activeProfile;
              return (
                <ComboboxItem
                  key={p.name}
                  value={p.name}
                  disabled={unhealthy}
                  className="flex items-start gap-2 py-2"
                >
                  <StatusDot healthy={p.healthy} className="mt-1.5" />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-sm">{p.label}</span>
                      {selected && (
                        <CheckIcon
                          size={13}
                          className="ml-auto"
                          style={{ color: 'var(--color-accent)' }}
                        />
                      )}
                    </div>
                    {p.description && (
                      <span className="text-[11px] text-muted-foreground">
                        {p.description}
                      </span>
                    )}
                  </div>
                </ComboboxItem>
              );
            })}
          </ComboboxGroup>
        </ComboboxList>
      </ComboboxContent>
    </Combobox>
  );
};

function StatusDot({ healthy, className }: { healthy: boolean | null; className?: string }) {
  const color =
    healthy === false
      ? 'var(--color-warning)'
      : healthy === null
        ? 'var(--color-text-mute)'
        : 'var(--color-success)';
  return (
    <span
      className={`inline-block size-1.5 rounded-full shrink-0 ${className ?? ''}`}
      style={{ backgroundColor: color }}
    />
  );
}

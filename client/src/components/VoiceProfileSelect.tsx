import { useEffect, useState } from 'react';
import { CheckIcon, ChevronDownIcon, VolumeIcon } from 'lucide-react';

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
import { PickerTrigger } from './PickerTrigger';

interface VoiceProfile {
  name: string;
  description: string;
  active: boolean;
  /** Provider hint (e.g. cartesia, kokoro). Optional — derived from description if absent. */
  provider?: string;
  /** STT engine label, if surfaced. Optional. */
  stt?: string;
}

function inferProvider(v: VoiceProfile): string {
  if (v.provider) return v.provider;
  // Best-effort: first token of the name before a dash, e.g. "cartesia-keith" -> "cartesia".
  const dash = v.name.indexOf('-');
  if (dash > 0) return v.name.slice(0, dash);
  return '';
}

export const VoiceProfileSelect = () => {
  const [voices, setVoices] = useState<VoiceProfile[]>([]);
  const [activeVoice, setActiveVoice] = useState<string>('');
  const [switching, setSwitching] = useState(false);
  const [error, setError] = useState('');
  const [open, setOpen] = useState(false);

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

  const handleSwitch = async (next: string) => {
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

  if (error && voices.length === 0) {
    return <span className="text-xs text-destructive">{error}</span>;
  }
  if (voices.length === 0) return null;

  const current = voices.find((v) => v.name === activeVoice) ?? voices[0];
  const currentProvider = inferProvider(current);
  const comboData = voices.map((v) => ({ value: v.name, label: v.description || v.name }));

  return (
    <Combobox
      data={comboData}
      type="voice"
      value={activeVoice}
      onValueChange={handleSwitch}
      open={open}
      onOpenChange={setOpen}
    >
      <ComboboxTrigger asChild>
        <PickerTrigger
          open={open}
          disabled={switching}
          title="Switch voice profile"
        >
          <VolumeIcon
            size={12}
            style={{ color: 'var(--color-text-mute)' }}
            className="shrink-0"
          />
          <span className="max-w-[140px] truncate">
            {current.description || current.name}
          </span>
          {currentProvider && (
            <span className="font-mono text-[10px] uppercase tracking-[0.04em] text-[var(--color-text-mute)]">
              {currentProvider}
            </span>
          )}
          <ChevronDownIcon
            size={11}
            style={{ color: 'var(--color-text-mute)' }}
            className="shrink-0"
          />
        </PickerTrigger>
      </ComboboxTrigger>
      <ComboboxContent className="min-w-[320px]" popoverOptions={{ align: 'start' }}>
        <ComboboxInput placeholder="Search voices, providers…" />
        <ComboboxList>
          <ComboboxEmpty />
          <ComboboxGroup heading="Voice profile">
            {voices.map((v) => {
              const selected = v.name === activeVoice;
              const provider = inferProvider(v);
              return (
                <ComboboxItem
                  key={v.name}
                  value={v.name}
                  className="flex items-start gap-2 py-2"
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-sm">{v.description || v.name}</span>
                      {provider && (
                        <span className="font-mono text-[10px] uppercase tracking-wider opacity-50">
                          {provider}
                        </span>
                      )}
                      {selected && (
                        <CheckIcon
                          size={13}
                          className="ml-auto"
                          style={{ color: 'var(--color-accent)' }}
                        />
                      )}
                    </div>
                    {v.stt && (
                      <span className="text-[11px] text-muted-foreground">
                        STT: {v.stt}
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

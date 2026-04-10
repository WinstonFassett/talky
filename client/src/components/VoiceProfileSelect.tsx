import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@pipecat-ai/voice-ui-kit';
import { useEffect, useState } from 'react';

interface VoiceProfile {
  name: string;
  description: string;
  active: boolean;
}

export const VoiceProfileSelect = () => {
  const [voices, setVoices] = useState<VoiceProfile[]>([]);
  const [activeVoice, setActiveVoice] = useState<string>('');
  const [switching, setSwitching] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let eventSource: EventSource | null = null;
    let mounted = true;

    const applyVoices = (data: VoiceProfile[]) => {
      setVoices(data);
      const active = data.find((v) => v.active);
      if (active) setActiveVoice(active.name);
    };

    // Initial REST fetch.
    fetch('/api/voices')
      .then((r) => r.json())
      .then((data) => {
        if (mounted) applyVoices(data.voices);
      })
      .catch(() => {
        if (mounted) setError('Cannot reach daemon');
      });

    // SSE subscription — reuse the same /api/events stream.
    eventSource = new EventSource('/api/events');

    eventSource.addEventListener('init', (e: MessageEvent) => {
      if (!mounted) return;
      const data = JSON.parse(e.data);
      if (data.voices) applyVoices(data.voices);
      setError('');
    });

    eventSource.addEventListener('voiceChanged', (e: MessageEvent) => {
      if (!mounted) return;
      const data = JSON.parse(e.data);
      setActiveVoice(data.profile);
      setVoices((prev) =>
        prev.map((v) => ({ ...v, active: v.name === data.profile }))
      );
    });

    eventSource.onerror = () => {
      if (mounted) setError('');
    };

    return () => {
      mounted = false;
      eventSource?.close();
    };
  }, []);

  const handleSwitch = async (profileName: string) => {
    if (profileName === activeVoice || switching) return;

    setSwitching(true);
    setError('');

    try {
      const resp = await fetch('/api/voices/switch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ profile: profileName }),
      });
      const data = await resp.json();

      if (!resp.ok) {
        setError(data.error || 'Switch failed');
      }
      // SSE voiceChanged event updates state on success.
    } catch {
      setError('Switch request failed');
    } finally {
      setSwitching(false);
    }
  };

  if (voices.length === 0 && !error) return null;

  if (error) {
    return (
      <div className="flex items-center gap-2 text-red-500 text-sm">
        <span>{error}</span>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <label htmlFor="voice-profile-select" className="text-sm font-medium text-gray-700">
        Voice:
      </label>
      <Select
        value={activeVoice}
        onValueChange={handleSwitch}
        disabled={switching}
      >
        <SelectTrigger className="w-48" id="voice-profile-select">
          <SelectValue placeholder="Select voice" />
        </SelectTrigger>
        <SelectContent>
          {voices.map((voice) => (
            <SelectItem key={voice.name} value={voice.name}>
              <div className="flex flex-col">
                <span className="font-medium">{voice.name}</span>
                <span className="text-xs text-gray-500">{voice.description}</span>
              </div>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
};

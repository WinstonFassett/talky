import { useEffect, useState } from 'react';
import type { PipecatClient } from '@pipecat-ai/client-js';
import {
  Button,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@pipecat-ai/voice-ui-kit';
import { ChevronDownIcon, Volume2Icon } from 'lucide-react';

interface SpeakerPickerProps {
  client: PipecatClient;
  size?: 'sm' | 'md' | 'lg';
}

// Speaker picker — pure enumerateDevices. No mic stream, no permission prompt.
// Browsers (Firefox/Safari) may show generic labels until any prior getUserMedia
// has happened; that's a browser limitation.
export const SpeakerPicker = ({ client, size = 'md' }: SpeakerPickerProps) => {
  const [speakers, setSpeakers] = useState<MediaDeviceInfo[]>([]);
  const [selectedId, setSelectedId] = useState<string>(
    () => localStorage.getItem('talky.speaker.deviceId') || '',
  );

  useEffect(() => {
    const load = () =>
      navigator.mediaDevices.enumerateDevices().then((devices) => {
        setSpeakers(devices.filter((d) => d.kind === 'audiooutput'));
      });
    void load();
    navigator.mediaDevices.addEventListener('devicechange', load);
    return () =>
      navigator.mediaDevices.removeEventListener('devicechange', load);
  }, []);

  const pickSpeaker = (deviceId: string) => {
    setSelectedId(deviceId);
    localStorage.setItem('talky.speaker.deviceId', deviceId);
    try {
      client.updateSpeaker(deviceId);
    } catch {
      /* applied on next connect */
    }
  };

  const selectedLabel =
    speakers.find((s) => s.deviceId === selectedId)?.label || 'Default speaker';

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size={size}
          className="h-9 gap-2 px-3 border border-input justify-between min-w-0 flex-1"
          aria-label="Choose speaker"
        >
          <span className="flex items-center gap-2 min-w-0">
            <Volume2Icon size={16} className="shrink-0" />
            <span className="truncate">{selectedLabel}</span>
          </span>
          <ChevronDownIcon size={14} className="shrink-0 opacity-60" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-[220px]">
        <DropdownMenuLabel>Speaker</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {speakers.length === 0 ? (
          <DropdownMenuItem disabled>No speakers</DropdownMenuItem>
        ) : (
          speakers.map((sp) => (
            <DropdownMenuItem
              key={sp.deviceId}
              onSelect={() => pickSpeaker(sp.deviceId)}
            >
              <span className="flex items-center gap-2">
                {selectedId === sp.deviceId && <span aria-hidden>✓</span>}
                <span>{sp.label || 'Speaker'}</span>
              </span>
            </DropdownMenuItem>
          ))
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
};

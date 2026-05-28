import { useEffect, useRef, useState } from 'react';
import { usePipecatClientTransportState } from '@pipecat-ai/client-react';
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
import { ChevronDownIcon, MicIcon, MicOffIcon } from 'lucide-react';

interface MicPickerProps {
  client: PipecatClient;
  size?: 'sm' | 'md' | 'lg';
}

// Mic picker owning its own MediaStream via the browser API. See ticket cf2f.
// Opening the dropdown requests mic + populates real labels; closing releases.
// Pipecat learns the chosen deviceId at connect time via client.updateMic.
export const MicPicker = ({ client, size = 'md' }: MicPickerProps) => {
  const transportState = usePipecatClientTransportState();
  const [open, setOpen] = useState(false);
  const [mics, setMics] = useState<MediaDeviceInfo[]>([]);
  const [selectedId, setSelectedId] = useState<string>(
    () => localStorage.getItem('talky.mic.deviceId') || '',
  );
  const streamRef = useRef<MediaStream | null>(null);

  const isInCall =
    transportState === 'connected' ||
    transportState === 'ready' ||
    transportState === 'connecting' ||
    transportState === 'authenticating';

  const closeStream = () => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
  };

  const openStream = async (deviceId?: string) => {
    closeStream();
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: deviceId ? { deviceId: { exact: deviceId } } : true,
    });
    streamRef.current = stream;
    // Real labels now available.
    const devices = await navigator.mediaDevices.enumerateDevices();
    setMics(devices.filter((d) => d.kind === 'audioinput'));
    if (!selectedId) {
      const actual = stream.getAudioTracks()[0]?.getSettings().deviceId;
      if (actual) setSelectedId(actual);
    }
  };

  useEffect(() => {
    if (isInCall) {
      closeStream();
      return;
    }
    if (open) {
      void openStream(selectedId || undefined).catch((err) => {
        console.warn('mic getUserMedia failed:', err);
      });
    } else {
      closeStream();
    }
    return () => closeStream();
  }, [open, isInCall, selectedId]);

  useEffect(() => {
    void navigator.mediaDevices.enumerateDevices().then((devices) => {
      setMics(devices.filter((d) => d.kind === 'audioinput'));
    });
  }, []);

  const pickMic = (deviceId: string) => {
    setSelectedId(deviceId);
    localStorage.setItem('talky.mic.deviceId', deviceId);
    try {
      client.updateMic(deviceId);
    } catch {
      /* not yet initialized; applied on next connect */
    }
  };

  const selectedLabel =
    mics.find((m) => m.deviceId === selectedId)?.label || 'Default mic';
  const showActive = open || isInCall;

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size={size}
          className="h-9 gap-2 px-3 border border-input justify-between min-w-0 flex-1"
          aria-label="Choose microphone"
        >
          <span className="flex items-center gap-2 min-w-0">
            {showActive ? (
              <MicIcon size={16} className="shrink-0" />
            ) : (
              <MicOffIcon size={16} className="shrink-0 opacity-50" />
            )}
            <span className="truncate">{selectedLabel}</span>
          </span>
          <ChevronDownIcon size={14} className="shrink-0 opacity-60" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-[220px]">
        <DropdownMenuLabel>Microphone</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {mics.length === 0 ? (
          <DropdownMenuItem disabled>No microphones</DropdownMenuItem>
        ) : (
          mics.map((mic) => (
            <DropdownMenuItem
              key={mic.deviceId}
              onSelect={() => pickMic(mic.deviceId)}
            >
              <span className="flex items-center gap-2">
                {selectedId === mic.deviceId && <span aria-hidden>✓</span>}
                <span>{mic.label || 'Microphone'}</span>
              </span>
            </DropdownMenuItem>
          ))
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
};

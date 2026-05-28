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

// Mic picker that owns its own MediaStream via the browser API. Pipecat
// only learns the chosen deviceId at connect time (via client.updateMic).
// No pipecat fight, no __mcp__ test call. See ticket cf2f.
export const MicPicker = ({ client, size = 'md' }: MicPickerProps) => {
  const transportState = usePipecatClientTransportState();
  const [open, setOpen] = useState(false);
  const [mics, setMics] = useState<MediaDeviceInfo[]>([]);
  const [selectedId, setSelectedId] = useState<string>(
    () => localStorage.getItem('talky.mic.deviceId') || '',
  );
  const [level, setLevel] = useState(0); // 0–1 visualizer level
  const streamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const rafRef = useRef<number | null>(null);

  const isInCall =
    transportState === 'connected' ||
    transportState === 'ready' ||
    transportState === 'connecting' ||
    transportState === 'authenticating';

  // Open a stream for the chosen mic, set up analyser, render levels.
  const openStream = async (deviceId?: string) => {
    closeStream();
    const constraints: MediaStreamConstraints = {
      audio: deviceId ? { deviceId: { exact: deviceId } } : true,
    };
    const stream = await navigator.mediaDevices.getUserMedia(constraints);
    streamRef.current = stream;

    // Refresh device list now that we have permission (real labels).
    const devices = await navigator.mediaDevices.enumerateDevices();
    setMics(devices.filter((d) => d.kind === 'audioinput'));
    if (!selectedId) {
      const actual = stream.getAudioTracks()[0]?.getSettings().deviceId;
      if (actual) setSelectedId(actual);
    }

    const ctx = new AudioContext();
    audioCtxRef.current = ctx;
    const source = ctx.createMediaStreamSource(stream);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 256;
    source.connect(analyser);
    const data = new Uint8Array(analyser.frequencyBinCount);

    const tick = () => {
      analyser.getByteTimeDomainData(data);
      // RMS deviation from 128 → rough level 0–1
      let sum = 0;
      for (let i = 0; i < data.length; i++) {
        const v = (data[i] - 128) / 128;
        sum += v * v;
      }
      const rms = Math.sqrt(sum / data.length);
      setLevel(Math.min(1, rms * 3));
      rafRef.current = requestAnimationFrame(tick);
    };
    tick();
  };

  const closeStream = () => {
    if (rafRef.current != null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    if (audioCtxRef.current) {
      void audioCtxRef.current.close();
      audioCtxRef.current = null;
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    setLevel(0);
  };

  // Lifecycle: open dropdown → open stream; close → close stream.
  // If we're in a call, pipecat owns the mic — don't fight it.
  useEffect(() => {
    if (isInCall) {
      closeStream(); // pipecat has it
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

  // Initial label fetch (no permission needed; labels may be generic).
  useEffect(() => {
    void navigator.mediaDevices.enumerateDevices().then((devices) => {
      setMics(devices.filter((d) => d.kind === 'audioinput'));
    });
  }, []);

  // When the user actually picks a mic, persist it and tell pipecat about it
  // so the next connect() uses the right device.
  const pickMic = (deviceId: string) => {
    setSelectedId(deviceId);
    localStorage.setItem('talky.mic.deviceId', deviceId);
    try {
      client.updateMic(deviceId);
    } catch {
      /* not yet initialized; will apply on connect */
    }
    // openStream re-runs via the selectedId dep in the effect.
  };

  const cleanup = () => closeStream();
  useEffect(() => cleanup, []);

  const showLive = open && !isInCall;
  const bars = 8;
  const heights = Array.from({ length: bars }, (_, i) => {
    // Center-weighted bars react more strongly.
    const center = (bars - 1) / 2;
    const weight = 1 - Math.abs(i - center) / center;
    return Math.max(2, level * 18 * (0.4 + weight * 0.6));
  });

  return (
    <div className="flex items-center">
      <div
        className="flex items-center gap-2 px-3 h-9 rounded-l-md border border-r-0 border-input bg-background min-w-[64px]"
        aria-label={showLive || isInCall ? 'Mic active' : 'Mic off'}
      >
        {showLive || isInCall ? (
          <MicIcon size={16} />
        ) : (
          <MicOffIcon size={16} className="opacity-50" />
        )}
        {showLive && (
          <div className="flex items-center gap-[2px] h-5">
            {heights.map((h, i) => (
              <span
                key={i}
                style={{ height: `${h}px` }}
                className="w-[3px] bg-current rounded-sm transition-[height] duration-75"
              />
            ))}
          </div>
        )}
      </div>

      <DropdownMenu open={open} onOpenChange={setOpen}>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            size={size}
            className="rounded-l-none border border-input px-2 h-9"
            aria-label="Choose microphone"
          >
            <ChevronDownIcon size={14} />
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
                onSelect={(e) => {
                  e.preventDefault(); // stay open; user is testing
                  pickMic(mic.deviceId);
                }}
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
    </div>
  );
};

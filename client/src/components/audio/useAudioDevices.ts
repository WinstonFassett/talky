import { useEffect, useState } from 'react';
import {
  usePipecatClient,
  usePipecatClientMediaDevices,
  usePipecatClientTransportState,
} from '@pipecat-ai/client-react';

export interface AudioDevicesState {
  /** Whether the transport is live. Useful for callers that swap chrome. */
  isHot: boolean;
  mics: MediaDeviceInfo[];
  speakers: MediaDeviceInfo[];
  selectedMicId: string;
  selectedSpeakerId: string;
  pickMic: (deviceId: string) => void;
  pickSpeaker: (deviceId: string) => void;
}

// Single source of truth for audio-device data + selection. Hides the
// cold-vs-hot split:
//
//   Hot  (transport connected): pipecat's usePipecatClientMediaDevices owns
//        device enumeration and selection. updateMic/updateSpeaker hit the
//        live mic stream.
//
//   Cold (transport down): pipecat's hook returns empty lists (initDevices
//        is gated on connect intent), so we fall back to enumerateDevices()
//        and localStorage. Picks are mirrored to client.updateMic/Speaker
//        best-effort so they survive the next connect.
//
// Safe to call in multiple consumers — no shared state, no provider needed.
export const useAudioDevices = (): AudioDevicesState => {
  const client = usePipecatClient();
  const transportState = usePipecatClientTransportState();
  const isHot = transportState === 'connected' || transportState === 'ready';

  const {
    availableMics: liveMics,
    selectedMic: liveSelectedMic,
    updateMic: liveUpdateMic,
    availableSpeakers: liveSpeakers,
    selectedSpeaker: liveSelectedSpeaker,
    updateSpeaker: liveUpdateSpeaker,
  } = usePipecatClientMediaDevices();

  const [coldMics, setColdMics] = useState<MediaDeviceInfo[]>([]);
  const [coldSpeakers, setColdSpeakers] = useState<MediaDeviceInfo[]>([]);
  const [coldSelectedMicId, setColdSelectedMicId] = useState<string>(
    () => localStorage.getItem('talky.mic.deviceId') || '',
  );
  const [coldSelectedSpeakerId, setColdSelectedSpeakerId] = useState<string>(
    () => localStorage.getItem('talky.speaker.deviceId') || '',
  );

  useEffect(() => {
    if (isHot) return;
    const load = () =>
      navigator.mediaDevices.enumerateDevices().then((devices) => {
        setColdMics(devices.filter((d) => d.kind === 'audioinput'));
        setColdSpeakers(devices.filter((d) => d.kind === 'audiooutput'));
      });
    void load();
    navigator.mediaDevices.addEventListener('devicechange', load);
    return () =>
      navigator.mediaDevices.removeEventListener('devicechange', load);
  }, [isHot]);

  const pickColdMic = (deviceId: string) => {
    setColdSelectedMicId(deviceId);
    localStorage.setItem('talky.mic.deviceId', deviceId);
    try {
      client?.updateMic(deviceId);
    } catch {
      /* applied on next connect */
    }
  };

  const pickColdSpeaker = (deviceId: string) => {
    setColdSelectedSpeakerId(deviceId);
    localStorage.setItem('talky.speaker.deviceId', deviceId);
    try {
      client?.updateSpeaker(deviceId);
    } catch {
      /* applied on next connect */
    }
  };

  if (isHot) {
    return {
      isHot: true,
      mics: liveMics ?? [],
      speakers: liveSpeakers ?? [],
      selectedMicId: liveSelectedMic?.deviceId ?? '',
      selectedSpeakerId: liveSelectedSpeaker?.deviceId ?? '',
      pickMic: (id) => liveUpdateMic?.(id),
      pickSpeaker: (id) => liveUpdateSpeaker?.(id),
    };
  }

  return {
    isHot: false,
    mics: coldMics,
    speakers: coldSpeakers,
    selectedMicId: coldSelectedMicId,
    selectedSpeakerId: coldSelectedSpeakerId,
    pickMic: pickColdMic,
    pickSpeaker: pickColdSpeaker,
  };
};

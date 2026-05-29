import {
  DropdownMenuCheckboxItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
} from '@pipecat-ai/voice-ui-kit';
import { MicIcon, SpeakerIcon } from 'lucide-react';

interface AudioMenuItemsProps {
  mics: MediaDeviceInfo[];
  speakers: MediaDeviceInfo[];
  selectedMicId: string;
  selectedSpeakerId: string;
  onPickMic: (deviceId: string) => void;
  onPickSpeaker: (deviceId: string) => void;
  /** Whether to render the "Audio Devices" header above the sections. */
  withHeader?: boolean;
}

// Mic + speaker checklists as DropdownMenu children. Shape mirrors
// voice-ui-kit's UserAudioComponent dropdown internals
// (UserAudioControl.tsx:268-315) so the menu looks identical wherever
// it appears.
export const AudioMenuItems = ({
  mics,
  speakers,
  selectedMicId,
  selectedSpeakerId,
  onPickMic,
  onPickSpeaker,
  withHeader = true,
}: AudioMenuItemsProps) => {
  const hasMics = mics.length > 0;
  const hasSpeakers = speakers.length > 0;

  return (
    <>
      {withHeader && (
        <>
          <DropdownMenuLabel>Audio Devices</DropdownMenuLabel>
          {(hasMics || hasSpeakers) && <DropdownMenuSeparator />}
        </>
      )}

      {hasMics && (
        <>
          <DropdownMenuLabel className="text-xs text-muted-foreground">
            <MicIcon size={12} className="inline mr-1" />
            Microphones
          </DropdownMenuLabel>
          {mics.map((device) => (
            <DropdownMenuCheckboxItem
              key={`mic-${device.deviceId}`}
              checked={device.deviceId === selectedMicId}
              onCheckedChange={() => onPickMic(device.deviceId)}
            >
              {device.label || `Microphone ${device.deviceId.slice(0, 5)}`}
            </DropdownMenuCheckboxItem>
          ))}
          {hasSpeakers && <DropdownMenuSeparator />}
        </>
      )}

      {hasSpeakers && (
        <>
          <DropdownMenuLabel className="text-xs text-muted-foreground">
            <SpeakerIcon size={12} className="inline mr-1" />
            Speakers
          </DropdownMenuLabel>
          {speakers.map((device) => (
            <DropdownMenuCheckboxItem
              key={`speaker-${device.deviceId}`}
              checked={device.deviceId === selectedSpeakerId}
              onCheckedChange={() => onPickSpeaker(device.deviceId)}
            >
              {device.label || `Speaker ${device.deviceId.slice(0, 5)}`}
            </DropdownMenuCheckboxItem>
          ))}
        </>
      )}
    </>
  );
};

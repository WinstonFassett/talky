import { MicSelector } from './audio/MicSelector';
import { SpeakerSelector } from './audio/SpeakerSelector';
import { LLMProfileSelect } from './LLMProfileSelect';
import { VoiceProfileSelect } from './VoiceProfileSelect';

// Stacked picker rows: Assistant / Voice / Audio. Used in the mobile
// SessionSheet drawer body AND in the desktop EmptyState pre-connect.
export const SessionControls = () => {
  return (
    <div className="flex flex-col gap-4 w-full">
      <Row label="Assistant">
        <LLMProfileSelect />
      </Row>
      <Row label="Voice">
        <VoiceProfileSelect />
      </Row>
      <Row label="Audio" stretch={false}>
        <div className="flex flex-col gap-1.5 w-full">
          <MicSelector />
          <SpeakerSelector />
        </div>
      </Row>
    </div>
  );
};

const Row = ({
  label,
  children,
  stretch = true,
}: {
  label: string;
  children: React.ReactNode;
  stretch?: boolean;
}) => (
  <div className="flex flex-col gap-1.5">
    <div
      className="font-mono uppercase"
      style={{
        fontSize: 10,
        fontWeight: 600,
        letterSpacing: '0.08em',
        color: 'var(--color-text-mute)',
      }}
    >
      {label}
    </div>
    <div
      className={
        stretch
          ? 'flex [&>*]:flex-1 [&_button]:w-full [&_button]:justify-between'
          : 'flex'
      }
    >
      {children}
    </div>
  </div>
);

import { usePipecatClientTransportState } from '@pipecat-ai/client-react';
import { ConnectedAudioControl } from './ConnectedAudioControl';
import { IdleAudioControl } from './IdleAudioControl';
import { LLMProfileSelect } from './LLMProfileSelect';
import { VoiceProfileSelect } from './VoiceProfileSelect';

// Stacked picker rows: Assistant / Voice / Audio. Used in the mobile
// SessionSheet drawer body AND in the desktop EmptyState pre-connect.
export const SessionControls = () => {
  const transportState = usePipecatClientTransportState();
  const connected = transportState === 'connected' || transportState === 'ready';
  return (
    <div className="flex flex-col gap-4 w-full">
      <Row label="Assistant">
        <LLMProfileSelect />
      </Row>
      <Row label="Voice">
        <VoiceProfileSelect />
      </Row>
      <Row label="Audio" stretch={false}>
        <div className="flex w-full [&>div]:w-full">
          {connected ? (
            <ConnectedAudioControl
              size="md"
              variant="ghost"
              noVisualizer={false}
              classNames={{ button: 'flex-1' }}
              visualizerProps={{ barCount: 32 }}
            />
          ) : (
            <IdleAudioControl size="md" />
          )}
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

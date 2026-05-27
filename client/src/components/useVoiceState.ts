import type { PipecatClient } from '@pipecat-ai/client-js';
import { useEffect, useState } from 'react';

import { useUrlParam, type SimulatedVoiceState } from '../fixtures/harness';

export type VoiceState = SimulatedVoiceState;

// Maps Pipecat bot events to a single high-level state. Honors
// ?voiceState= override for /dev layout work.
export function useVoiceState(
  client: PipecatClient | null,
  transportConnected: boolean,
): VoiceState {
  const [botThinking, setBotThinking] = useState(false);
  const [botSpeaking, setBotSpeaking] = useState(false);
  const [userSpeaking, setUserSpeaking] = useState(false);
  const override = useUrlParam('voiceState') as VoiceState | null;

  useEffect(() => {
    if (!client) return;

    const onLlmStarted = () => setBotThinking(true);
    const onLlmStopped = () => setBotThinking(false);
    const onTtsStarted = () => setBotThinking(false);
    const onBotStarted = () => {
      setBotThinking(false);
      setBotSpeaking(true);
    };
    const onBotStopped = () => setBotSpeaking(false);
    const onUserStarted = () => setUserSpeaking(true);
    const onUserStopped = () => setUserSpeaking(false);

    client.on('botLlmStarted', onLlmStarted);
    client.on('botLlmStopped', onLlmStopped);
    client.on('botTtsStarted', onTtsStarted);
    client.on('botStartedSpeaking', onBotStarted);
    client.on('botStoppedSpeaking', onBotStopped);
    client.on('userStartedSpeaking', onUserStarted);
    client.on('userStoppedSpeaking', onUserStopped);

    return () => {
      client.off('botLlmStarted', onLlmStarted);
      client.off('botLlmStopped', onLlmStopped);
      client.off('botTtsStarted', onTtsStarted);
      client.off('botStartedSpeaking', onBotStarted);
      client.off('botStoppedSpeaking', onBotStopped);
      client.off('userStartedSpeaking', onUserStarted);
      client.off('userStoppedSpeaking', onUserStopped);
    };
  }, [client]);

  if (override) return override;
  if (!transportConnected) return 'disconnected';
  if (botSpeaking) return 'speaking';
  if (botThinking) return 'thinking';
  if (userSpeaking) return 'listening';
  return 'idle';
}

export const VOICE_STATE_LABELS: Record<VoiceState, string> = {
  disconnected: 'DISCONNECTED',
  idle: 'IDLE',
  listening: 'LISTENING',
  thinking: 'THINKING',
  speaking: 'SPEAKING',
};

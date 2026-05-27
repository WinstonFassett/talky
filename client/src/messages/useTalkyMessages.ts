import { useMemo } from 'react';

import type { AggregationMetadata } from '@pipecat-ai/voice-ui-kit';
import { usePipecatConversation } from '@pipecat-ai/voice-ui-kit';

import { getFixture } from '../fixtures/messages';
import { useUrlParam } from '../fixtures/harness';
import { adaptMessages } from './adapter';
import type { TalkyMessage } from './types';

const AGGREGATION_METADATA: Record<string, AggregationMetadata> = {
  thinking: { isSpoken: false, displayMode: 'block' as const },
  tool_start: { isSpoken: false, displayMode: 'block' as const },
  tool_end: { isSpoken: false, displayMode: 'block' as const },
  error: { isSpoken: false, displayMode: 'block' as const },
  info: { isSpoken: false, displayMode: 'block' as const },
};

export function useTalkyMessages(): TalkyMessage[] {
  const fixtureName = useUrlParam('fixture');
  const fixture = useMemo(() => getFixture(fixtureName), [fixtureName]);
  const live = usePipecatConversation({ aggregationMetadata: AGGREGATION_METADATA });
  const adapted = useMemo(() => adaptMessages(live.messages), [live.messages]);
  return fixture ?? adapted;
}

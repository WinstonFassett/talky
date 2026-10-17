import { useMemo } from 'react';

import { getFixture } from '../fixtures/messages';
import { useUrlParam } from '../fixtures/harness';
import { useSessions } from '../sessions/SessionsProvider';
import type { TalkyMessage } from './types';

// The open session's saved turns followed by the live call's messages
// (see SessionsProvider). Dev fixtures override both.
export function useTalkyMessages(): TalkyMessage[] {
  const fixtureName = useUrlParam('fixture');
  const fixture = useMemo(() => getFixture(fixtureName), [fixtureName]);
  const { messages } = useSessions();
  return fixture ?? messages;
}

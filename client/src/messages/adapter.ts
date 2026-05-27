import type {
  BotOutputText,
  ConversationMessage,
  ConversationMessagePart,
} from '@pipecat-ai/voice-ui-kit';

import type { TalkyMessage, TalkyPart } from './types';

function isBotOutputText(val: unknown): val is BotOutputText {
  return typeof val === 'object' && val !== null && 'spoken' in val && 'unspoken' in val;
}

function partAsString(part: ConversationMessagePart): string {
  if (isBotOutputText(part.text)) return part.text.spoken + part.text.unspoken;
  return typeof part.text === 'string' ? part.text : '';
}

function splitPayload(part: ConversationMessagePart): { spoken: string; unspoken: string } {
  if (isBotOutputText(part.text)) {
    return { spoken: part.text.spoken, unspoken: part.text.unspoken };
  }
  const text = typeof part.text === 'string' ? part.text : '';
  return { spoken: text, unspoken: '' };
}

// The daemon emits a NUL-delimited "key\x00rest" envelope for some event parts.
// Strip the envelope so renderers see just the human-readable string.
function stripEventEnvelope(content: string): string {
  const i = content.indexOf('\x00');
  return i < 0 ? content : content.slice(0, i);
}

function adaptPart(part: ConversationMessagePart): TalkyPart | null {
  const agg = part.aggregatedBy;

  if (agg === 'thinking') {
    return { kind: 'thinking', content: partAsString(part) };
  }
  if (agg === 'tool_start') {
    return { kind: 'tool_start', content: stripEventEnvelope(partAsString(part)) };
  }
  if (agg === 'tool_end') {
    return { kind: 'tool_end', content: stripEventEnvelope(partAsString(part)) };
  }
  if (agg === 'info') {
    return { kind: 'info', content: stripEventEnvelope(partAsString(part)) };
  }
  if (agg === 'error') {
    return { kind: 'error', content: stripEventEnvelope(partAsString(part)) };
  }

  // Default: text (karaoke-aware).
  const { spoken, unspoken } = splitPayload(part);
  if (!spoken && !unspoken) return null;
  return { kind: 'text', spoken, unspoken };
}

export function adaptMessage(msg: ConversationMessage, idx: number): TalkyMessage {
  const parts: TalkyPart[] = [];
  for (const p of msg.parts) {
    const out = adaptPart(p);
    if (out) parts.push(out);
  }
  return {
    id: `${msg.createdAt}-${idx}`,
    role: msg.role === 'function_call' ? 'system' : msg.role,
    createdAt: msg.createdAt,
    final: msg.final ?? true,
    parts,
  };
}

export function adaptMessages(messages: ConversationMessage[]): TalkyMessage[] {
  return messages.map(adaptMessage);
}

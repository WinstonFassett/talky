// Talky's own message model. Independent of voice-ui-kit.
// Add new part kinds here as the daemon starts emitting them
// (approvals, structured tool results, etc.).

export type TalkyPart =
  | { kind: 'text'; spoken: string; unspoken: string }
  | { kind: 'thinking'; content: string }
  | { kind: 'tool_start'; content: string }
  | { kind: 'tool_end'; content: string }
  | { kind: 'info'; content: string }
  | { kind: 'error'; content: string };

export type TalkyMessage = {
  id: string;
  role: 'user' | 'assistant' | 'system';
  createdAt: string;
  final: boolean;
  profile?: string;
  parts: TalkyPart[];
};

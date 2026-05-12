import { useCallback, useRef, useState } from 'react';
import { RTVIEvent } from '@pipecat-ai/client-js';
import { useRTVIClientEvent } from '@pipecat-ai/client-react';

export type TurnRole = 'user' | 'assistant' | 'system';

export type TurnNode =
  | { kind: 'text'; text: string; isStreaming: boolean }
  | { kind: 'reasoning'; text: string; isStreaming: boolean; duration?: number }
  | { kind: 'tool'; toolName: string; status: 'running' | 'completed' | 'failed'; result?: string; resultLines?: number; isError?: boolean; elapsedMs?: number }
  | { kind: 'info'; text: string }
  | { kind: 'error'; text: string; payload?: unknown }
  | { kind: 'warning'; text: string };

export interface Turn {
  id: number;
  role: TurnRole;
  nodes: TurnNode[];
}

function splitContent(content: string): { summary: string; payload: unknown | null } {
  const i = content.indexOf('\x00');
  if (i < 0) return { summary: content, payload: null };
  const rest = content.slice(i + 1);
  try {
    return { summary: content.slice(0, i), payload: JSON.parse(rest) };
  } catch {
    return { summary: content.slice(0, i), payload: rest };
  }
}

export function useTurns() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const nextId = useRef(0);
  const lastWasThinking = useRef(false);

  const pushTurn = useCallback((role: TurnRole, nodes: TurnNode[]) => {
    setTurns(prev => [...prev, { id: nextId.current++, role, nodes }]);
  }, []);

  const mutateLastAssistantTurn = useCallback((fn: (nodes: TurnNode[]) => TurnNode[]) => {
    setTurns(prev => {
      for (let i = prev.length - 1; i >= 0; i--) {
        if (prev[i].role === 'assistant') {
          const updated = [...prev];
          updated[i] = { ...prev[i], nodes: fn([...prev[i].nodes]) };
          return updated;
        }
      }
      // No assistant turn yet — create one
      return [...prev, { id: nextId.current++, role: 'assistant', nodes: fn([]) }];
    });
  }, []);

  useRTVIClientEvent(
    RTVIEvent.BotOutput,
    useCallback((data: { text?: string; aggregated_by?: string; spoken?: boolean } | null) => {
      if (!data) return;
      const { text = '', aggregated_by } = data;

      switch (aggregated_by) {
        case 'thinking': {
          const delta = text;
          mutateLastAssistantTurn(nodes => {
            const last = nodes[nodes.length - 1];
            if (lastWasThinking.current && last?.kind === 'reasoning' && last.isStreaming) {
              nodes[nodes.length - 1] = { ...last, text: last.text + delta };
              return nodes;
            }
            return [...nodes, { kind: 'reasoning', text: delta, isStreaming: true }];
          });
          lastWasThinking.current = true;
          break;
        }

        case 'tool_start': {
          const { summary } = splitContent(text);
          lastWasThinking.current = false;
          mutateLastAssistantTurn(nodes => [
            ...nodes,
            { kind: 'tool', toolName: summary, status: 'running' },
          ]);
          break;
        }

        case 'tool_end': {
          const { summary, payload } = splitContent(text);
          const p = typeof payload === 'object' && payload !== null ? payload as Record<string, unknown> : {};
          const isError = !!p.is_error;
          const resultLines = typeof p.result_lines === 'number' ? p.result_lines : undefined;
          lastWasThinking.current = false;
          mutateLastAssistantTurn(nodes => {
            // Find the matching running tool node (last one with same name)
            for (let i = nodes.length - 1; i >= 0; i--) {
              const n = nodes[i];
              if (n.kind === 'tool' && n.status === 'running' && n.toolName === summary) {
                nodes[i] = { ...n, status: isError ? 'failed' : 'completed', isError, resultLines };
                return nodes;
              }
            }
            // No match — append a completed node
            return [...nodes, { kind: 'tool', toolName: summary, status: isError ? 'failed' : 'completed', isError, resultLines }];
          });
          break;
        }

        case 'error': {
          const { summary, payload } = splitContent(text);
          lastWasThinking.current = false;
          mutateLastAssistantTurn(nodes => [...nodes, { kind: 'error', text: summary, payload }]);
          break;
        }

        case 'info': {
          const { summary } = splitContent(text);
          lastWasThinking.current = false;
          mutateLastAssistantTurn(nodes => [...nodes, { kind: 'info', text: summary }]);
          break;
        }

        default: {
          // Plain spoken/unspoken text
          lastWasThinking.current = false;
          mutateLastAssistantTurn(nodes => {
            const last = nodes[nodes.length - 1];
            if (last?.kind === 'text' && last.isStreaming) {
              nodes[nodes.length - 1] = { ...last, text: last.text + text };
              return nodes;
            }
            return [...nodes, { kind: 'text', text, isStreaming: true }];
          });
          break;
        }
      }
    }, [mutateLastAssistantTurn]),
  );

  useRTVIClientEvent(
    RTVIEvent.BotLlmStopped,
    useCallback(() => {
      lastWasThinking.current = false;
      mutateLastAssistantTurn(nodes =>
        nodes.map(n => {
          if ((n.kind === 'reasoning' || n.kind === 'text') && n.isStreaming) {
            return { ...n, isStreaming: false };
          }
          return n;
        }),
      );
    }, [mutateLastAssistantTurn]),
  );

  useRTVIClientEvent(
    RTVIEvent.UserTranscript,
    useCallback((data: { text?: string } | null) => {
      if (!data?.text) return;
      pushTurn('user', [{ kind: 'text', text: data.text, isStreaming: false }]);
    }, [pushTurn]),
  );

  return turns;
}

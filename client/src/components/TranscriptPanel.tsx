import { useEffect, useRef } from 'react';
import { useTurns } from '../hooks/useTurns';
import type { Turn, TurnNode } from '../hooks/useTurns';
import { Reasoning } from './Reasoning';

function ToolNode({ node }: { node: Extract<TurnNode, { kind: 'tool' }> }) {
  const icon = node.status === 'running' ? '⟳' : node.isError ? '✗' : '✓';
  const lines = node.resultLines != null ? ` (${node.resultLines} lines)` : '';
  return (
    <div className={`flex items-center gap-1.5 text-xs font-mono py-0.5 ${node.isError ? 'text-destructive' : 'text-muted-foreground'}`}>
      <span className={node.status === 'running' ? 'opacity-50' : ''}>{icon}</span>
      <span>{node.toolName}{lines}</span>
    </div>
  );
}

function TurnNodeView({ node }: { node: TurnNode }) {
  switch (node.kind) {
    case 'reasoning':
      return <Reasoning text={node.text} isStreaming={node.isStreaming} />;
    case 'text':
      return (
        <div className="text-sm whitespace-pre-wrap">
          {node.text}
          {node.isStreaming && <span className="animate-pulse ml-0.5 opacity-60">▌</span>}
        </div>
      );
    case 'tool':
      return <ToolNode node={node} />;
    case 'info':
      return <div className="text-xs text-muted-foreground opacity-50 py-0.5">{node.text}</div>;
    case 'error':
      return (
        <div
          className="text-xs font-mono text-destructive py-0.5"
          title={node.payload ? JSON.stringify(node.payload) : undefined}
        >
          ✗ {node.text}
        </div>
      );
    case 'warning':
      return <div className="text-xs text-yellow-500 py-0.5">⚠ {node.text}</div>;
  }
}

function TurnView({ turn }: { turn: Turn }) {
  if (turn.role === 'user') {
    const text = turn.nodes.find(n => n.kind === 'text');
    return (
      <div className="flex justify-end px-4 py-1">
        <div className="bg-muted rounded-lg px-3 py-2 text-sm max-w-[75%]">
          {text?.kind === 'text' ? text.text : null}
        </div>
      </div>
    );
  }

  return (
    <div className="px-4 py-1 space-y-0.5">
      {turn.nodes.map((node, i) => (
        <TurnNodeView key={i} node={node} />
      ))}
    </div>
  );
}

export function TranscriptPanel() {
  const turns = useTurns();
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [turns]);

  if (turns.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center text-muted-foreground text-sm opacity-40">
        Waiting for agent…
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto py-2 space-y-1">
      {turns.map(turn => (
        <TurnView key={turn.id} turn={turn} />
      ))}
      <div ref={bottomRef} />
    </div>
  );
}

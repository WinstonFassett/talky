import { useEffect, useRef, useState } from 'react';

interface ReasoningProps {
  text: string;
  isStreaming: boolean;
}

export function Reasoning({ text, isStreaming }: ReasoningProps) {
  const [open, setOpen] = useState(true);
  const [duration, setDuration] = useState<number | undefined>(undefined);

  const explicitlyClosed = useRef(false);
  const hasAutoClosed = useRef(false);
  const startTime = useRef(Date.now());
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!isStreaming) {
      setDuration(Math.ceil((Date.now() - startTime.current) / 1000));
      if (!hasAutoClosed.current && !explicitlyClosed.current) {
        hasAutoClosed.current = true;
        closeTimer.current = setTimeout(() => setOpen(false), 1000);
      }
    }
    return () => {
      if (closeTimer.current) clearTimeout(closeTimer.current);
    };
  }, [isStreaming]);

  const handleToggle = () => {
    const next = !open;
    setOpen(next);
    if (isStreaming) explicitlyClosed.current = !next;
  };

  const label = isStreaming
    ? 'Thinking…'
    : duration !== undefined && duration > 0
      ? `Thought for ${duration}s`
      : 'Thought';

  return (
    <div className="text-xs text-muted-foreground my-0.5">
      <button
        onClick={handleToggle}
        className="flex items-center gap-1 cursor-pointer opacity-60 hover:opacity-100 transition-opacity select-none"
      >
        <span className="text-[10px]">{open ? '▾' : '▸'}</span>
        <span className={isStreaming ? 'animate-pulse' : ''}>{label}</span>
      </button>
      {open && text && (
        <div className="pl-3 border-l border-muted/40 italic whitespace-pre-wrap mt-0.5 opacity-70">
          {text}
        </div>
      )}
    </div>
  );
}

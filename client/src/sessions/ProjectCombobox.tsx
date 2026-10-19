// Project filter for "In tools" (WIN-579): a dropdown, searchable once the list is long. Replaces the chip row.
import { useState } from 'react';
import { CheckIcon, ChevronDownIcon } from 'lucide-react';

import { Command, CommandEmpty, CommandInput, CommandItem, CommandList } from '../components/ui/command';
import { Popover, PopoverContent, PopoverTrigger } from '../components/ui/popover';

export type ProjectOption = { id: string; title: string; count: number };
const SEARCH_MIN = 8;

export function ProjectCombobox({ options, value, onChange }: { options: ProjectOption[]; value: string | null; onChange: (id: string | null) => void }) {
  const [open, setOpen] = useState(false);
  const total = options.reduce((n, o) => n + o.count, 0);
  const all: ProjectOption = { id: '', title: 'All projects', count: total };
  const current = options.find((o) => o.id === value) ?? all;
  const pick = (id: string) => {
    onChange(id || null);
    setOpen(false);
  };
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          role="combobox"
          aria-expanded={open}
          aria-label="Project"
          data-testid="intools-project"
          className="flex h-8 w-full items-center gap-2 rounded-md px-2 text-left text-[12px] hover:bg-[var(--color-muted)]"
          style={{ border: '1px solid var(--color-border-soft)', color: 'var(--color-foreground)' }}
        >
          <span className="min-w-0 flex-1 truncate">{current.title}</span>
          <span className="shrink-0 font-mono text-[10px]" style={{ color: 'var(--color-text-mute)' }}>
            {current.count}
          </span>
          <ChevronDownIcon size={12} className="shrink-0" style={{ color: 'var(--color-text-mute)' }} />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[var(--radix-popover-trigger-width)] min-w-56 p-0" data-testid="intools-project-list">
        <Command>
          {options.length >= SEARCH_MIN && <CommandInput placeholder="Find a project…" data-testid="intools-project-search" />}
          <CommandList className="max-h-72">
            <CommandEmpty>No project matches.</CommandEmpty>
            {[all, ...options].map((o) => (
              <CommandItem key={o.id || 'all'} value={o.title} data-testid="intools-project-option" data-project-id={o.id} onSelect={() => pick(o.id)}>
                <CheckIcon size={12} className={`mr-2 shrink-0 ${o.id === (value ?? '') ? 'opacity-100' : 'opacity-0'}`} />
                <span className="min-w-0 flex-1 truncate">{o.title}</span>
                <span className="ml-2 shrink-0 font-mono text-[10px]" style={{ color: 'var(--color-text-mute)' }}>
                  {o.count}
                </span>
              </CommandItem>
            ))}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}

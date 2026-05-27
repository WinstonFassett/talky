import type { TalkyMessage, TalkyPart } from './types';

function partText(part: TalkyPart): string {
  if (part.kind === 'text') return part.spoken + part.unspoken;
  return part.content;
}

function messageBody(msg: TalkyMessage): string {
  return msg.parts
    .filter((p) => p.kind === 'text')
    .map(partText)
    .join(' ')
    .trim();
}

function roleLabel(msg: TalkyMessage): string {
  if (msg.role === 'user') return 'User';
  if (msg.role === 'assistant') {
    if (msg.profile) return msg.profile.charAt(0).toUpperCase() + msg.profile.slice(1);
    return 'Assistant';
  }
  return msg.role;
}

export function toMarkdown(messages: TalkyMessage[]): string {
  const lines: string[] = [`# Transcript — ${new Date().toLocaleString()}`, ''];
  for (const msg of messages) {
    const body = messageBody(msg);
    if (!body) continue;
    const time = new Date(msg.createdAt).toLocaleTimeString();
    lines.push(`**${roleLabel(msg)}** (${time})`);
    lines.push(body, '');
  }
  return lines.join('\n');
}

export function toJsonl(messages: TalkyMessage[]): string {
  return messages
    .map((m) =>
      JSON.stringify({
        role: m.role,
        profile: m.profile,
        text: messageBody(m),
        createdAt: m.createdAt,
      }),
    )
    .join('\n');
}

export function toCsv(messages: TalkyMessage[]): string {
  const escape = (s: string) => `"${s.replace(/"/g, '""')}"`;
  const rows = ['role,profile,text,createdAt'];
  for (const m of messages) {
    const body = messageBody(m);
    if (!body) continue;
    rows.push(
      [escape(m.role), escape(m.profile ?? ''), escape(body), escape(m.createdAt)].join(','),
    );
  }
  return rows.join('\n');
}

export function downloadBlob(content: string, filename: string, mime: string): void {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function timestamp(): string {
  return new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
}

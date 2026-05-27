import type { TalkyMessage } from '../messages/types';

const T0 = '2026-05-27T10:42:00Z';
const t = (offsetSec: number) =>
  new Date(new Date(T0).getTime() + offsetSec * 1000).toISOString();

let idCounter = 0;
const nextId = () => `fx-${++idCounter}`;

const user = (text: string, createdAt: string): TalkyMessage => ({
  id: nextId(),
  role: 'user',
  createdAt,
  final: true,
  parts: [{ kind: 'text', spoken: text, unspoken: '' }],
});

const assistant = (
  text: string,
  createdAt: string,
  profile = 'hermes',
): TalkyMessage => ({
  id: nextId(),
  role: 'assistant',
  createdAt,
  final: true,
  profile,
  parts: [{ kind: 'text', spoken: text, unspoken: '' }],
});

const HAIKU: TalkyMessage[] = [
  user('give me a haiku in md with a title and each line is a bullet', t(0)),
  assistant(
    '# Morning Light\n\n- Cherry blossoms fall\n- Dew glistens on silent grass\n- New day awakens',
    t(1),
  ),
];

const HAIKU_NO_NL: TalkyMessage[] = [
  user(
    'give me a haiku in md with a title and each line is a bullet (no newlines)',
    t(0),
  ),
  assistant(
    '# Morning Light - Cherry blossoms fall - Dew glistens on silent grass - New day awakens',
    t(1),
  ),
];

const PROSE: TalkyMessage[] = [
  user('tell me a joke', t(0)),
  assistant(
    "Why don't scientists trust atoms?\n\nBecause they make up everything!",
    t(1),
  ),
];

const PROSE_NO_NL: TalkyMessage[] = [
  user('tell me a joke', t(0)),
  assistant(
    "Why don't scientists trust atoms? Because they make up everything!",
    t(1),
  ),
];

const CODE: TalkyMessage[] = [
  user('show a python function', t(0)),
  assistant(
    'Here is a tiny example:\n\n```python\ndef hello(name):\n    return f"Hello, {name}!"\n```\n\nThat\'s it.',
    t(1),
  ),
];

const KARAOKE: TalkyMessage[] = [
  user('tell me a long story so I can see the cursor mid-stream', t(0)),
  {
    id: nextId(),
    role: 'assistant',
    createdAt: t(1),
    final: false,
    profile: 'hermes',
    parts: [
      {
        kind: 'text',
        spoken:
          'Once upon a time, in a small village at the edge of an ancient forest, ' +
          'there lived a humble baker who loved to wake before dawn. ',
        unspoken:
          'Every morning she kneaded her dough by candlelight, listening to the soft ' +
          'creaking of the timbers and the distant call of an owl returning home. ' +
          'On this particular morning, however, something was different — a faint ' +
          'silver light spilled under her door, and a hush had fallen over the whole street.',
      },
    ],
  },
];

// Representative conversation exercising every part kind we actually have today.
// Excludes approval (not implemented yet) and structured tool output (also not yet).
const KITCHEN_SINK: TalkyMessage[] = [
  assistant('Hermes channel.', t(0)),
  user(
    'Hi there, Hermes. Can you tell me the name of the folder you are in? Not the full path, just the name. Along with the name of the branch.',
    t(3),
  ),
  {
    id: nextId(),
    role: 'assistant',
    createdAt: t(4),
    final: true,
    profile: 'hermes',
    parts: [
      {
        kind: 'thinking',
        content:
          'The user wants the current directory name and git branch. I will run a terminal command to get both.',
      },
      { kind: 'tool_start', content: 'basename $(pwd) && git rev-parse --abbrev-ref HEAD' },
      { kind: 'tool_end', content: 'talky · client-design-refresh-20260526 · 0.8s' },
      {
        kind: 'text',
        spoken:
          "We're in the **talky** folder, on the **client-design-refresh-20260526** branch.",
        unspoken: '',
      },
    ],
  },
  user(
    'Perfect. Read the README and then tell me the top three to five things that a person should know about talky.',
    t(12),
  ),
  {
    id: nextId(),
    role: 'assistant',
    createdAt: t(13),
    final: true,
    profile: 'hermes',
    parts: [
      {
        kind: 'thinking',
        content:
          'Let me read the README. I should focus on the most important user-facing aspects.',
      },
      { kind: 'tool_start', content: 'README.md' },
      { kind: 'tool_end', content: 'README.md · 187 lines · 0.3s' },
      {
        kind: 'text',
        spoken:
          "Based on the README, here are the **top 3–5 things** you should know about Talky:\n\n" +
          "**It's a voice interface for AI** — Talky lets you talk to multiple AI assistants " +
          '(OpenClaw, Moltis, Hermes, Pi) or use MCP to integrate with Claude Desktop and Claude Code. ' +
          'Everything runs through a browser UI on `localhost:9090`.\n\n' +
          '**It runs on a daemon** — `talky daemon` runs the core service on port 9090. ' +
          'It serves the browser UI and MCP tools, and auto-spawns any daemon-dependent processes.\n\n' +
          '**Local or cloud voices** — The default uses local ML models (Kokoro TTS + Whisper STT), ' +
          'but you can swap to cloud providers like Google or Deepgram by adding API keys.',
        unspoken: '',
      },
    ],
  },
  user('And while you streamed that I could read along — neat.', t(40)),
  // Live streaming part (karaoke mid-stream) as the newest message
  {
    id: nextId(),
    role: 'assistant',
    createdAt: t(41),
    final: false,
    profile: 'hermes',
    parts: [
      { kind: 'info', content: 'streaming...' },
      {
        kind: 'text',
        spoken:
          'Right — the spoken text renders solid as TTS plays it, and the rest stays dimmed until it catches up. ',
        unspoken:
          "That's the karaoke effect: spoken in normal contrast, unspoken in muted-foreground, " +
          'and the caret sits at the seam while you talk.',
      },
    ],
  },
];

export const FIXTURES: Record<string, TalkyMessage[]> = {
  'kitchen-sink': KITCHEN_SINK,
  haiku: HAIKU,
  'haiku-no-nl': HAIKU_NO_NL,
  prose: PROSE,
  'prose-no-nl': PROSE_NO_NL,
  code: CODE,
  karaoke: KARAOKE,
};

export const FIXTURE_NAMES = Object.keys(FIXTURES);

export function getFixture(name: string | null | undefined): TalkyMessage[] | null {
  if (!name) return null;
  return FIXTURES[name] ?? null;
}

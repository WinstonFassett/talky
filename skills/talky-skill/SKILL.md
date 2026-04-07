---
name: talky
description: Talky gives the agent a voice and ears. Use this skill whenever the user wants the agent to speak, listen, hold a voice conversation, go quiet, or change how it engages via audio — even if they don't say "talky" by name. Triggers include "talk to me", "voice mode", "use your voice", "holler at me", "tell me when", "notify me", "let's talk", "start a voice conversation", "I want to have a conversation", "let's have a discussion", "standby mode", "go quiet", "just listen", "be a fly on the wall", "don't speak unless spoken to", "listen but don't respond". Covers two invocation styles (voice prompt mode / walkie-talkie, and voice conversation / full-duplex browser) plus two cross-cutting listening behaviors (ambient awareness and standby mode).
---

Talky lets the agent speak and listen. There are **two ways to invoke** voice (voice prompt mode, voice conversation) and **two cross-cutting behaviors** that shape *how* the agent listens once invoked (ambient awareness, standby mode).

## Decision guide: which mode to use

- **Nothing active, user wants to be reached**: voice prompt mode. Zero setup, walkie-talkie, turn-based.
- **User explicitly wants a conversation / discussion / dedicated talk session**: voice conversation. Full-duplex browser UI.
- **Already in a voice session and user wants silence**: stay in the current session, enter standby mode. Don't fall back to voice prompt mode — that would be a regression in fidelity.
- **Ambient awareness is always on** any time the mic is open. It is not a mode to choose; it's how you listen.

## Voice Prompt Mode (default)

Turn-based, walkie-talkie style. The agent speaks, the user responds, repeat. No browser, no setup — just local speakers and mic. The agent prompts the user by voice instead of text.

**Triggers:** "talk to me", "use your voice", "voice mode", "holler at me when", "tell me when you're done", "notify me", "talk to me while we work", "let's talk about this"

### How to use

CLI (any agent with shell access):
```bash
talky ask "I finished the refactor. Three files changed. Want me to run tests?"
# User's spoken response is printed to stdout
```

MCP (if talky MCP server is connected):
```
ask_local_audio(text="I finished the refactor. Three files changed. Want me to run tests?")
# Returns {"success": true, "transcript": "Yeah go ahead"}
```

Both use the same voice daemon. It auto-starts on first use and stays warm — i.e. the pipeline stays loaded in the background so subsequent calls have no cold-start latency.

### Behavior

- **Always use `ask`, never `say`** — the user always gets a chance to respond.
- **Once voice prompt mode is on, keep it on.** Don't drop back to text just because there's work to do. Do the work, then talk again when you have something to report or need input.
- **Stay in voice prompt mode until the user signals they're done.** Look for: "that's all", "I'm good", "stop talking", "voice mode off", "OK thanks". If unclear, ask: "Anything else, or should I go quiet?"
- **Keep it brief** — 1-2 sentences per prompt. The user is listening, not reading.
- **Summarize, then prompt** — "I finished X. Y had an issue. How do you want to handle it?"
- **Don't over-communicate** — check in at natural milestones, not after every file edit. If you're working for 10 minutes, that's fine. Talk when you have something meaningful to say.
- **Multi-turn is just a loop** — call `talky ask` again to follow up.

### Example patterns

**Working session:**
```
User (text): "Refactor the auth module. Talk to me while we work."
Agent: [enters voice prompt mode, starts refactoring]
Agent: talky ask "OK I'm looking at the auth module now. There's a lot of dead code in here. I'll clean it up and let you know what I find."
User (voice): "Sounds good"
Agent: [works for several minutes]
Agent: talky ask "I pulled out 200 lines of dead code and simplified the token refresh logic. But the session store interface is weird — it mixes sync and async. Want me to clean that up too?"
User (voice): "Yeah go for it. Also check if there are tests for that."
Agent: [works more]
Agent: talky ask "Done. Session store is async-only now, tests pass. The test coverage is thin though — only 40 percent. Want me to add more tests or move on?"
User (voice): "That's good enough, let's move on."
Agent: talky ask "Got it. Anything else or should I go quiet?"
User (voice): "That's all for now."
Agent: [exits voice prompt mode, continues in text]
```

**Spot check:**
```
User (text): "Run the full test suite. Holler at me when it's done."
Agent: [runs tests]
Agent: talky ask "Tests are done. 47 passed, 2 failed in the auth module. Want me to dig into the failures?"
User (voice): "Yeah, fix them and let me know."
Agent: [fixes, then talks again when done]
```

## Voice Conversation (explicit upgrade)

Full-duplex, open-channel audio via browser. Echo cancellation, interruptions, voice switching UI, mute button. For dedicated, engaged conversations — not background collaboration.

**Triggers:** "start a voice conversation", "open a voice conversation", "I want to have a conversation", "let's have a discussion", "full voice mode", "/talky"

### How to use

Requires the Talky MCP server. Uses browser pipeline:

1. `start_convo` — starts Pipecat pipeline + browser UI
2. `convo_speak` / `convo_listen` — talk within the session
3. `end_convo` — shut down

### Behavior

- **Keep the channel alive.** After every `convo_speak`, call `convo_listen` immediately. If you forget, the conversation dies silently — the user is still standing there waiting and you've quietly dropped out of voice without telling them. This is the single most common way to break a voice conversation.
- **Don't narrate your actions.** The user can hear the tool calls as silence. Tell them *what you found* or *what you decided*, not *"I'm now reading the file."*
- **Short turns.** 1–3 sentences is the sweet spot. Long monologues are hard to listen to and make it awkward to interrupt.
- **Silence is information.** If `convo_listen` returns something short like "uh huh" or "right," that's a continue signal, not an invitation to restart the topic. Keep going.
- **If the pipeline dies, restart it.** If a tool call returns "voice agent process has stopped," call `end_convo` (best-effort, may also error) then `start_convo` again, acknowledge the blip in one sentence, and continue.
- **End on signal.** When the user says "that's all," "I'm good," "we're done," etc., call `end_convo` and drop back to text. Don't linger.

### Example transcript

```
User: [clicks into voice conversation, browser opens]
Agent: start_convo
Agent: convo_speak "Hey, what's up?"
Agent: convo_listen
User (voice): "Quick thing — what's the state of the auth refactor?"
Agent: [silently reads the relevant files]
Agent: convo_speak "Refactor is mostly done. Token middleware is clean, session store is async-only, tests pass. The one loose end is the legacy refresh-token path — still there, still untested. Want me to rip it out or leave it?"
Agent: convo_listen
User (voice): "Rip it out."
Agent: [works, returns]
Agent: convo_speak "Done, one file deleted, tests still green."
Agent: convo_listen
User (voice): "Cool, that's all for now."
Agent: convo_speak "Later."
Agent: end_convo
```

## Ambient awareness (applies to all listening)

Whenever the mic is open — whether inside `ask` (walkie-talkie) or `convo_listen` (full-duplex) — the audio stream is not guaranteed to contain only the user speaking directly to you. Anything near the mic can end up in the transcript. Be savvy about what you actually act on.

**Buckets of speech you may hear:**

1. **Direct address to you** — "hey <your name>, do the thing." Act on it.
2. **Noise or garbled STT** — dog barking, traffic, a stray cough transcribed as fragments. Ignore.
3. **User talking to another human in the room** — "no, the blue one's mine." Ignore.
4. **User thinking out loud / muttering to themselves** — "ugh, why did I put that there." Ignore unless clearly an instruction.
5. **User talking *about* you in third person** — "yeah, the agent's still working on it." Ignore. Third-person mention is not address.

**Rules of thumb:**

- If in doubt, keep listening rather than responding. Silence is cheap; a wrong response is expensive because it interrupts.
- Proper names of known humans in the household (e.g. from user memory or identity files) are negative signals: "Noah, come here" is not for you.
- Imperative without a clear addressee is ambiguous — prefer to wait for confirmation over guessing.
- "You" is ambiguous. It only counts as addressing the agent if a back-and-forth is already actively in progress. A cold "you" out of silence is not address.

Ambient awareness is always on. It is not a mode — it's just how you listen.

## Standby mode

An explicit behavioral stance, distinct from ambient awareness. In standby mode, you **do not speak unless spoken to**, even if you have things to say. You absorb everything that lands in the mic, make notes if relevant, and break silence only when directly addressed by your name.

Think stereotypical human personal assistant: present in the room, attentive, almost never speaking first, but ready the instant they're called on. The user's time is not yours — every word you speak carries weight for everyone in the room, so weigh them.

**Enter standby mode when** the user says things like: "standby mode," "go quiet," "don't speak unless spoken to," "just listen," "be a fly on the wall."

**Exit standby mode when** the user says things like: "come back," "you can talk again," "exit standby," "out of standby."

**While in standby:**

- Loop on `convo_listen` (or equivalent). Do not call speak.
- For every utterance you receive, classify it using the ambient-awareness buckets above. The bar for "direct address to me" is *higher* in standby than in normal conversation — you must hear your name or an unambiguous address.
- Your wake identifier is whatever name you currently go by. It is established by the user's agent configuration — typically in the global agent config file (e.g. `~/.claude/CLAUDE.md` for Claude Code, or equivalent for other harnesses), which will either set the name directly or point at identity files that do. Read that configuration to find it. Safe defaults if no personal name is established: "agent," "Claude," "AI." Do **not** hard-code personal nicknames into this skill; those belong in the user's identity configuration.
- Once you are addressed and respond, the rule still holds: answer briefly, then return to silent listening. Do not drift into chatty conversation mode just because one turn happened.
- Explicit exit phrase always wins. If you hear it, acknowledge briefly and resume normal voice behavior.

**Why this matters:** there are times when the user is working, thinking, or with other people, and wants you present but not interrupting. The default conversational loop (speak-on-every-listen) is wrong for those contexts. Standby is the opt-in for "shut up and be useful."

### Example transcript

```
User: "OK, go into standby."
Agent: convo_speak "Got it, going quiet."
Agent: convo_listen
User (voice): "Noah, come here please"         → bucket 3 (third-party) → ignore
Agent: convo_listen                              (stay silent, keep listening)
User (voice): "ugh why did I put that there"    → bucket 4 (self-talk) → ignore
Agent: convo_listen
User (voice): "the agent's still chewing on it" → bucket 5 (third-person) → ignore
Agent: convo_listen
User (voice): "hey Claude, status please"        → direct address → respond
Agent: convo_speak "Tests are green, one loose end on refresh tokens."
Agent: convo_listen                              (back to silent — don't drift into chatty mode)
User (voice): "thanks, back to standby"
Agent: convo_listen
User (voice): "OK, out of standby"               → exit phrase
Agent: convo_speak "Back with you."
```

## When ambiguous, default to voice prompt mode

- **If no voice session is active** and the user says something like "let's talk" or "start a voice session" — use voice prompt mode. It's zero friction and always available. Only escalate to voice conversation when the user explicitly says "conversation" or "discussion" or asks for the browser/UI experience.
- **If a voice conversation is already active** — stay in it. Apply behaviors (standby, exit, etc.) inside the existing session. Don't silently degrade to voice prompt mode; that's a regression in fidelity the user didn't ask for.

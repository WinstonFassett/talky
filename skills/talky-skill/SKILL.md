---
name: talky
description: Talky gives the agent a voice. Use when the user says "talk to me", "voice mode", "use your voice", "holler at me", "tell me when", "notify me", or wants a voice conversation. Two modes — voice prompt mode (walkie-talkie, turn-based, no setup) and voice conversation (full-duplex browser audio).
---

Talky lets the agent talk with the user. Two modes:

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

Both use the same voice daemon. It auto-starts on first use and stays warm.

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

## When ambiguous, default to voice prompt mode

If the user says something like "start a voice session" or "let's talk" — use voice prompt mode. It's zero friction and always available. Only escalate to voice conversation when the user explicitly says "conversation" or "discussion" or asks for the browser/UI experience.

---
name: talky
description: Communicate with the user by voice using local audio. Use `talky ask` to speak and listen for a response. Use when told to notify, check in, ask how to proceed, or have a voice collaboration.
---

Speak to the user and hear their voice response via local speakers and microphone.

## When to Use

- User says "notify me when done", "tell me when you're done", "ask me how to proceed"
- User says "let's do a voice collaboration" or "talk to me about this"
- You need clarification and the user has indicated they prefer voice
- You've completed a significant chunk of work and the user asked to be notified

## How It Works

Two ways to do the same thing — pick whichever is available:

### CLI (any agent with shell access)
```bash
talky ask "Hey, I finished the refactor. Want me to run tests?"
# User's spoken response printed to stdout
```

### MCP (if talky MCP server is connected)
```
ask_local_audio(text="Hey, I finished the refactor. Want me to run tests?")
# Returns {"success": true, "transcript": "Yeah go ahead and run them"}
```

Both use the same voice daemon under the hood. It auto-starts on first use and stays warm. No browser, no WebRTC, no setup.

## Guidelines

- **Always use `ask`, not `say`** — whenever you speak to the user, give them the chance to respond.
- **Keep it brief** — 1-2 sentences max per call. The user is listening, not reading.
- **Don't over-communicate** — one check-in at a natural milestone, not after every file edit.
- **Summarize what happened, then ask** — "I finished X and Y. Z had an issue. How do you want to handle it?"
- **If they say something, act on it** — their response is text in stdout. Read it and proceed.
- **Multi-turn is just a loop** — call `talky ask` again if you need to follow up.

## Example Patterns

### Notify when done
User says: "Let me know when you're done with the tests"
```bash
# ... after tests complete ...
talky ask "Tests are done. 47 passed, 2 failed — both in the auth module. Want me to look into the failures?"
# Read their response from stdout and act on it
```

### Ask how to proceed
User says: "Refactor this module. Ask me before making breaking changes."
```bash
# ... during refactor, you find a breaking change ...
talky ask "I need to change the return type of get_user from dict to a User dataclass. This breaks 3 callers. Should I update them too?"
```

### Voice collaboration session
User says: "Let's talk through the architecture"
```bash
talky ask "OK, I'm ready to talk architecture. What's on your mind?"
# They respond, you process, then:
talky ask "Got it. So you want the daemon to own all audio I/O and have CLI and MCP as thin clients. That makes sense. What about the browser pipeline — keep it separate?"
# Continue the loop
```

## Full Voice Conversation (Browser)

For a richer voice experience with echo cancellation, interruptions, voice switching, and a UI — use the browser pipeline instead:

1. Start with `start_convo` MCP tool
2. Use `convo_speak` / `convo_listen` for the conversation
3. End with `end_convo`

This requires the Talky MCP server running and a browser connection. Use when the user explicitly asks for a full voice conversation or says `/talky`.

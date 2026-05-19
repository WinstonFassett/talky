---
name: talky
description: Talky gives the agent a voice and ears. Use this skill whenever the user wants the agent to speak, listen, hold a voice conversation, go quiet, or change how it engages via audio — even if they don't say "talky" by name. Triggers include "talk to me", "voice mode", "use your voice", "holler at me", "tell me", "notify me", "let's talk", "start a voice conversation", "I want to have a conversation", "executive assistant mode", "standby mode", "go quiet", "just listen", "be a fly on the wall", "don't speak unless spoken to", "listen but don't respond".
---

## Which modality am I in?

**MCP-driven** (Claude, GPT-4o, etc.) — You have Talky MCP tools. You manage the loop: ensure daemon is up, start pipeline, speak, always listen after speaking. → See MCP Voice Conversation below.

**Local audio / voice prompt** — No sustained session. Use `talky ask` (CLI) or `ask_local_audio` (MCP) for one exchange at a time. → See Local Audio below.

**Harness-controlled backend** (Pi, etc.) — The harness feeds you text and pipes your output to TTS. No tools to call. Just respond naturally; communication rules still apply.

---

## CRITICAL: The listen loop (MCP-driven only)

**After every `convo_speak`, call `convo_listen`. Every time. No exceptions.**

Loop: `speak → listen → act → speak → listen → repeat`

Default state is **listening**. Exit only on explicit end signal ("that's all," "we're done"). After any command, return to listening.

**Long task:** narrate ("On it"), call `convo_listen` to catch interrupts while working, speak result when done, listen again.

---

## MCP Voice Conversation

Requires daemon on port 9090.

```bash
talky daemon   # start if not running
talky kill     # reclaim 9090 if stuck
```

**Tools:**
- `start_convo` — start pipeline + browser UI
- `join_convo()` — confirm pipeline live; no state mutation
- `convo_speak(text)` — speak
- `convo_listen()` — block until user speaks, returns transcript
- `request_leave()` — polite exit; returns `{left, user_interrupted, text?}`; if `user_interrupted`, resume — do not leave

**Startup:**
1. `start_convo`
2. `join_convo()`
3. `convo_speak("Claude here — what's on your mind?")` — daemon does NOT greet; silence until you do
4. `convo_listen()` — enter the loop

**Pipeline dies** ("voice agent process has stopped"): `start_convo` again, one-sentence acknowledgment, continue.

---

## Local Audio (Voice Prompt)

Turn-based, no browser.

- `talky ask "message"` (CLI) or `ask_local_audio(text="message")` (MCP) — user responds, you get transcript
- `say_local_audio(text="message")` — one-shot TTS, no response

Use `ask` when the user should respond. Stay in voice mode until dismissed. No listen loop to manage.

---

## How to speak

Applies in all modalities.

- **Two sentences max, ~30 words.** Fragments fine.
- **Speak only when addressed or you have a milestone/blocker.**
- Strip filler. "Got it" is fine. "Great question" is slop.
- Never read back what the user said. Act, then report.
- Write long, say short — heavy content to a durable artifact; voice gets the one-liner.
- No lists read aloud.

---

## How to listen

**Ambient awareness** — always on when mic is open. Ignore: noise, garbled STT, user talking to someone else, thinking out loud, third-person mentions. Act on direct address only. In doubt, keep listening.

**Standby mode** — explicit silence. Enter: "standby," "go quiet," "just listen." Exit: "come back," "you can talk." While in standby: loop `convo_listen`, require your name or unambiguous address before responding, return to silence after each response.

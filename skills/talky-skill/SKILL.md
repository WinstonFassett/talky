---
name: talky
description: Start a voice conversation using the Talky voice server
---

Start a voice conversation using the Talky voice server.

## Core Principle
When the user SPEAKS to you, your response must be primarily SPOKEN. Chat text is only for logs/reference. The user is listening, not reading chat. Always acknowledge spoken input with voice_speak() before doing anything else.

## Setup

The Talky Pi extension must be installed. See `docs/integrations/pi.md`.

```bash
# Quick test
pi -e /path/to/talky/pi-extension/index.ts

# Permanent
ln -s /path/to/talky/pi-extension ~/.pi/agent/extensions/talky
```

## Flow

1. Call `voice_start` or type `/voice` or say "I want a voice conversation"
2. Browser opens automatically for WebRTC audio
3. Greet the user with `voice_speak()`, then call `voice_listen()`
4. When the user SPEAKS (via voice_listen()):
   - Immediately acknowledge with `voice_speak()` - "Got it", "OK", "I'll do that", etc.
   - Do the work (edit files, run commands, etc.)
   - Call `voice_speak()` frequently for progress updates
   - Report the result with `voice_speak()`
   - Only then call `voice_listen()`
5. For simple questions: `voice_speak()` then immediately `voice_listen()`
6. To end: say goodbye with `voice_speak()`, then `voice_stop()`

The key principle: `voice_listen()` means "I'm done and ready for the user to talk." The user is listening, not reading chat.

## Guidelines

- Keep all voice messages to 1-2 short sentences
- Never work in silence — give voice progress updates
- Before destructive changes, ask for verbal confirmation
- Always call `voice_stop()` when the conversation ends

## CLI

```bash
talky say "Hello"                          # speak text
talky say --voice-profile cloud-male "Hi"  # with profile
talky --list-profiles                      # show profiles
talky --status                             # running processes
```
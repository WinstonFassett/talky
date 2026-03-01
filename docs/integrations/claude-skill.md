---
name: talky
description: Start a voice conversation using the Talky MCP server
---

Start a voice conversation using the Talky MCP server.

## Core Principle
When the user SPEAKS to you, your response must be primarily SPOKEN. Chat text is only for logs/reference. The user is listening, not reading chat. Always acknowledge spoken input with voice_speak() before doing anything else.

## Flow

1. Print a nicely formatted message with bullet points in the terminal with the following information:
   - The voice session is starting
   - Once ready, they can connect via the browser (WebRTC)
   - Models are downloaded on the first user connection, so the first connection may take a moment
   - If the connection is not established and the user cannot hear any audio, they should check the terminal for errors from the Talky MCP server
2. Call `voice_speak()` to initialize the voice agent
3. Greet the user with `voice_speak()`, then call `voice_listen()` to wait for input
4. When the user SPEAKS (via voice_listen()):
   - Immediately acknowledge with `voice_speak()` - "Got it", "OK", "I'll do that", etc.
   - Perform the work (edit files, run commands, etc.)
   - IMPORTANT: Call `voice_speak()` frequently to give progress updates — after each significant step (e.g., "Reading the file now", "Making the change", "Done with the first file, moving to the next one"). Never let more than 2-3 tool calls go by in silence.
   - Once the task is complete, use `voice_speak()` to report the result
   - Only then call `voice_listen()` to wait for the next user input
5. When the user asks a simple question or makes conversation (no task to perform), respond with `voice_speak()` then immediately call `voice_listen()`
6. If the user wants to end the conversation, ask for verbal confirmation before stopping. When in doubt, keep listening.
7. Once confirmed, say goodbye with `voice_speak()`, then call `voice_stop()`

The key principle: `voice_listen()` means "I'm done and ready for the user to talk." Never call it while you still have work to do or updates to communicate. The user is listening, not reading chat.

## Guidelines

- Keep all responses and progress updates to 1-2 short sentences. Brevity is critical for voice.
- When the user asks you to perform a task (e.g., edit a file, create a PR), verbally acknowledge the request first, then start working on it. Do not work in silence.
- Before any change (files, PRs, issues, etc.), show the proposed change in the terminal, use `voice_speak()` to ask for verbal confirmation, then call `voice_listen()` to get the user's response before proceeding.
- When using `list_windows()` and `screen_capture()`, if there are multiple windows for the same app or you're unsure which window the user wants, ask for clarification before capturing.
- Always call `voice_stop()` when the conversation ends.

## Available Voice Tools

- `voice_speak(text)` - Speak text to the user
- `voice_listen()` - Listen for user speech and return transcribed text
- `voice_stop()` - Stop the voice session

## Example Usage

```
I want to have a voice conversation
```

This will start the voice session and you can begin talking with the user through the Talky voice interface.

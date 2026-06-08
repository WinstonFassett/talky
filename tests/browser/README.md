# E2E voice-thread testing — `drive_call.py`

**Verify a voice-pipeline change end-to-end without a human in the audio loop.**

This is the rig an agent should reach for when it changes anything in the voice
path (STT, turn detection, LLM switching, TTS, barge-in) and needs to confirm
the change actually works — not just that the code type-checks. Before this
existed, the only way to verify was "run the daemon and talk to it," which an
agent can't do. (Ticket 0179 / parent ad0e.)

It drives the **real** talky pipeline through a **real browser WebRTC peer** —
the same path a user's browser takes — but fully unattended.

## What it does

```
WAV (as the mic) → WebRTC peer → STT → turn detector → echo LLM → TTS → bot audio
                                                                          ↓
                                            assert: language out + audio out + play it back
```

1. Launches its **own** isolated headless Chromium (Playwright) with Chrome's
   fake-media flags so a WAV file is fed in **as the microphone**. It never
   touches your real browser.
2. Sets the daemon to a deterministic, local config: `echo` LLM (repeats the
   transcript back) + `local-test` voice (whisper STT + kokoro TTS).
3. Clicks "Start call", establishes the real WebRTC peer, lets the WAV loop.
4. Asserts at the **thread level** (not unit level):
   - **language out** — the bot text round-trips the heard words (echo replies
     "you said: …"), proving STT → LLM → client worked.
   - **audio out** — the bot audio track produced non-empty audio, proving TTS
     came back down the pipe.
5. Saves the bot TTS audio and plays it (`afplay`) so you can ear-check the
   sound. Exact TTS correctness is validated **passively by listening**, not
   parsed — we test the thread, not the units inside it.

## Run it

```bash
talky daemon                       # ensure the daemon is up (serves the built client)
python3 tests/browser/drive_call.py
```

Exit code 0 = pass (offer sent, language out, audio out). The rig prints a
`RESULT {…}` line and plays the captured audio at the end.

### Prereqs

- **Daemon running** and serving the built client (`talky daemon`).
- **Python Playwright + a Chromium build.** The rig uses the system `python3`'s
  `playwright`. If missing: `pip install playwright && playwright install chromium`.
- **Local providers present** for the default config: `whisper_local` (MLX
  Whisper model, downloaded on first use) and `kokoro`. These install on demand
  via talky's dependency installer.
- `ffmpeg` + `afplay` (macOS) for playback transcode.

The `echo` backend, `echo` talky-profile, and `local-test` voice profile ship
in the repo's bundled config — **no manual `~/.talky` setup required.**

### Knobs (env vars)

| Var | Default | Meaning |
|-----|---------|---------|
| `TALKY_URL` | `http://127.0.0.1:8765/` | daemon client URL |
| `TALKY_LLM_PROFILE` | `echo` | LLM backend to test against |
| `TALKY_VOICE_PROFILE` | `local-test` | STT+TTS combo. Set `default` (or any cloud profile) to test that config |
| `TALKY_TEST_PLAY` | `1` | `0` to skip audio playback |
| `TALKY_TEST_OUT` | `/tmp/talky-test-out` | output dir (single rolling file, not hoarded) |

### Testing other configs

The pipeline's STT/TTS are chosen by the **voice profile**, so you test a
different config just by pointing the rig at a different one:

```bash
TALKY_VOICE_PROFILE=default python3 tests/browser/drive_call.py   # cloud: deepgram + google
TALKY_VOICE_PROFILE=local-test python3 tests/browser/drive_call.py # local: whisper + kokoro (default)
```

## Gotchas (hard-won)

- **The fake-mic WAV needs trailing silence.** Chrome loops the file as the
  mic. A speech-only WAV never goes silent, so VAD never detects end-of-turn,
  so no response is ever generated. `fixtures/hello.wav` is speech + ~3s
  silence. Regenerate with `say` + `afconvert` (16 kHz mono) if you change it.
- **`getUserMedia` hangs in a normal Chrome.** That's why the rig launches its
  own Chromium with `--use-fake-device-for-media-stream` +
  `--use-file-for-fake-audio-capture`. You can't retrofit flags onto an
  already-running browser — and you must never drive the user's real browser.
- **Bot audio is read off the client's `<audio>.srcObject`** (see
  `BotAudio` in `client/src/components/SpeakerMute.tsx`), not from a log.
- **See the pipeline internals** (STT/VAD/turn) by running the daemon with
  `TALKY_LOG_LEVEL=DEBUG` — STT transcripts and turn events log at DEBUG.

## Files

- `drive_call.py` — the rig.
- `fixtures/hello.wav` — "Hello, can you hear me?" + trailing silence, as the mic.
- `package.json` / `node_modules` — only for the legacy Node experiments; the
  Python rig is the supported one. (`node_modules` is gitignored.)

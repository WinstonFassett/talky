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
                                                          ↓               ↓
                                            assert: transcript/karaoke   audio out
                                            RENDERED in the client       + play it back
```

1. Launches its **own** isolated headless Chromium (Playwright) with Chrome's
   fake-media flags so a WAV file is fed in **as the microphone**. It never
   touches your real browser.
2. Sets the daemon to a deterministic, local config: `echo` LLM (repeats the
   transcript back) + `local-test` voice (whisper STT + kokoro TTS).
3. Clicks "Start call", establishes the real WebRTC peer, lets the WAV loop.
4. Asserts at the **thread level** (not unit level), in two tiers (ticket af68):
   - **Tier 0 — the audio floor (PRIMARY).**
     - **language out** — the bot text round-trips the heard words (echo replies
       "you said: …"), proving STT → LLM → client worked.
     - **audio out** — the bot audio track produced non-empty audio, proving TTS
       came back down the pipe.
   - **Tier 0 must pass; Tier 1 is the client-render layer** —
     - **client render** — the transcript/"karaoke" surface actually **rendered**
       in the browser, asserted via stable `data-testid` hooks
       (`transcript-list`, `transcript-message`, `karaoke-part`) on talky's own
       JSX. Catches the client misrendering frames the pipeline sent correctly —
       the failure that "usually requires the user." A render break trips Tier 1
       while Tier 0 stays green, so the rig tells pipeline breaks from render
       breaks apart. The `bot-speaking-bar` hook is also watched but only
       **reported** (transient/timing-sensitive — gating on it would risk
       unattended flakiness).
     - The hooks live on talky's own components (not voice-ui-kit internals), so
       the contract survives the planned kit rip-out: re-attach the same testids
       to the replacement.
5. Saves the bot TTS audio and plays it (`afplay`) so you can ear-check the
   sound. Exact TTS correctness is validated **passively by listening**, not
   parsed — we test the thread, not the units inside it.

## Run it

```bash
talky daemon                       # ensure the daemon is up (serves the built client)
python3 tests/browser/drive_call.py
```

Exit code 0 = pass (offer sent, language out, audio out, **client render ok**).
The rig prints a `RESULT {…}` line and plays the captured audio at the end.

**After client changes, rebuild + restart first** — the daemon serves
`client/dist`, not source:

```bash
cd client && npm run build && cd ..
talky kill && talky daemon
```

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

## Approval-loop rig — `drive_approval.py` (ticket 3e56)

Sibling rig that proves the **dangerous-command approval surface** end to end,
unattended — the human-in-the-loop gate that is *exactly* "the stuff that requires
me." Unlike `drive_call.py` (deterministic `echo`), this drives a **real, gate-capable
backend** (Hermes), because the approval event is only emitted by a real agent turn:

```
WAV (a guarded shell command, as the mic) → STT → REAL Hermes turn
  → Hermes dangerous-command guard fires → talky's set_approval_callback
  → permissionRequest (SSE) → PermissionBanner RENDERS (data-testid)
  → rig clicks Allow → POST /api/permission/grant 200
  → HermesLLMService.resolve_permission() unblocks the Hermes thread
```

```bash
talky daemon
python3 tests/browser/drive_approval.py     # GREEN: banner rendered + grant 200
```

Exit 0 = banner rendered (emit→SSE→render half) **and** grant returned 200 (the
resolve half: server found the pending Hermes request and resolved it). A `409`
means the gate never fired or the server can't see the backend → RED. Proven to go
RED by breaking the `permission-banner` testid in the served bundle.

**Knobs:** `TALKY_LLM_PROFILE` (default `hermes` — must be gate-capable),
`TALKY_VOICE_PROFILE` (`local-test`), `TALKY_APPROVAL_WAV`,
`TALKY_APPROVAL_DECISION` (`allow`|`deny`).

**Gotchas specific to this rig:**
- It costs **one real model call per Hermes turn** (Hermes routes via
  `~/.hermes/config.yaml` — point it at a cheap/free model). Not offline like
  `drive_call.py`.
- **Hermes needs `HERMES_INTERACTIVE` to gate at all.** Embedded talky is neither
  CLI nor gateway, so Hermes would AUTO-APPROVE dangerous commands; the backend
  sets `HERMES_INTERACTIVE=1` on its agent thread so the approval callback is
  consulted. (See `src/talky/backends/hermes.py`.)
- **whisper mis-hears "chmod"** ("mod"/"shmod") on early loops — the looping mic +
  a generous banner window give repeated shots until a correct transcription lands.
  This is real-mic nondeterminism (per 0179); fine here since one correct loop is
  enough.

## Files

- `drive_call.py` — the audio/render rig (echo backend).
- `drive_approval.py` — the approval-loop rig (real Hermes turn → gate → grant).
- `fixtures/hello.wav` — "Hello, can you hear me?" + trailing silence, as the mic.
- `fixtures/approval-cmd.wav` — a guarded shell command (`chmod 777 …`) + trailing
  silence, as the mic, for `drive_approval.py`.
- `package.json` / `node_modules` — only for the legacy Node experiments; the
  Python rig is the supported one. (`node_modules` is gitignored.)

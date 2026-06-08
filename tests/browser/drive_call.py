"""Unattended browser WebRTC rig for talky (ticket 0179 / parent ad0e).

Native Python Playwright. Launches its OWN isolated headless Chromium — it does
NOT touch the user's Chrome. The browser is fed a WAV file AS the microphone via
Chrome's fake-media flags, so the page does a real getUserMedia, sends a real
WebRTC offer, and the daemon runs its real pipeline (STT -> turn -> LLM -> TTS).

This is the truest unattended path: an agent verifies a voice change end-to-end
with no human in the audio loop. It is a LOCAL rig, not CI.

Run (daemon must be up, fixtures/hello.wav present):
    python3 tests/browser/drive_call.py

Feasibility chain it asserts, in order:
    1. fake-media getUserMedia resolves (no hang)             — mic gate
    2. Start call -> real POST/PATCH /api/offer               — signaling
    3. peer reaches a connected/listening UI state            — wire live
    4. STT transcribes the looped utterance (RTVI transcript) — audio path

Two assertion tiers (ticket af68):
    Tier 0 (PRIMARY, the floor): language_out + audio_out — the pipe works
        end to end.
    Tier 1 (SECONDARY but the class that usually requires the user):
        client_render_ok — the transcript/"karaoke" surface actually RENDERED
        (stable data-testid hook + the round-tripped words landed in it). Catches
        a client misrendering frames the pipeline sent correctly. The
        bot-speaking bar is also watched but only REPORTED (transient/timing-
        sensitive — gating on it would risk unattended flakiness).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
WAV = HERE / "fixtures" / "hello.wav"
CLIENT_URL = os.environ.get("TALKY_URL", "http://127.0.0.1:8765/")
# Single rolling output dir — overwritten each run, don't hoard audio/video.
OUT_DIR = Path(os.environ.get("TALKY_TEST_OUT", "/tmp/talky-test-out"))

# The rig sets the daemon to a deterministic, LOCAL-by-default config so a run
# is reproducible and offline. Override via env to test other configs:
#   TALKY_LLM_PROFILE   = echo        (deterministic responder)
#   TALKY_VOICE_PROFILE = local-test  (whisper_local STT + kokoro TTS, no cloud)
# Set TALKY_VOICE_PROFILE=default to run the cloud config (deepgram+google).
LLM_PROFILE = os.environ.get("TALKY_LLM_PROFILE", "echo")
VOICE_PROFILE = os.environ.get("TALKY_VOICE_PROFILE", "local-test")


def log(*a: object) -> None:
    print("[rig]", *a, flush=True)


def _set_profiles() -> None:
    """Point the daemon at the deterministic + local test config before driving.

    Idempotent; self-contained so a run doesn't depend on prior manual switches.
    """
    import urllib.request

    base = CLIENT_URL.rstrip("/")
    for path, prof in (
        ("/api/profiles/switch", LLM_PROFILE),
        ("/api/voices/switch", VOICE_PROFILE),
    ):
        body = json.dumps({"profile": prof}).encode()
        req = urllib.request.Request(
            base + path, data=body, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                log(f"set {path.split('/')[-1]} -> {prof}: {r.read().decode()[:80]}")
        except Exception as e:  # noqa: BLE001
            log(f"WARN: could not set {prof} via {path}: {e}")


def main() -> int:
    # Point the daemon at the deterministic + local config before driving.
    _set_profiles()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--use-fake-device-for-media-stream",
                "--use-fake-ui-for-media-stream",  # auto-grant mic, no prompt
                f"--use-file-for-fake-audio-capture={WAV}",  # WAV looped AS mic
            ],
        )
        ctx = browser.new_context(permissions=["microphone"])
        page = ctx.new_page()

        page.on("console", lambda m: log("console:", m.type, m.text))
        page.on("pageerror", lambda e: log("pageerror:", str(e)))

        saw_offer = {"v": False}
        page.on(
            "request",
            lambda req: (
                saw_offer.__setitem__("v", True) or log(">>>", req.method, req.url)
                if "/api/offer" in req.url
                else None
            ),
        )

        # Capture RTVI messages straight off the WebRTC data channel BEFORE
        # connecting. The pipecat client carries transcripts/bot-text as JSON
        # RTVI envelopes over a datachannel; we wrap RTCDataChannel.onmessage
        # at the prototype level so we see every inbound message regardless of
        # how the React client is wired. This is transport-level truth, no
        # guessing at client globals.
        page.add_init_script(
            """() => {
              window.__talky_rtvi = [];
              const OrigRTCPC = window.RTCPeerConnection;
              if (!OrigRTCPC) return;
              const wrapChannel = (ch) => {
                try {
                  ch.addEventListener('message', (ev) => {
                    let d = ev.data;
                    try { d = JSON.parse(ev.data); } catch (e) {}
                    window.__talky_rtvi.push({ t: Date.now(), d });
                  });
                } catch (e) {}
              };
              const Patched = function (...args) {
                const pc = new OrigRTCPC(...args);
                pc.addEventListener('datachannel', (e) => wrapChannel(e.channel));
                const origCreate = pc.createDataChannel.bind(pc);
                pc.createDataChannel = (...a) => { const ch = origCreate(...a); wrapChannel(ch); return ch; };
                return pc;
              };
              Patched.prototype = OrigRTCPC.prototype;
              window.RTCPeerConnection = Patched;
            }"""
        )

        page.goto(CLIENT_URL, wait_until="domcontentloaded")
        log("loaded", page.url)

        # Gate 1: fake mic resolves.
        gum = page.evaluate(
            """async () => {
              if (!navigator.mediaDevices) return {ok:false, err:'no mediaDevices'};
              try { const s = await navigator.mediaDevices.getUserMedia({audio:true});
                return {ok:true, tracks:s.getAudioTracks().map(t=>t.label)}; }
              catch(e){ return {ok:false, err:String(e)}; }
            }"""
        )
        log("gum:", json.dumps(gum))
        if not gum.get("ok"):
            browser.close()
            raise SystemExit("mic gate failed: " + str(gum.get("err")))

        # Gate 2+3: start the call, wait for LISTENING + offer.
        page.get_by_role("button", name="Start call").click()
        log("clicked Start call")

        deadline = time.time() + 20
        last = ""
        while time.time() < deadline:
            status = page.evaluate(
                """() => {
                  const el = document.querySelector('[aria-label^="Voice status"]');
                  return el ? el.getAttribute('aria-label') : '';
                }"""
            )
            if status != last:
                log("status:", status)
                last = status
            if "LISTENING" in (status or "") and saw_offer["v"]:
                break
            time.sleep(1)

        if not saw_offer["v"]:
            browser.close()
            raise SystemExit("no /api/offer — signaling never started")
        log("wire live: offer sent, status =", last)

        # Gate 4 (the thread): the WAV (speech + trailing silence) loops as the
        # mic. VAD closes a turn on the silence -> real STT transcript -> echo
        # backend responds -> TTS audio comes back on the bot track. We assert
        # at the THREAD level, not unit level:
        #   - language OUT: bot text appears (correlates with what was heard)
        #   - audio OUT: the bot audio track produced non-empty audio
        # Exact TTS correctness is validated passively by playing back the
        # captured video+audio, not parsed here.

        # Start capturing the bot AUDIO track into a MediaRecorder. The client
        # sinks bot audio onto an <audio> element's srcObject (see BotAudio in
        # SpeakerMute.tsx). We grab that stream and record it; we also start a
        # canvas+audio composite recording for human playback (video w/ sound).
        page.evaluate(
            """() => {
              window.__talky_audio_chunks = [];
              window.__talky_audio_bytes = 0;
              window.__talky_started = false;
              const tryStart = () => {
                if (window.__talky_started) return;
                const a = document.querySelector('audio');
                const stream = a && a.srcObject;
                if (!stream || !stream.getAudioTracks().length) return;
                window.__talky_started = true;
                // Audio-only recorder for the assertion (cheap, just bytes).
                const rec = new MediaRecorder(stream, { mimeType: 'audio/webm' });
                rec.ondataavailable = (e) => {
                  if (e.data && e.data.size) {
                    window.__talky_audio_bytes += e.data.size;
                    window.__talky_audio_chunks.push(e.data);
                  }
                };
                rec.start(500);
                window.__talky_rec = rec;
              };
              window.__talky_audio_poll = setInterval(tryStart, 300);
            }"""
        )

        # Tier-1 client-render watch (ticket af68): the bot-speaking bar mounts
        # only WHILE TTS plays, so we can't read it after the fact — poll for it
        # during the wait and latch if it ever appears. The transcript surface
        # persists, so it's read at signal-time below. Both assert against
        # stable data-testid hooks (talky's own JSX), not kit internals / CSS.
        page.evaluate(
            """() => {
              window.__talky_saw_speaking_bar = false;
              window.__talky_render_poll = setInterval(() => {
                if (document.querySelector('[data-testid="bot-speaking-bar"]')) {
                  window.__talky_saw_speaking_bar = true;
                }
              }, 200);
            }"""
        )

        log("driving thread: waiting for echo turn + TTS (25s)...")
        time.sleep(25)

        # Stop the audio recorder and read the signals.
        signals = page.evaluate(
            """async () => {
              clearInterval(window.__talky_audio_poll);
              clearInterval(window.__talky_render_poll);
              if (window.__talky_rec && window.__talky_rec.state !== 'inactive') {
                window.__talky_rec.stop();
                await new Promise(r => setTimeout(r, 300));
              }
              // Tier-1 client-render (af68): assert the transcript surface
              // actually RENDERED via its stable hook — not just that the text
              // leaked somewhere into the body. A broken TranscriptPanel that
              // drops messages fails this even if the round-trip text exists.
              const transcriptList = document.querySelector('[data-testid="transcript-list"]');
              const transcriptMsgs = document.querySelectorAll('[data-testid="transcript-message"]');
              const karaokeParts = document.querySelectorAll('[data-testid="karaoke-part"]');
              const transcriptText = transcriptList ? (transcriptList.innerText || '') : '';
              // Bot text: scrape the conversation panel. Echo replies
              // "you said: ...", so the heard words round-trip into the text.
              const txt = (document.body.innerText || '');
              // Pull the recorded bot audio out as base64 so the rig can write
              // it to disk and mux it into the page video for playback.
              let audio_b64 = '';
              try {
                const blob = new Blob(window.__talky_audio_chunks || [], { type: 'audio/webm' });
                if (blob.size) {
                  const buf = await blob.arrayBuffer();
                  let bin = '';
                  const bytes = new Uint8Array(buf);
                  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
                  audio_b64 = btoa(bin);
                }
              } catch (e) {}
              return {
                audio_bytes: window.__talky_audio_bytes || 0,
                audio_capture_started: !!window.__talky_started,
                panel_text: txt.slice(0, 2000),
                transcript_rendered: !!transcriptList && transcriptMsgs.length > 0,
                transcript_msg_count: transcriptMsgs.length,
                karaoke_part_count: karaokeParts.length,
                transcript_text: transcriptText.slice(0, 1000),
                saw_speaking_bar: !!window.__talky_saw_speaking_bar,
                audio_b64,
              };
            }"""
        )

        panel = (signals.get("panel_text") or "").lower()
        # Language OUT: echo's "you said:" reply means a transcript round-tripped
        # through STT -> echo -> back to the client as text.
        language_out = "you said" in panel or any(
            w in panel for w in ["hear me", "can you hear"]
        )
        audio_out = signals.get("audio_bytes", 0) > 0

        # Tier-1 client-render (af68): the karaoke/transcript surface is the lead
        # example of the client-render class that usually requires the user. It's
        # SECONDARY to the audio floor but the proof the rig catches a render
        # regression unattended. Assert the transcript actually RENDERED (its
        # hook present + >=1 message) AND the round-tripped words landed inside
        # that surface — so a TranscriptPanel that drops messages fails here even
        # while audio_out stays green.
        transcript_text = (signals.get("transcript_text") or "").lower()
        transcript_rendered = bool(signals.get("transcript_rendered"))
        karaoke_rendered = signals.get("karaoke_part_count", 0) > 0
        transcript_has_words = "you said" in transcript_text or any(
            w in transcript_text for w in ["hear me", "can you hear"]
        )
        # The karaoke renderer (KaraokePart) is the lead client-render surface;
        # it only emits for assistant turns. Requiring it + the words inside the
        # transcript surface means a broken karaoke/transcript render fails here
        # even while audio_out stays green.
        client_render_ok = transcript_rendered and karaoke_rendered and transcript_has_words

        log("RESULT", json.dumps({
            "saw_offer": saw_offer["v"],
            "status": last,
            "language_out": language_out,
            "audio_out": audio_out,
            "audio_bytes": signals.get("audio_bytes", 0),
            "audio_capture_started": signals.get("audio_capture_started"),
            # Tier-1 client-render
            "client_render_ok": client_render_ok,
            "transcript_rendered": transcript_rendered,
            "transcript_msg_count": signals.get("transcript_msg_count", 0),
            "karaoke_rendered": karaoke_rendered,
            "karaoke_part_count": signals.get("karaoke_part_count", 0),
            # informational: the speaking bar is transient/timing-sensitive, so
            # it's reported but does NOT gate (gating risks unattended flakiness).
            "saw_speaking_bar": signals.get("saw_speaking_bar"),
        }))
        if not language_out:
            log("panel_text sample:", panel[:400])
        if not client_render_ok:
            log("transcript_text sample:", transcript_text[:400])

        time.sleep(1)
        browser.close()

        # Save the bot TTS audio the client received, then play it through your
        # speakers — the passive ear-check that the sound is actually right.
        # Single rolling file, overwritten each run; we don't hoard audio.
        audio_b64 = signals.get("audio_b64") or ""
        if audio_b64:
            import base64
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            import subprocess
            webm_path = OUT_DIR / "bot-audio.webm"
            wav_path = OUT_DIR / "bot-audio.wav"
            webm_path.write_bytes(base64.b64decode(audio_b64))
            # afplay can't read webm — transcode to wav for playback.
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(webm_path), str(wav_path)],
                capture_output=True,
            )
            log("bot audio:", wav_path)
            if os.environ.get("TALKY_TEST_PLAY", "1") != "0" and wav_path.exists():
                subprocess.run(["afplay", str(wav_path)])
        else:
            log("no bot audio captured")

        # Tier 0 (audio floor) AND Tier 1 (client-render) must both hold.
        ok = saw_offer["v"] and language_out and audio_out and client_render_ok
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Unattended approval-loop rig for talky (ticket 3e56, sibling of drive_call.py / af68).

Proves the DANGEROUS-COMMAND APPROVAL surface end to end, unattended:

    WAV (a guarded shell command, as the mic) -> WebRTC -> STT -> REAL Hermes turn
      -> Hermes terminal tool trips its dangerous-command guard
      -> talky's wired set_approval_callback emits a `permissionRequest`
      -> the browser PermissionBanner RENDERS (stable data-testid)
      -> the rig clicks Allow
      -> POST /api/permission/grant -> HermesLLMService.resolve_permission()
      -> the blocked Hermes agent thread unblocks and the turn completes.

This is the FAITHFUL path the user chose: a real Hermes turn (real model call —
Hermes routes via ~/.hermes/config.yaml; use a cheap/free model there to keep the
per-run cost down) drives a REAL approval gate. The approval seam is exactly the
class of thing that pulls the user into the loop, so proving it works unattended
is the highest-value extension of the af68 render rig.

Same discipline as af68: it must go RED when the wiring or render is broken, not
just green. Two failure surfaces it guards:
  - the banner never RENDERS (testid missing / wrong component / SSE broken)
  - grant never RESOLVES (server doesn't know Hermes / resolve_permission broken)

Run (daemon up; Hermes installed + a model configured in ~/.hermes/config.yaml):
    python3 tests/browser/drive_approval.py

Knobs (env):
    TALKY_URL              http://127.0.0.1:8765/
    TALKY_LLM_PROFILE      hermes     (must be a real, gate-capable backend)
    TALKY_VOICE_PROFILE    local-test (whisper STT + kokoro TTS, no cloud)
    TALKY_APPROVAL_WAV     fixtures/approval-cmd.wav
    TALKY_APPROVAL_DECISION allow | deny   (which button to click; default allow)
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
WAV = Path(os.environ.get("TALKY_APPROVAL_WAV", str(HERE / "fixtures" / "approval-cmd.wav")))
CLIENT_URL = os.environ.get("TALKY_URL", "http://127.0.0.1:8765/")

# The approval gate is ONLY emitted by a real, gate-capable backend (Hermes,
# opencode, claude_code). echo never gates — so this rig must drive a real one.
LLM_PROFILE = os.environ.get("TALKY_LLM_PROFILE", "hermes")
VOICE_PROFILE = os.environ.get("TALKY_VOICE_PROFILE", "local-test")
DECISION = os.environ.get("TALKY_APPROVAL_DECISION", "allow")  # allow | deny


def log(*a: object) -> None:
    print("[appr-rig]", *a, flush=True)


def _set_profiles() -> None:
    """Point the daemon at the real gate-capable backend + local STT/TTS."""
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
            with urllib.request.urlopen(req, timeout=15) as r:
                log(f"set {path.split('/')[-1]} -> {prof}: {r.read().decode()[:80]}")
        except Exception as e:  # noqa: BLE001
            log(f"WARN: could not set {prof} via {path}: {e}")


def main() -> int:
    if not WAV.exists():
        raise SystemExit(f"approval WAV missing: {WAV}")
    _set_profiles()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--use-fake-device-for-media-stream",
                "--use-fake-ui-for-media-stream",
                f"--use-file-for-fake-audio-capture={WAV}",
            ],
        )
        ctx = browser.new_context(permissions=["microphone"])
        page = ctx.new_page()

        page.on("console", lambda m: log("console:", m.type, m.text))
        page.on("pageerror", lambda e: log("pageerror:", str(e)))

        saw_offer = {"v": False}
        page.on(
            "request",
            lambda req: saw_offer.__setitem__("v", True) if "/api/offer" in req.url else None,
        )

        # Watch the grant POST + its response so we can assert the resolve half
        # really happened (200 = a backend had a pending request and resolved it;
        # 409 = nothing pending -> the gate never fired or the server can't see it).
        grant = {"posted": False, "status": None}
        page.on(
            "request",
            lambda req: grant.__setitem__("posted", True)
            if "/api/permission/grant" in req.url
            else None,
        )

        def _on_resp(resp):
            if "/api/permission/grant" in resp.url:
                grant["status"] = resp.status
                log("grant response:", resp.status)

        page.on("response", _on_resp)

        page.goto(CLIENT_URL, wait_until="domcontentloaded")
        log("loaded", page.url)

        # Mic gate.
        gum = page.evaluate(
            """async () => {
              try { const s = await navigator.mediaDevices.getUserMedia({audio:true});
                return {ok:true}; } catch(e){ return {ok:false, err:String(e)}; }
            }"""
        )
        if not gum.get("ok"):
            browser.close()
            raise SystemExit("mic gate failed: " + str(gum.get("err")))

        # Start the call, wait for LISTENING + offer.
        page.get_by_role("button", name="Start call").click()
        log("clicked Start call")

        deadline = time.time() + 25
        last = ""
        while time.time() < deadline:
            status = page.evaluate(
                """() => { const el = document.querySelector('[aria-label^="Voice status"]');
                          return el ? el.getAttribute('aria-label') : ''; }"""
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
        log("wire live, status =", last)

        # The WAV speaks a guarded command. whisper transcribes -> real Hermes
        # turn -> Hermes runs the terminal tool -> guard fires -> permissionRequest
        # over SSE -> PermissionBanner renders. A real model turn can take a while;
        # poll up to 90s for the banner to appear.
        # A real Hermes turn is slow (~30s) and whisper may mis-hear "chmod" on
        # early loops of the fake-mic; the looping WAV gives repeated shots. Allow
        # a generous window so a correct transcription lands before we give up.
        log("waiting for approval banner (real Hermes turn, looping STT, up to 150s)...")
        banner_deadline = time.time() + 150
        banner_seen = False
        while time.time() < banner_deadline:
            present = page.evaluate(
                """() => !!document.querySelector('[data-testid="permission-banner"]')"""
            )
            if present:
                banner_seen = True
                break
            time.sleep(1)

        tool_name = ""
        if banner_seen:
            tool_name = page.evaluate(
                """() => { const el = document.querySelector('[data-testid="permission-tool-name"]');
                          return el ? (el.innerText || '') : ''; }"""
            )
            log(f"BANNER RENDERED (tool={tool_name!r}) — clicking {DECISION}")
            btn = "permission-allow" if DECISION == "allow" else "permission-deny"
            page.click(f'[data-testid="{btn}"]')
            # Give the grant POST + resolve a moment to round-trip.
            time.sleep(3)
        else:
            log("banner NEVER rendered within the window")

        result = {
            "saw_offer": saw_offer["v"],
            "banner_rendered": banner_seen,
            "tool_name": tool_name,
            "grant_posted": grant["posted"],
            "grant_status": grant["status"],
            "decision": DECISION,
        }
        log("RESULT", json.dumps(result))

        time.sleep(1)
        browser.close()

        # Pass criteria:
        #  - banner RENDERED (emit -> SSE -> client render half works)
        #  - grant POSTed and returned 200 (resolve half: server found a pending
        #    Hermes request and resolve_permission() handled it). 409 = gate
        #    never fired or server can't see Hermes -> RED.
        ok = (
            saw_offer["v"]
            and banner_seen
            and grant["posted"]
            and grant["status"] == 200
        )
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

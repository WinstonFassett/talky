"""T3 thread picker rig (WIN-564): open the client, pick an existing T3 thread, talk to it.

Expands the "T3 Code threads" sidebar section, screenshots it, clicks the thread whose title
contains ``--thread``, checks the thread's recent messages show as context, starts a call,
types ``--say`` and waits for a reply containing ``--expect`` (proof the message reached
THAT thread, which already holds the fact). Point it at an isolated daemon:

    TALKY_URL=http://127.0.0.1:18765/ python3 tests/browser/drive_t3_pick.py \\
        --thread "WIN-564 scratch" --say "What is the secret fruit? One word." --expect mango

Never aim it at a T3 thread you care about: it sends a real message.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.request
import wave
from pathlib import Path

from playwright.sync_api import sync_playwright

CLIENT_URL = os.environ.get("TALKY_URL", "http://127.0.0.1:8765/")
OUT_DIR = Path(os.environ.get("TALKY_TEST_OUT", "/tmp/talky-test-out"))


def log(*a: object) -> None:
    print("[t3-pick]", *a, flush=True)


def _silence_wav() -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "silence.wav"
    if not path.exists():
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\x00\x00" * 16000 * 5)
    return path


def _post(path: str, body: dict) -> None:
    req = urllib.request.Request(
        CLIENT_URL.rstrip("/") + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    urllib.request.urlopen(req, timeout=10).read()


def _page_text(page) -> str:  # noqa: ANN001
    return page.evaluate("() => document.body.innerText")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--thread", required=True, help="substring of the T3 thread title to pick")
    ap.add_argument("--say", required=True)
    ap.add_argument("--expect", action="append", default=[])
    ap.add_argument("--voice", default=os.environ.get("TALKY_VOICE_PROFILE", "local-test"))
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--mobile", action="store_true", help="390x844 viewport (sessions live in a drawer)")
    ap.add_argument("--shots", default=str(OUT_DIR))
    args = ap.parse_args()
    shots = Path(args.shots)
    shots.mkdir(parents=True, exist_ok=True)

    _post("/api/voices/switch", {"profile": args.voice})
    failures: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", f"--use-file-for-fake-audio-capture={_silence_wav()}"],
        )
        ctx = browser.new_context(
            permissions=["microphone"], viewport={"width": 390, "height": 844} if args.mobile else {"width": 1280, "height": 800}
        )
        page = ctx.new_page()
        page.on("pageerror", lambda e: log("pageerror:", str(e)))
        page.goto(CLIENT_URL, wait_until="domcontentloaded")

        if args.mobile:
            page.get_by_label(re.compile("sessions", re.I)).first.click()
        toggle = page.get_by_test_id("t3-toggle")
        toggle.wait_for(timeout=15000)
        if toggle.get_attribute("aria-expanded") != "true":
            toggle.click()
        page.get_by_test_id("t3-thread").first.wait_for(timeout=20000)
        n = page.get_by_test_id("t3-thread").count()
        log(f"picker lists {n} T3 threads; statuses:", sorted({e.get_attribute("data-status") for e in page.get_by_test_id("t3-thread").all()}))
        page.screenshot(path=str(shots / "t3-picker.png"))

        row = page.get_by_test_id("t3-thread").filter(has_text=args.thread).first
        row.click()
        deadline = time.time() + 20
        while time.time() < deadline and "SCRATCH-READY" not in _page_text(page) and not page.get_by_test_id("transcript-message").count():
            time.sleep(0.5)
        time.sleep(1)
        log("context shown:", page.get_by_test_id("transcript-message").count(), "message(s)")
        if not page.get_by_test_id("transcript-message").count():
            failures.append("attached thread's recent messages not shown")
        page.screenshot(path=str(shots / "t3-attached.png"))
        if args.mobile:
            page.keyboard.press("Escape")

        page.get_by_role("button", name=re.compile("^(Start call|Converse)$")).first.click()
        box = page.get_by_placeholder("Type", exact=False)
        deadline = time.time() + 30
        while time.time() < deadline and not box.is_enabled():
            time.sleep(0.5)
        if not box.is_enabled():
            browser.close()
            raise SystemExit("call never connected")
        time.sleep(2)
        before = page.get_by_test_id("transcript-message").count()
        box.fill(args.say)
        box.press("Enter")
        log("typed:", args.say)

        t0, saw_cue = time.time(), False
        reply = ""
        while time.time() - t0 < args.timeout:
            time.sleep(0.5)
            txt = _page_text(page)
            if "working" in txt.lower() and not saw_cue:
                saw_cue = True
                log(f"working cue visible after {time.time() - t0:.1f}s")
                page.screenshot(path=str(shots / "t3-working.png"))
            msgs = page.evaluate(
                """() => Array.from(document.querySelectorAll('[data-testid="transcript-message"]')).map(e => e.innerText.replace(/\\s+/g,' ').trim())"""
            )
            if len(msgs) >= before + 2 and all(e.lower() in msgs[-1].lower() for e in args.expect):
                reply = msgs[-1]
                break
            reply = msgs[-1] if msgs else ""
        log(f"reply after {time.time() - t0:.1f}s:", reply[:200])
        page.screenshot(path=str(shots / "t3-reply.png"))
        if not saw_cue:
            failures.append("no working cue seen")
        missing = [e for e in args.expect if e.lower() not in reply.lower()]
        browser.close()

    print("RESULT " + json.dumps({"reply": reply[:300], "missing": missing, "failures": failures}))
    return 1 if (missing or failures) else 0


if __name__ == "__main__":
    raise SystemExit(main())

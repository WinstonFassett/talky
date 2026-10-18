"""Text-chat rig: drive a real call by typing, not talking (WIN-534).

Same isolated headless Chromium as ``drive_call.py``, but the fake mic is
silence, and each ``--say`` message is typed into the client's text box
(``client.sendText`` → ``UserTurnTextFrame``). Waits for the transcript to stop
changing after each message, prints it, and checks ``--expect`` substrings
against the last reply. Useful for backends where STT noise would muddy an
assertion, e.g. "does the resumed backend remember the code word".

    python3 tests/browser/drive_text.py --say "my code word is pineapple"
    python3 tests/browser/drive_text.py --say "what was my code word?" --expect pineapple

Unlike ``drive_call.py`` it does not switch the LLM profile unless
``--profile`` is given, so it can drive whatever ``talky resume`` set up.
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
    print("[text-rig]", *a, flush=True)


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


def _switch(path: str, profile: str) -> None:
    req = urllib.request.Request(
        CLIENT_URL.rstrip("/") + path,
        data=json.dumps({"profile": profile}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        log(f"switch {path} -> {profile}: {r.read().decode()[:80]}")


def _transcript(page) -> list[str]:  # noqa: ANN001
    return page.evaluate(
        """() => Array.from(document.querySelectorAll('[data-testid="transcript-message"]'))
                 .map(el => el.innerText.replace(/\\s+/g, ' ').trim())"""
    )


def _wait_settled(page, before: int, timeout: float, quiet: float) -> list[str]:  # noqa: ANN001
    """Wait until a reply appeared after the typed message and stopped growing."""
    deadline = time.time() + timeout
    last, last_change = _transcript(page), time.time()
    while time.time() < deadline:
        time.sleep(0.5)
        cur = _transcript(page)
        if cur != last:
            last, last_change = cur, time.time()
        if len(cur) >= before + 2 and time.time() - last_change >= quiet:
            break
    return last


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--say", action="append", required=True, help="message to type (repeatable)")
    ap.add_argument("--expect", action="append", default=[], help="substring the last reply must contain")
    ap.add_argument("--profile", help="switch LLM profile first (default: leave as is)")
    ap.add_argument("--voice", default=os.environ.get("TALKY_VOICE_PROFILE", "local-test"))
    ap.add_argument("--quiet", type=float, default=4.0, help="seconds of no transcript change that count as settled (raise for slow cold backends)")
    ap.add_argument("--timeout", type=float, default=90.0, help="seconds to wait per reply")
    args = ap.parse_args()

    if args.profile:
        _switch("/api/profiles/switch", args.profile)
    _switch("/api/voices/switch", args.voice)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--use-fake-device-for-media-stream",
                "--use-fake-ui-for-media-stream",
                f"--use-file-for-fake-audio-capture={_silence_wav()}",
            ],
        )
        page = browser.new_context(permissions=["microphone"]).new_page()
        page.on("pageerror", lambda e: log("pageerror:", str(e)))
        page.goto(CLIENT_URL, wait_until="domcontentloaded")
        page.get_by_role("button", name=re.compile("^(Start call|Converse)$")).first.click()  # label is "Converse" once a convo exists

        box = page.get_by_placeholder("Type", exact=False)
        deadline = time.time() + 30
        while time.time() < deadline and not box.is_enabled():
            time.sleep(0.5)
        if not box.is_enabled():
            browser.close()
            raise SystemExit("call never connected (text box stayed disabled)")
        time.sleep(3)  # let a profile announcement land before typing
        log("connected")

        transcript: list[str] = _transcript(page)
        for msg in args.say:
            before = len(transcript)
            box.fill(msg)
            box.press("Enter")
            log("typed:", msg)
            transcript = _wait_settled(page, before, args.timeout, args.quiet)

        browser.close()  # disconnect → pipeline teardown → assistant sink flush

    for line in transcript:
        log("  |", line[:300])
    last = transcript[-1] if transcript else ""
    missing = [e for e in args.expect if e.lower() not in last.lower()]
    print("RESULT " + json.dumps({"messages": len(transcript), "last": last[:500], "missing": missing}))
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())

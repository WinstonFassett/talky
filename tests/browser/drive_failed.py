"""Failed-dot rig: a failing turn on a backend shows the red sidebar dot (WIN-549).

Same isolated headless Chromium + silent fake mic as ``drive_text.py``. For each
``--profile``: switch to it, start a fresh session, start a call, type a message,
then wait for the current session's ``session-status`` dot to read ``failed``
(screenshot ``failed-<profile>.png`` in ``$TALKY_TEST_OUT``). Point it at a daemon
whose backend is broken on purpose (bad model, expired key, dead subprocess).

    python3 tests/browser/drive_failed.py --profile claude --profile hermes --profile opencode
"""

from __future__ import annotations

import argparse
import os
import time

from drive_sessions import OUT_DIR, CLIENT_URL, _api, _wait
from drive_text import _silence_wav, _switch, log
from playwright.sync_api import sync_playwright


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", action="append", required=True)
    ap.add_argument("--voice", default=os.environ.get("TALKY_VOICE_PROFILE", "local-test"))
    ap.add_argument("--timeout", type=float, default=90.0)
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _switch("/api/voices/switch", args.voice)
    results: dict[str, str] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=[
            "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
            f"--use-file-for-fake-audio-capture={_silence_wav()}",
        ])
        for profile in args.profile:
            _switch("/api/profiles/switch", profile)
            _api("/api/convos/new", "POST", {})
            ctx = browser.new_context(permissions=["microphone"], viewport={"width": 1280, "height": 800})
            ctx.add_init_script("try { localStorage.removeItem('talky.openConvo') } catch {}")
            page = ctx.new_page()
            page.on("pageerror", lambda e: log("pageerror:", str(e)))
            page.goto(CLIENT_URL, wait_until="domcontentloaded")
            page.get_by_role("button", name="Start call").first.click()
            box = page.get_by_placeholder("Type", exact=False)
            _wait(box.is_enabled, 30, "call to connect")
            time.sleep(3)
            box.fill("say hello")
            box.press("Enter")
            log(f"{profile}: typed, waiting for the failed dot")
            dot = page.locator('[data-testid="session-row"][data-open] [data-testid="session-status"]')
            deadline = time.time() + args.timeout
            status = ""
            while time.time() < deadline:
                status = dot.first.get_attribute("data-status") if dot.count() else ""
                if status == "failed":
                    break
                time.sleep(0.5)
            time.sleep(0.5)
            page.screenshot(path=str(OUT_DIR / f"failed-{profile}.png"))
            results[profile] = status or "none"
            log(f"{profile}: dot={results[profile]}")
            ctx.close()
        browser.close()
    print("RESULT", results)
    return 0 if all(v == "failed" for v in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())

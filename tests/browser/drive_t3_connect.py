"""T3 auto-connect rig (WIN-575). Needs `rig_daemon.sh start --port P --fake-t3` (fresh run → no token).

    python3 tests/browser/drive_t3_connect.py [--port 18781] [--mobile]

1. no token file → open sessions, expand T3 → threads appear, no "login" text, token file now mode 600
2. fail-cli on → Disconnect → card + Connect; Connect fails → error text + Retry
3. fail-cli off → Retry → threads again
4. stop fake T3 → "T3 Code isn't running"; the convo list still renders
Exit 0 and no pageerror = pass. Screenshots s0-01..s0-04 (+ `-mobile`) go to --shots.
"""

from __future__ import annotations

import argparse
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
RUN = Path(os.environ.get("TALKY_RIG_DIR") or ROOT / ".rig")
RIG = ROOT / "tests/browser/rig_daemon.sh"


def log(*a: object) -> None:
    print("[t3-connect]", *a, flush=True)


def rig(*args: str) -> None:
    subprocess.run([str(RIG), *args], check=True, env={**os.environ, "TALKY_RIG_DIR": str(RUN)})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("RIG_PORT", 18781)))
    ap.add_argument("--mobile", action="store_true")
    ap.add_argument("--shots", default=str(ROOT / "screenshots" / "win-575"))
    a = ap.parse_args()
    if a.port == 8765:
        raise SystemExit("refusing port 8765")
    shots = Path(a.shots)
    shots.mkdir(parents=True, exist_ok=True)
    sfx = "-mobile" if a.mobile else ""
    token_file, cli_log = RUN / "t3.json", RUN / "cli.log"
    errors: list[str] = []
    fails: list[str] = []

    def check(ok: bool, msg: str) -> None:
        log(("ok  " if ok else "FAIL"), msg)
        if not ok:
            fails.append(msg)

    rig("fail-cli", "off")
    check(not token_file.exists(), "token file absent before the first open")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 390, "height": 844} if a.mobile else {"width": 1280, "height": 800})
        page = ctx.new_page()
        page.on("pageerror", lambda e: (errors.append(str(e)), log("pageerror:", e)))
        page.goto(f"http://127.0.0.1:{a.port}/", wait_until="domcontentloaded")

        def open_sessions() -> None:
            if a.mobile and not page.get_by_test_id("t3-toggle").is_visible():
                page.get_by_label(re.compile("sessions", re.I)).first.click()
            page.get_by_test_id("t3-toggle").wait_for(timeout=20000)

        open_sessions()
        toggle = page.get_by_test_id("t3-toggle")
        if toggle.get_attribute("aria-expanded") != "true":
            toggle.click()

        # 1. auto-connect
        page.get_by_test_id("t3-thread").first.wait_for(timeout=20000)
        check(page.get_by_test_id("t3-thread").count() >= 1, "threads listed with no manual login")
        check("login" not in page.evaluate("() => document.body.innerText").lower(), "no 'login' text on the page")
        check(token_file.exists() and stat.S_IMODE(token_file.stat().st_mode) == 0o600, "token file issued, mode 600")
        page.screenshot(path=str(shots / f"s0-01-autoconnect{sfx}.png"))

        # 2. disconnect, then a failing connect
        rig("fail-cli", "on")
        page.get_by_test_id("t3-menu").click()
        page.get_by_test_id("t3-disconnect").click()
        page.get_by_test_id("connection-connect").wait_for(timeout=10000)
        check(not token_file.exists(), "token file removed on disconnect")
        check(any(line.startswith("revoke ") for line in cli_log.read_text().splitlines()), "CLI log shows a revoke")
        page.screenshot(path=str(shots / f"s0-02-disconnected{sfx}.png"))
        page.get_by_test_id("connection-connect").click()
        page.get_by_test_id("connection-retry").wait_for(timeout=10000)
        detail = page.get_by_test_id("connection-detail").inner_text()
        check("failed" in detail, f"failure detail shown ({detail!r})")
        page.screenshot(path=str(shots / f"s0-03-failure{sfx}.png"))

        # 3. recovery
        rig("fail-cli", "off")
        page.get_by_test_id("connection-retry").click()
        page.get_by_test_id("t3-thread").first.wait_for(timeout=20000)
        check(page.get_by_test_id("connection-card").count() == 0, "card gone after retry")
        page.screenshot(path=str(shots / f"s0-04-recovered{sfx}.png"))

        # 4. T3 down: the section degrades, the rest of the app does not
        rig("stop-fake-t3")
        page.get_by_test_id("t3-refresh").click()
        page.get_by_test_id("connection-card").wait_for(timeout=15000)
        check("isn't running" in page.get_by_test_id("connection-detail").inner_text(), "t3_down text shown")
        check(page.get_by_test_id("t3-toggle").is_visible(), "sidebar still renders with T3 down")
        browser.close()

    check(not errors, "no pageerror")
    if fails:
        log("FAILED:", fails)
        return 1
    log("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

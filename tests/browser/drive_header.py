"""Header chips rig (WIN-578/579). Needs `rig_daemon.sh start --port P --fake-t3 --wait-ready`.

    python3 tests/browser/drive_header.py [--port 18782] [--mobile] [--shots DIR] [--before]

--before: only screenshot the header (baseline build, no chips expected).
Otherwise: ONE profile chip (no backend/model/voice chips) opening a sheet (phone) / popover (desktop) with
backend + voice visible, profile switch and back; link chip on a linked chat not clipped; footer has no backend picker. Fake T3 only; never port 8765. Exit 0 and no pageerror = pass.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]


def log(*a: object) -> None:
    print("[header]", *a, flush=True)


def api(base: str, path: str, body: dict | None = None):
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"}, method="POST" if body is not None else "GET",
    )
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("RIG_PORT", 18782)))
    ap.add_argument("--mobile", action="store_true")
    ap.add_argument("--before", action="store_true")
    ap.add_argument("--shots", default="/tmp/rt2/drive-header")
    a = ap.parse_args()
    if a.port == 8765:
        raise SystemExit("refusing port 8765")
    base = f"http://127.0.0.1:{a.port}"
    shots = Path(a.shots)
    shots.mkdir(parents=True, exist_ok=True)
    sfx = "-phone" if a.mobile else "-desktop"
    errors: list[str] = []
    fails: list[str] = []

    def check(ok: bool, msg: str) -> None:
        log("ok  " if ok else "FAIL", msg)
        if not ok:
            fails.append(msg)

    linked_id = None
    if not a.before:
        threads = api(base, "/api/t3/threads?project=project:alpha&limit=100")["threads"]
        linked_id = api(base, "/api/t3/attach", {"thread_id": threads[3]["id"]})["convo_id"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 390, "height": 844} if a.mobile else {"width": 1280, "height": 800})
        page = ctx.new_page()
        page.on("pageerror", lambda e: (errors.append(str(e)), log("pageerror:", e)))
        page.goto(f"{base}/" + (f"?session={linked_id}" if linked_id else ""), wait_until="domcontentloaded")
        page.get_by_test_id("app-header").or_(page.locator("header").first).first.wait_for(timeout=30000)
        page.wait_for_timeout(2500)  # profiles + voices load

        def shot(name: str) -> None:
            page.screenshot(path=str(shots / f"{name}{sfx}.png"))

        if a.before:
            shot("header-before")
            browser.close()
            return 0

        shot("header-after")
        check(page.get_by_test_id("chat-title").is_visible(), "chat title visible")
        check(page.get_by_test_id("chip-profile").is_visible(), "profile chip visible")
        for gone in ("chip-backend", "chip-model", "chip-voice"):
            check(page.get_by_test_id(gone).count() == 0, f"no separate {gone}")
        check(page.get_by_test_id("chip-link").count() == 1, "link chip on the linked chat")
        lb = page.get_by_test_id("chip-link").bounding_box()
        vw = 390 if a.mobile else 1280
        check(bool(lb) and lb["x"] + lb["width"] <= vw, "link chip not clipped")

        chip = page.get_by_test_id("chip-profile")
        before = page.get_by_test_id("chip-profile-label").inner_text()
        chip.click()
        panel = page.get_by_test_id("chip-sheet" if a.mobile else "chip-popover")
        panel.wait_for(timeout=10000)
        page.wait_for_timeout(600)
        check(page.get_by_test_id("profile-summary").first.is_visible(), "backend shown in the profile panel")
        check(page.get_by_test_id("profile-voice").is_visible(), "voice shown in the profile panel")
        shot("profile-open")
        other = page.locator('[data-testid=profile-option]:not([aria-selected=true]):not([disabled])').first
        target = other.get_attribute("data-profile")
        other.click()
        page.wait_for_function(
            "(l) => document.querySelector('[data-testid=chip-profile-label]')?.innerText !== l", arg=before, timeout=15000)
        check(True, f"switched profile {before} -> {target}")
        chip.click()
        panel.wait_for(timeout=10000)
        page.locator(f'[data-testid=profile-option][data-profile="{before}"]').click()
        page.wait_for_function(
            "(l) => document.querySelector('[data-testid=chip-profile-label]')?.innerText === l", arg=before, timeout=15000)
        check(True, "switched back")

        check(page.locator("footer .picker-footer", has_text="by ").count() == 0, "footer has no backend picker")
        if a.mobile:
            check(page.locator("footer").get_by_text(before, exact=True).count() == 0, "phone footer has no profile label")
        browser.close()

    check(not errors, "no pageerror")
    if fails:
        log("FAILED:", fails)
        return 1
    log("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

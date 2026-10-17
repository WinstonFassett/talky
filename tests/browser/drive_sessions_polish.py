"""Sessions polish rig (WIN-535 slice C): paging, rename, pin, archive, delete, live list, mobile.

Runs against a daemon whose store already holds these sessions (titles are
substrings): ``--long`` (>100 turns), ``--rename``, ``--archive``, ``--delete``.
Screenshots go to ``$TALKY_TEST_OUT`` as ``p*.png``. Destructive: it renames,
archives (then restores) and deletes sessions in that store.
"""

from __future__ import annotations

import argparse
import json
import os
import time

from drive_sessions import OUT_DIR, _api, _rows, _wait
from drive_text import _silence_wav, _switch, _transcript, _wait_settled
from playwright.sync_api import sync_playwright

CLIENT_URL = os.environ.get("TALKY_URL", "http://127.0.0.1:8765/")


def log(*a: object) -> None:
    print("[polish-rig]", *a, flush=True)


def _row(page, title: str):  # noqa: ANN001
    return page.locator('[data-testid="session-row"]', has_text=title).first


def _action(page, title: str, action: str) -> None:  # noqa: ANN001
    row = _row(page, title)
    row.hover()
    row.get_by_test_id("session-actions").click()
    page.get_by_test_id(f"session-action-{action}").click()


def _groups(page) -> dict[str, list[str]]:  # noqa: ANN001
    return page.evaluate(
        """() => Object.fromEntries(Array.from(document.querySelectorAll('[data-testid="session-group"]')).map(g =>
             [g.dataset.group, Array.from(g.querySelectorAll('[data-testid="session-title"]')).map(t => t.innerText)]))"""
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--long", required=True)
    ap.add_argument("--rename", required=True)
    ap.add_argument("--archive", required=True)
    ap.add_argument("--delete", required=True)
    ap.add_argument("--profile", default="echo", help="profile for the live-list call")
    ap.add_argument("--voice", default=os.environ.get("TALKY_VOICE_PROFILE", "local-test"))
    args = ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    shot = lambda page, name: page.screenshot(path=str(OUT_DIR / name))  # noqa: E731
    results: dict[str, object] = {}
    _switch("/api/voices/switch", args.voice)
    _api("/api/convos/new", "POST", {})

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--use-fake-device-for-media-stream",
                "--use-fake-ui-for-media-stream",
                f"--use-file-for-fake-audio-capture={_silence_wav()}",
            ],
        )
        ctx = browser.new_context(permissions=["microphone"], viewport={"width": 1280, "height": 800})
        ctx.add_init_script("try { localStorage.removeItem('talky.openConvo') } catch {}")
        page = ctx.new_page()
        page.on("pageerror", lambda e: log("pageerror:", str(e)))
        page.goto(CLIENT_URL, wait_until="domcontentloaded")
        _wait(lambda: len(_rows(page)) > 0, 15, "session rows")

        # ── paging: older turns load at the top without moving what's on screen
        _row(page, args.long).click()
        _wait(lambda: len(_transcript(page)) >= 100, 10, "first page")
        total = _api(f"/api/convos/{_row(page, args.long).get_attribute('data-convo-id')}?limit=1")["total"]
        jumps = []
        while page.get_by_test_id("transcript-load-older").count():
            n = len(_transcript(page))
            anchor_id = page.locator('[data-testid="transcript-message"]').first.get_attribute("data-message-id")
            anchor = page.locator(f'[data-message-id="{anchor_id}"]')
            lst = page.get_by_test_id("transcript-list")
            # Park the top page out of the auto-load zone, note where the anchor is,
            # then scroll to the top: with no jump it ends up exactly scrollTop px lower.
            lst.evaluate("el => { el.scrollTop = Math.min(el.scrollHeight, 600) }")
            time.sleep(0.2)
            top = lst.evaluate("el => el.scrollTop")
            y0 = anchor.bounding_box()["y"]
            lst.evaluate("el => { el.scrollTop = 0 }")
            _wait(lambda: len(_transcript(page)) > n, 10, "older page")
            time.sleep(0.3)
            moved = anchor.bounding_box()["y"] - (y0 + top)
            jumps.append(round(moved, 1))
            log(f"paged: {n} -> {len(_transcript(page))} messages; anchor off by {moved:+.1f}px")
        shown = len(_transcript(page))
        results["paging"] = {"total": total, "shown": shown, "anchor_moves_px": jumps}
        assert shown == total, (shown, total)
        assert all(abs(j) <= 4 for j in jumps), f"older history jumped: {jumps}"
        shot(page, "p1-paged-to-top.png")

        # ── rename
        _action(page, args.rename, "rename")
        box = page.get_by_test_id("session-rename-input")
        box.fill("Lake trip packing")
        box.press("Enter")
        _wait(lambda: "Lake trip packing" in _rows(page), 5, "rename in list")
        rid = _row(page, "Lake trip packing").get_attribute("data-convo-id")
        assert _api(f"/api/convos/{rid}?limit=1")["convo"]["title"] == "Lake trip packing"
        results["rename"] = "ok"

        # ── pin
        _action(page, "Lake trip packing", "pin")
        _wait(lambda: _groups(page).get("Pinned") == ["Lake trip packing"], 5, "pinned group")
        results["pin"] = _groups(page).get("Pinned")
        time.sleep(0.4)
        shot(page, "p2-renamed-pinned.png")

        # ── archive + restore
        _action(page, args.archive, "archive")
        _wait(lambda: not any(args.archive in t for t in _rows(page)), 5, "archived row to leave the list")
        page.get_by_test_id("session-show-archived").click()
        _wait(lambda: any(args.archive in t for t in _rows(page)), 5, "archived view")
        shot(page, "p3-archived-view.png")
        _action(page, args.archive, "archive")  # "Unarchive" in this view
        _wait(lambda: not any(args.archive in t for t in _rows(page)), 5, "unarchived row to leave archived view")
        page.get_by_test_id("session-show-archived").click()
        _wait(lambda: any(args.archive in t for t in _rows(page)), 5, "unarchived row back in the list")
        results["archive"] = "ok"

        # ── delete with confirmation (cancel first, then confirm)
        did = _row(page, args.delete).get_attribute("data-convo-id")
        _action(page, args.delete, "delete")
        page.get_by_test_id("session-delete-dialog").wait_for()
        time.sleep(0.6)  # let the popover/dialog animations finish
        shot(page, "p4-delete-confirm.png")
        page.get_by_role("button", name="Cancel").click()
        assert any(args.delete in t for t in _rows(page)), "cancel deleted it"
        _action(page, args.delete, "delete")
        page.get_by_test_id("session-delete-confirm").click()
        _wait(lambda: not any(args.delete in t for t in _rows(page)), 5, "deleted row to leave")
        try:
            _api(f"/api/convos/{did}")
            raise AssertionError("deleted convo still served")
        except Exception as e:  # noqa: BLE001
            assert "404" in str(e), e
        results["delete"] = "ok"

        # ── live list: a new chat shows up as soon as its first turn lands
        page.get_by_test_id("session-new").click()
        _wait(lambda: page.locator('[data-testid="session-row"][data-open]').count() == 0, 5, "new chat to clear selection")
        before_rows = len(_rows(page))
        if args.profile:
            _api("/api/profiles/switch", "POST", {"profile": args.profile})
        start = page.get_by_role("button", name="Start call")
        if start.count() == 0:
            start = page.get_by_role("button", name="Converse")
        start.first.click()
        tbox = page.get_by_placeholder("Type", exact=False)
        _wait(tbox.is_enabled, 30, "call to connect")
        time.sleep(2)
        n = len(_transcript(page))
        tbox.fill("brand new session about kites")
        tbox.press("Enter")
        _wait(lambda: "brand new session about kites" in _rows(page), 10, "new session row (live)")
        statuses = page.locator('[data-testid="session-row"][data-open] [data-testid="session-status"]').evaluate_all(
            "els => els.map(e => e.dataset.status)")
        _wait_settled(page, n, 30)
        idle = page.locator('[data-testid="session-row"][data-open] [data-testid="session-status"]').get_attribute("data-status")
        results["live_list"] = {"rows_before": before_rows, "rows_after": len(_rows(page)),
                                "status_on_send": statuses, "status_after_reply": idle}
        assert len(_rows(page)) == before_rows + 1
        assert idle == "idle", idle
        shot(page, "p5-live-new-session.png")
        page.get_by_role("button", name="End", exact=False).first.click()
        ctx.close()

        # ── mobile sheet
        mctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        m = mctx.new_page()
        m.goto(CLIENT_URL, wait_until="domcontentloaded")
        m.get_by_test_id("sessions-toggle").first.click()
        m.get_by_test_id("session-drawer").wait_for()
        _wait(lambda: m.locator('[data-testid="session-drawer"] [data-testid="session-row"]').count() > 0, 10, "rows in sheet")
        time.sleep(0.6)
        m.screenshot(path=str(OUT_DIR / "p6-mobile-sheet.png"))
        m.locator('[data-testid="session-drawer"] [data-testid="session-row"]', has_text="falcon").first.click()
        _wait(lambda: m.get_by_test_id("session-drawer").count() == 0, 5, "sheet to close after picking")
        _wait(lambda: len(_transcript(m)) >= 2, 10, "transcript on mobile")
        time.sleep(0.4)
        m.screenshot(path=str(OUT_DIR / "p7-mobile-opened.png"))
        results["mobile"] = {"messages": len(_transcript(m))}
        mctx.close()
        browser.close()

    print("RESULT " + json.dumps(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

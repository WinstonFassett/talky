"""Sessions rig: browse, search, open an old session and continue it (WIN-535).

Same isolated headless Chromium + silent fake mic as ``drive_text.py``. Steps,
each asserted and screenshotted into ``$TALKY_TEST_OUT``:

1. the sidebar lists saved sessions (``01-sidebar.png``);
2. ``--search`` filters by transcript text (``02-search.png``);
3. clicking the session titled ``--open`` shows its saved turns with no call
   running, and binds it on the daemon (``03-opened.png``);
4. starting a call and typing ``--say`` continues it: the last reply must
   contain every ``--expect`` (``04-reply.png``);
5. a reload reopens the same session with its turns (``05-reload.png``).

``--video`` records the whole run as a .webm in the same folder.

    python3 tests/browser/drive_sessions.py --open "my code word is falcon" \\
        --search teal --say "what did I tell you?" --expect teal --video
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request
from pathlib import Path

from drive_text import _silence_wav, _switch, _transcript, _wait_settled
from playwright.sync_api import sync_playwright

CLIENT_URL = os.environ.get("TALKY_URL", "http://127.0.0.1:8765/")
OUT_DIR = Path(os.environ.get("TALKY_TEST_OUT", "/tmp/talky-test-out"))


def log(*a: object) -> None:
    print("[sessions-rig]", *a, flush=True)


def _api(path: str, method: str = "GET", body: dict | None = None) -> dict:
    req = urllib.request.Request(
        CLIENT_URL.rstrip("/") + path,
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode() or "{}")


def _wait(cond, timeout: float, what: str) -> None:  # noqa: ANN001
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return
        time.sleep(0.25)
    raise SystemExit(f"timed out waiting for {what}")


def _rows(page) -> list[str]:  # noqa: ANN001
    return page.locator('[data-testid="session-row"] [data-testid="session-title"]').all_inner_texts()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--open", required=True, help="title (substring) of the session to open")
    ap.add_argument("--search", help="search text that must find that session")
    ap.add_argument("--say", action="append", default=[], help="message to type after starting a call")
    ap.add_argument("--expect", action="append", default=[], help="substring the last reply must contain")
    ap.add_argument("--voice", default=os.environ.get("TALKY_VOICE_PROFILE", "local-test"))
    ap.add_argument("--timeout", type=float, default=90.0)
    ap.add_argument("--video", action="store_true")
    ap.add_argument("--prefix", default="", help="screenshot filename prefix")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    shot = lambda page, name: page.screenshot(path=str(OUT_DIR / f"{args.prefix}{name}"))  # noqa: E731
    _switch("/api/voices/switch", args.voice)
    _api("/api/convos/new", "POST", {})  # start from a blank screen

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--use-fake-device-for-media-stream",
                "--use-fake-ui-for-media-stream",
                f"--use-file-for-fake-audio-capture={_silence_wav()}",
            ],
        )
        ctx = browser.new_context(
            permissions=["microphone"],
            viewport={"width": 1280, "height": 800},
            record_video_dir=str(OUT_DIR) if args.video else None,
            record_video_size={"width": 1280, "height": 800},
        )
        ctx.add_init_script("try { localStorage.removeItem('talky.openConvo') } catch {}")
        page = ctx.new_page()
        page.on("pageerror", lambda e: log("pageerror:", str(e)))
        page.goto(CLIENT_URL, wait_until="domcontentloaded")

        # 1. sidebar
        _wait(lambda: len(_rows(page)) > 0, 15, "session rows")
        titles = _rows(page)
        log(f"sidebar: {len(titles)} sessions; groups:",
            page.locator('[data-testid="session-group"]').evaluate_all("els => els.map(e => e.dataset.group)"))
        assert any(args.open.lower() in t.lower() for t in titles), f"{args.open!r} not in sidebar {titles}"
        time.sleep(0.5)
        shot(page, "01-sidebar.png")

        # 2. search
        if args.search:
            page.get_by_test_id("session-search").fill(args.search)
            _wait(lambda: len(_rows(page)) >= 1 and all(args.open.lower() in t.lower() for t in _rows(page)),
                  10, f"search {args.search!r} to narrow to {args.open!r}")
            snippet = page.get_by_test_id("session-snippet").first.inner_text()
            log(f"search {args.search!r}: {_rows(page)} snippet={snippet!r}")
            shot(page, "02-search.png")
            page.get_by_test_id("session-search").fill("")
            _wait(lambda: len(_rows(page)) == len(titles), 10, "search cleared")

        # 3. open it — saved turns appear, no call is running
        row = page.locator('[data-testid="session-row"]', has_text=args.open).first
        convo_id = row.get_attribute("data-convo-id")
        saved = _api(f"/api/convos/{convo_id}?limit=1000")["turns"]
        row.click()
        _wait(lambda: len(_transcript(page)) >= min(len(saved), 100), 10, "saved turns to render")
        shown = _transcript(page)
        log(f"opened {convo_id}: {len(shown)} messages shown (saved: {len(saved)})")
        assert saved[-1]["content"][:40].lower() in shown[-1].lower(), (saved[-1], shown[-1])
        assert page.get_by_placeholder("Connect to start", exact=False).count() == 1, "a call is running"
        _wait(lambda: (_api("/api/convos").get("current") or {}).get("id") == convo_id, 10, "daemon to bind it")
        time.sleep(0.5)
        shot(page, "03-opened.png")

        # 4. continue it
        transcript = shown
        if args.say:
            start = page.get_by_role("button", name="Converse")
            if start.count() == 0:
                start = page.get_by_role("button", name="Start call")
            start.first.click()
            box = page.get_by_placeholder("Type", exact=False)
            _wait(box.is_enabled, 30, "call to connect")
            time.sleep(3)  # let a profile announcement land
            transcript = _transcript(page)
            log(f"connected; {len(transcript)} messages on screen")
            for msg in args.say:
                before = len(transcript)
                box.fill(msg)
                box.press("Enter")
                log("typed:", msg)
                transcript = _wait_settled(page, before, args.timeout)
            shot(page, "04-reply.png")

        # 5. reload keeps it
        page.reload(wait_until="domcontentloaded")
        _wait(lambda: page.locator(f'[data-testid="session-row"][data-convo-id="{convo_id}"][data-open]').count() == 1,
              15, "the same session to be open after reload")
        _wait(lambda: len(_transcript(page)) >= 2, 15, "transcript after reload")
        after = _transcript(page)
        log(f"after reload: {len(after)} messages")
        time.sleep(0.5)
        shot(page, "05-reload.png")

        video = page.video.path() if args.video and page.video else None
        ctx.close()
        browser.close()

    for line in transcript[-6:]:
        log("  |", line[:300])
    last = transcript[-1] if transcript else ""
    missing = [e for e in args.expect if e.lower() not in last.lower()]
    print("RESULT " + json.dumps({"convo": convo_id, "last": last[:300], "missing": missing,
                                  "after_reload": len(after), "video": str(video) if video else None}))
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())

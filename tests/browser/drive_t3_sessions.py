"""Chats | In tools sidebar rig (WIN-577 S1b). Needs `rig_daemon.sh start --port P --fake-t3`.

    tests/browser/rig_daemon.sh start --port 18782 --fake-t3
    python3 tests/browser/drive_t3_sessions.py [--port 18782] [--mobile] [--shots DIR]

1. Chats tab: "Chats" heading, seeded linked chat listed.
2. In tools tab: source picker (T3 live, Claude/Hermes disabled), legend, one flat list (no group headers),
   pinned threads first, project tag + marker per row, chips, 20 rows + "Show more".
3. Chip filter narrows the list; "Show more" pages; legend dismisses and stays dismissed.
4. Linked row (`in a chat ↔`) opens its existing Chat: convo count unchanged.
5. Unlinked row (`chat ›`) attaches: convo count +1, row becomes linked.
6. T3 down → connection card + Retry; fake with zero threads → empty-state explainer.
Fake T3 only; never port 8765, never a real T3. Exit 0 and no pageerror = pass.
Phone (--mobile, 390px): tabs sit at the bottom of the drawer; desktop: top of the rail.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
RUN = Path(os.environ.get("TALKY_RIG_DIR") or ROOT / ".rig")
RIG = ROOT / "tests/browser/rig_daemon.sh"


def log(*a: object) -> None:
    print("[t3-sessions]", *a, flush=True)


def rig(*args: str) -> None:
    subprocess.run([str(RIG), *args], check=True, env={**os.environ, "TALKY_RIG_DIR": str(RUN)})


def api(base: str, path: str, body: dict | None = None):
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"}, method="POST" if body is not None else "GET",
    )
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def convo_count(base: str) -> int:
    return len(api(base, "/api/convos?archived=0")["convos"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("RIG_PORT", 18782)))
    ap.add_argument("--mobile", action="store_true")
    ap.add_argument("--shots", default=str(ROOT / "screenshots" / "win-577"))
    a = ap.parse_args()
    if a.port == 8765:
        raise SystemExit("refusing port 8765")
    base = f"http://127.0.0.1:{a.port}"
    shots = Path(a.shots)
    shots.mkdir(parents=True, exist_ok=True)
    sfx = "-phone" if a.mobile else ""
    errors: list[str] = []
    fails: list[str] = []

    def check(ok: bool, msg: str) -> None:
        log("ok  " if ok else "FAIL", msg)
        if not ok:
            fails.append(msg)

    rig("fail-cli", "off")
    # Seed: one alpha thread already linked to a Chat (so "in a chat ↔" and the Chats tab have something).
    alpha = api(base, "/api/t3/threads?project=project:alpha&limit=100")["threads"]
    seeded = alpha[10]
    seed = api(base, "/api/t3/attach", {"thread_id": seeded["id"]})
    n0 = convo_count(base)
    log(f"seeded linked thread {seeded['title']!r} → convo {seed['convo_id'][:8]}; {n0} convo(s)")

    empty_fake = None
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 390, "height": 844} if a.mobile else {"width": 1280, "height": 800})
        page = ctx.new_page()
        page.on("pageerror", lambda e: (errors.append(str(e)), log("pageerror:", e)))
        page.goto(f"{base}/", wait_until="domcontentloaded")

        def open_panel() -> None:
            if a.mobile and not page.get_by_test_id("sidebar-tabs").is_visible():
                page.get_by_label(re.compile("show sessions", re.I)).first.click()
            page.get_by_test_id("sidebar-tabs").wait_for(timeout=20000)

        def settle() -> None:  # phone: a pick closes the drawer; wait out the animation before reopening
            if a.mobile:
                page.get_by_test_id("session-drawer").wait_for(state="detached", timeout=10000)

        def tools() -> None:
            open_panel()
            page.get_by_test_id("sidebar-tab-tools").click()

        def shot(name: str) -> None:
            page.screenshot(path=str(shots / f"{name}{sfx}.png"))

        # 1. Chats tab
        open_panel()
        placement = page.get_by_test_id("sidebar-tabs").get_attribute("data-placement")
        check(placement == ("bottom" if a.mobile else "top"), f"tabs placement {placement}")
        page.get_by_test_id("sidebar-tab-chats").click()
        check("Chats" in page.get_by_test_id("session-sidebar").inner_text(), "Chats heading")
        page.get_by_test_id("session-row").first.wait_for(timeout=15000)
        check(page.get_by_test_id("session-row").count() >= 1, "seeded Chat listed")
        shot("s1b-01-chats")

        # 2. In tools tab
        page.get_by_test_id("sidebar-tab-tools").click()
        page.get_by_test_id("t3-thread").first.wait_for(timeout=20000)
        check(page.get_by_test_id("intools-source-t3").is_enabled(), "T3 source live")
        check(page.get_by_test_id("intools-source-claude").is_disabled() and page.get_by_test_id("intools-source-hermes").is_disabled(), "Claude/Hermes disabled")
        check(page.get_by_test_id("intools-source-claude").get_attribute("title") == "coming soon", "disabled sources say 'coming soon'")
        check(page.get_by_test_id("intools-legend").is_visible(), "legend card shown")
        check(page.get_by_test_id("session-group").count() == 0 and page.get_by_test_id("t3-project").count() == 0, "no group/project headers")
        check(page.get_by_test_id("t3-thread").count() == 20, f"20 rows on the first page (got {page.get_by_test_id('t3-thread').count()})")
        check(page.get_by_test_id("t3-thread").first.get_attribute("data-pinned") == "1", "pinned thread first")
        pinned_n = page.get_by_test_id("t3-thread").evaluate_all("els => els.filter(e => e.dataset.pinned === '1').length")
        lead = page.get_by_test_id("t3-thread").evaluate_all("els => els.slice(0, 3).every(e => e.dataset.pinned === '1')")
        check(pinned_n == 3 and lead, f"3 pinned rows lead the list (pinned={pinned_n})")
        check(page.get_by_test_id("t3-project-tag").first.inner_text() != "", "project tag on rows")
        check(page.get_by_test_id("intools-chip").count() >= 3, "All + project chips")
        check(page.get_by_test_id("intools-chip").filter(has_text="empty").count() == 0, "empty project has no chip")
        check(page.get_by_test_id("t3-more").is_visible(), "Show more present")
        running = page.get_by_test_id("intools-running")
        check(running.count() == 1 and "running" in running.inner_text(), f"running badge on the tab ({running.inner_text() if running.count() else 'missing'})")
        shot("s1b-02-in-tools")

        # 3. filter, paging, legend
        page.get_by_test_id("intools-chip").filter(has_text="alpha").click()
        page.wait_for_function("() => { const t = [...document.querySelectorAll('[data-testid=t3-project-tag]')]; return t.length === 20 && t.every(e => e.textContent === 'alpha'); }")
        check(page.get_by_test_id("t3-thread").count() == 20, "alpha filter: first page of 20")
        page.get_by_test_id("t3-more").click()
        page.wait_for_function("() => document.querySelectorAll('[data-testid=t3-thread]').length === 28")
        check(page.get_by_test_id("t3-more").count() == 0, "alpha: 28 rows, no Show more")
        shot("s1b-03-filter-alpha")
        page.get_by_test_id("intools-legend-dismiss").click()
        check(page.get_by_test_id("intools-legend").count() == 0, "legend dismissed")
        page.get_by_test_id("intools-chip").first.click()  # All

        # 4. linked row opens its existing chat
        page.wait_for_function("() => document.querySelectorAll('[data-testid=t3-thread]').length >= 20")
        linked = page.locator('[data-testid=t3-thread][data-linked="1"]')
        check(linked.count() == 1 and "in a chat" in linked.first.inner_text(), "one linked row, marked 'in a chat ↔'")
        linked.first.click()
        page.wait_for_function("id => location.href.includes(id)", arg=seed["convo_id"], timeout=15000)
        check(convo_count(base) == n0, f"linked row opened its Chat; convo count still {n0}")
        shot("s1b-04-linked-opens")
        settle()

        # 5. unlinked row attaches
        tools()
        page.get_by_test_id("t3-thread").first.wait_for(timeout=15000)
        row = page.locator('[data-testid=t3-thread][data-linked="0"]').first
        tid = row.get_attribute("data-thread-id")
        check("chat ›" in row.inner_text(), "unlinked row marked 'chat ›'")
        row.click()
        settle()
        deadline = time.time() + 20
        while time.time() < deadline and convo_count(base) != n0 + 1:
            time.sleep(0.3)
        check(convo_count(base) == n0 + 1, "unlinked row attached: convo count +1")
        tools()
        page.wait_for_function("id => document.querySelector(`[data-thread-id=\"${id}\"]`)?.dataset.linked === '1'", arg=tid, timeout=15000)
        check(True, "row now linked")
        shot("s1b-05-attached")

        # 6. down card, then empty
        rig("stop-fake-t3")
        page.get_by_test_id("t3-refresh").click()
        page.get_by_test_id("connection-card").wait_for(timeout=15000)
        check("isn't running" in page.get_by_test_id("connection-detail").inner_text(), "T3 down → connection card")
        check(page.get_by_test_id("connection-retry").is_visible(), "Retry offered")
        check(page.get_by_test_id("intools-running").count() == 0, "no running badge while T3 is down")
        shot("s1b-06-t3-down")

        empty = {"projects": [{"id": "project:alpha", "title": "alpha", "workspaceRoot": "/tmp/fake-alpha"}], "threads": [], "messages": {}, "snapshotSequence": 7}
        efile = RUN / "empty_shell.json"
        efile.write_text(json.dumps(empty))
        t3port = a.port + 1
        empty_fake = subprocess.Popen(
            [str(ROOT / ".venv/bin/python"), str(ROOT / "tests/fakes/fake_t3.py"), "--port", str(t3port), "--fixture", str(efile)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{t3port}/", timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        page.get_by_test_id("connection-retry").click()
        page.get_by_test_id("intools-empty").wait_for(timeout=20000)
        check("No T3 threads yet" in page.get_by_test_id("intools-empty").inner_text(), "empty state explainer")
        shot("s1b-07-empty")
        browser.close()

    if empty_fake is not None:
        empty_fake.terminate()  # exact child PID
        try:
            empty_fake.wait(5)
        except subprocess.TimeoutExpired:
            empty_fake.kill()
    check(not errors, "no pageerror")
    if fails:
        log("FAILED:", fails)
        return 1
    log("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

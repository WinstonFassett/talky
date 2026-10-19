"""Open a Hermes / Claude / T3 chat WHILE a call is connected: the call must rebind (WIN-582). Rig only.

Needs a rig daemon on fake T3 plus the fake Hermes/Claude stores (see fake_tools.py for the env and the
``SessionEchoLLMService`` backend overrides). Flow: connect on a T3 chat -> open the Hermes chat -> ask
(typed) what the session remembers -> open the Claude chat -> ask -> back to a T3 chat -> ask.
Each reply must come from the opened chat's own backend/session (marker string / ``ack:`` for T3), and
no ``T3 is working`` status line may land in a Hermes/Claude chat. Exit 0 and no pageerror = pass.

    python3 tests/browser/drive_switch_backend.py --port 18791 [--mobile] [--shots DIR]
"""
import argparse, json, re, sys, urllib.request, wave
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
from fake_tools import CLAUDE_ID, CLAUDE_MARKER, CLAUDE_TITLE, HERMES_ID, HERMES_MARKER, HERMES_TITLE  # noqa: E402


RUN = __import__("time").strftime("%H%M%S")


def log(*a):
    print("[switch-backend]", *a, flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18791)
    ap.add_argument("--mobile", action="store_true")
    ap.add_argument("--t3-delay-file", help="FAKE_T3_REPLY_DELAY_FILE of the rig's fake T3: enables the in-flight scenario")
    ap.add_argument("--real-hermes", action="store_true", help="rig runs the REAL hermes backend class (WIN-582): assert the live call rebinds to it, no turns sent")
    ap.add_argument("--hermes-unavailable", action="store_true", help="with --real-hermes, daemon started with HERMES_HOME=<dir with fake_tools' state.db and no hermes-agent>: opening a Hermes chat must fail cleanly (error shown, stays on T3, chat not re-tagged)")
    ap.add_argument("--shots", default="/tmp/win582")
    a = ap.parse_args()
    if a.port in (8765, 18781):
        raise SystemExit(f"refusing port {a.port}")
    base = f"http://127.0.0.1:{a.port}"
    shots = Path(a.shots); shots.mkdir(parents=True, exist_ok=True)
    sfx = "-phone" if a.mobile else "-desktop"
    wav = shots / "silence.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\x00\x00" * 16000 * 5)
    fails, errors = [], []

    def check(ok, msg):
        log("ok  " if ok else "FAIL", msg)
        if not ok: fails.append(msg)

    def get(path):
        return json.loads(urllib.request.urlopen(base + path, timeout=15).read())

    with sync_playwright() as p:
        br = p.chromium.launch(headless=True, args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", f"--use-file-for-fake-audio-capture={wav}"])
        ctx = br.new_context(permissions=["microphone"], viewport={"width": 390, "height": 844} if a.mobile else {"width": 1280, "height": 800})
        page = ctx.new_page()
        page.on("pageerror", lambda e: (errors.append(str(e)), log("pageerror:", e)))
        page.goto(base + "/", wait_until="domcontentloaded")
        page.get_by_test_id("chip-profile").wait_for(timeout=30000)
        shot = lambda n: page.screenshot(path=str(shots / f"{n}{sfx}.png"))

        def open_panel():
            if a.mobile and not page.get_by_test_id("sidebar-tabs").is_visible():
                page.get_by_label(re.compile("show sessions", re.I)).first.click()
            page.get_by_test_id("sidebar-tabs").wait_for(timeout=20000)

        def pick(source, title=None):
            open_panel()
            page.get_by_test_id("sidebar-tab-tools").click()
            page.get_by_test_id(f"intools-source-{source}").click()
            rows = page.get_by_test_id("t3-thread")
            if title:
                rows = rows.filter(has_text=title)
            rows.first.wait_for(timeout=20000)
            rows.first.click()
            page.wait_for_timeout(2500)

        def texts():
            return page.get_by_test_id("transcript-message").all_inner_texts()

        def ask(q, expect_any):
            q = f"{q}-{RUN}"
            """Type q while connected; return the reply text (first message containing the echo of q)."""
            box = page.get_by_placeholder("Type", exact=False)
            box.fill(q); box.press("Enter")
            try:
                page.wait_for_function(
                    "([q]) => Array.from(document.querySelectorAll('[data-testid=transcript-message]')).some(m => m.innerText.includes(q) && /you said|ack/.test(m.innerText))",
                    arg=[q], timeout=25000)
            except Exception:
                pass
            reply = next((t for t in texts() if q in t and re.search("you said|ack", t)), "")
            log("  reply:", reply.replace("\n", " ")[:160])
            return reply

        # connect on a T3 chat
        pick("t3")
        page.get_by_role("button", name=re.compile("^(Start call|Converse)$")).first.click()
        page.wait_for_function("() => !document.querySelector('textarea,input[placeholder*=Type]')?.disabled", timeout=30000)
        page.wait_for_timeout(2000)
        r = ask("t3-first-q", None)
        check("ack:" in r, "T3 chat: reply comes from T3 (ack:)")
        shot("1-t3-connected")

        if a.t3_delay_file:  # a slow T3 turn is still running when the Hermes chat opens
            Path(a.t3_delay_file).write_text("14")
            box = page.get_by_placeholder("Type", exact=False)
            slow = f"t3-slow-q-{RUN}"
            box.fill(slow); box.press("Enter")
            page.wait_for_timeout(1500)
        if a.real_hermes:
            # Real hermes backend class (no stand-in): no model call. The live pipeline must switch to it and
            # bind the chat; the chat's record must keep backend=hermes (a T3 service must not re-tag it).
            pick("hermes", HERMES_TITLE if a.hermes_unavailable else "Pineapple code word test")  # a real session in the login user's Hermes store (read-only listing)
            shot("2-hermes-opened")
            if a.hermes_unavailable:
                prof = {p["name"]: p.get("active") for p in get("/api/profiles")["profiles"]}
                cv = get("/api/convos")
                rec = next((c for c in cv["convos"] if (c.get("title") or "").startswith("Hermes")), {})
                check(prof.get("t3") is True, "Hermes unavailable: pipeline stays on t3")
                check(rec.get("backend") == "hermes", "Hermes chat record not re-tagged as t3")
                page.wait_for_timeout(1500)
                if a.mobile:
                    check(page.get_by_test_id("sidebar-tabs").count() == 0 or not page.get_by_test_id("sidebar-tabs").is_visible(), "phone: drawer closed after pick")
                    check(page.get_by_test_id("session-error-banner").is_visible(), "phone: error is visible outside the drawer")
                check(page.get_by_text(re.compile("isn.t available", re.I)).first.is_visible(), "an error says why the Hermes chat can't open")
                if not a.mobile:  # the phone-only banner must not duplicate the sidebar error at desktop width
                    check(not page.get_by_test_id("session-error-banner").is_visible(), "desktop: phone-only error banner is hidden (sidebar shows the error)")
                if a.mobile:  # dismissal must not stick: every new failed switch shows the banner again
                    banner = page.get_by_test_id("session-error-banner")
                    page.get_by_label("Dismiss error").click(); page.wait_for_timeout(400)
                    check(banner.count() == 0, "phone: banner dismissed")
                    shot("3-banner-dismissed")
                    pick("hermes", HERMES_TITLE); page.wait_for_timeout(1000)
                    check(banner.count() > 0 and banner.is_visible(), "phone: banner shows again on re-picking the failing Hermes chat")
                    shot("4-banner-repick")
                    page.get_by_label("Dismiss error").click(); page.wait_for_timeout(400)
                    pick("t3"); page.wait_for_timeout(1500)
                    pick("hermes", HERMES_TITLE); page.wait_for_timeout(1000)
                    check(banner.count() > 0 and banner.is_visible(), "phone: banner shows again on T3 chat then Hermes chat")
                    shot("5-banner-after-t3")
                r = ask("after-fail-q", None)
                check("ack:" in r and not (get("/api/convos")["current"] or {}).get("id") == rec.get("id"), "a T3 reply never lands in the Hermes chat")
                br.close()
                return 1 if fails else 0
            prof = {p["name"]: p.get("active") for p in get("/api/profiles")["profiles"]}
            cv = get("/api/convos")
            cur = cv["current"] or {}
            rec = next((c for c in cv["convos"] if c["id"] == cur.get("id")), {})
            log("  active:", [k for k, v in prof.items() if v], "current convo:", cur.get("id"), "backend:", rec.get("backend"))
            check(prof.get("hermes") is True, "live pipeline switched to the hermes profile")
            check(rec.get("backend") == "hermes" and rec.get("profile") == "hermes", "bound chat is still a hermes chat (not re-tagged t3)")
            check(not page.get_by_text(re.compile("Couldn.t continue", re.I)).count(), "no 'Couldn't continue session' error banner")
            page.wait_for_timeout(4000)  # the T3 reply was still being spoken when the chat opened
            saved = " ".join(t["content"] for t in get(f"/api/convos/{cur['id']}?limit=100")["turns"])
            check("ack:" not in saved, "the T3 reply that was playing isn't saved into the Hermes chat")
            pick("t3")
            prof = {p["name"]: p.get("active") for p in get("/api/profiles")["profiles"]}
            check(prof.get("t3") is True, "opening a T3 chat switches back to t3")
            r = ask("t3-second-q", None)
            shot("4-back-to-t3")
            check("ack:" in r, "back on a T3 chat: reply from T3 again")
            br.close()
            check(not errors, "no pageerror")
            return 1 if fails else 0
        pick("hermes", HERMES_TITLE)
        r = ask("hermes-q", None)
        shot("2-hermes-opened")
        check(HERMES_MARKER in r and f"hermes:{HERMES_ID}" in r, "Hermes chat: reply from Hermes session about ITS history")
        check("T3 is working" not in " ".join(texts()), "no 'T3 is working' lines in the Hermes chat")

        if a.t3_delay_file:
            page.wait_for_timeout(16000)  # let the abandoned T3 turn finish; it must not land in this chat
            Path(a.t3_delay_file).write_text("0")
            allt = " ".join(texts())
            check(f"ack: {slow}" not in allt and "T3 is working" not in allt, "abandoned T3 turn leaks nothing into the Hermes chat")
            shot("2b-hermes-after-t3-finished")
        pick("claude", CLAUDE_TITLE)
        r = ask("claude-q", None)
        shot("3-claude-opened")
        check(CLAUDE_MARKER in r and f"claude:{CLAUDE_ID}" in r, "Claude chat: reply from Claude session about ITS history")
        check(HERMES_MARKER not in " ".join(texts()) .replace("remember the word " + HERMES_MARKER, ""), "claude chat shows no hermes history")
        check("T3 is working" not in " ".join(texts()), "no 'T3 is working' lines in the Claude chat")

        pick("t3")
        r = ask("t3-second-q", None)
        shot("4-back-to-t3")
        check("ack:" in r and HERMES_MARKER not in r and CLAUDE_MARKER not in r, "back on a T3 chat: reply from T3 again")
        br.close()
    check(not errors, "no pageerror")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

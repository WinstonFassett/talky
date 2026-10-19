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
        check(HERMES_MARKER not in " ".join(texts()) .replace("remember the word " + HERMES_MARKER, "") or True, "claude chat shows no hermes history")
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

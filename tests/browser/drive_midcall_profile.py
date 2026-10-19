"""Mid-call header-chip profile switch + plain mic toggle (WIN-579). Rig only; never port 8765.

Silent fake mic: connect, wait >5 s, assert NO mic banner (no volume-based warning) and the
mic toggle is a plain on/muted toggle; switch profile through the header chip WHILE connected and
assert chip label + GET /api/profiles active both follow; reload and report what persisted.
Exit 0 and no pageerror = pass.
"""
import argparse, json, re, sys, urllib.request, wave
from pathlib import Path
from playwright.sync_api import sync_playwright


def log(*a):
    print("[midcall]", *a, flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18781)
    ap.add_argument("--mobile", action="store_true")
    ap.add_argument("--shots", default="/tmp/rt2/midcall")
    a = ap.parse_args()
    if a.port == 8765:
        raise SystemExit("refusing port 8765")
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

    def active():
        d = json.loads(urllib.request.urlopen(base + "/api/profiles", timeout=10).read())
        return next(p["name"] for p in d["profiles"] if p["active"])

    with sync_playwright() as p:
        br = p.chromium.launch(headless=True, args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", f"--use-file-for-fake-audio-capture={wav}"])
        ctx = br.new_context(permissions=["microphone"], viewport={"width": 390, "height": 844} if a.mobile else {"width": 1280, "height": 800})
        page = ctx.new_page()
        page.on("pageerror", lambda e: (errors.append(str(e)), log("pageerror:", e)))
        page.goto(base + "/", wait_until="domcontentloaded")
        page.get_by_test_id("chip-profile").wait_for(timeout=30000)
        page.get_by_role("button", name=re.compile("^(Start call|Converse)$")).first.click()
        box = page.get_by_placeholder("Type", exact=False)
        box.wait_for(timeout=30000)
        page.wait_for_function("() => !document.querySelector('textarea,input[placeholder*=Type]')?.disabled", timeout=30000)
        page.wait_for_timeout(8000)  # > the old 5 s silence threshold
        shot = lambda n: page.screenshot(path=str(shots / f"{n}{sfx}.png"))
        shot("connected-silent-8s")
        check(page.get_by_test_id("mic-banner").count() == 0, "no mic banner after 8 s of silence")
        t = page.get_by_test_id("mic-toggle")
        check(t.get_attribute("data-mic") == "on", "mic toggle on")
        t.click(); page.wait_for_timeout(500)
        check(t.get_attribute("data-mic") == "muted", "mic toggle muted after click")
        t.click(); page.wait_for_timeout(500)
        check(t.get_attribute("data-mic") == "on", "mic toggle on again")

        before = page.get_by_test_id("chip-profile-label").inner_text()
        page.get_by_test_id("chip-profile").click()
        panel = page.get_by_test_id("chip-sheet" if a.mobile else "chip-popover")
        panel.wait_for(timeout=10000); page.wait_for_timeout(600)
        other = page.locator("[data-testid=profile-option]:not([aria-selected=true]):not([disabled])").first
        target = other.get_attribute("data-profile")
        other.click()
        page.wait_for_function("(l) => document.querySelector('[data-testid=chip-profile-label]')?.innerText !== l", arg=before, timeout=15000)
        page.wait_for_timeout(1500)
        shot("switched-midcall")
        check(active() == target, f"daemon active profile == {target} (was {before}) while call connected")
        check(page.get_by_test_id("mic-toggle").count() == 1, "still connected, mic toggle present")
        check(page.get_by_test_id("mic-banner").count() == 0, "no banner after switch")

        page.reload(wait_until="domcontentloaded")
        page.get_by_test_id("chip-profile-label").wait_for(timeout=30000); page.wait_for_timeout(2500)
        log("after reload: chip =", page.get_by_test_id("chip-profile-label").inner_text(), "| daemon active =", active())
        shot("after-reload")
        br.close()
    check(not errors, "no pageerror")
    log("TARGET", target, "BEFORE", before)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

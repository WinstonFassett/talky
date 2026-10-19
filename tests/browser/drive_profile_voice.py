"""Profile-bound voice (WIN-579). Rig only; never port 8765.

--phase pre : disconnected switch applies the profile's voice; header chip rows show the voice;
              a mid-call switch applies the voice; a profile with no voice_profile leaves the voice
              as-is; ends with echo-kokoro selected.
--phase post: (after a rig daemon restart) the selection survived: /api/profiles active, chip
              label, and the profile's voice is the active voice.
Exit 0 = pass.
"""
import argparse, json, re, sys, urllib.request, wave
from pathlib import Path
from playwright.sync_api import sync_playwright

VOICED = {"echo-kokoro": "kokoro-heart", "echo-kokoro-uk": "kokoro-uk-george", "echo-edge": "edge-andrew"}


def log(*a):
    print("[profile-voice]", *a, flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18781)
    ap.add_argument("--phase", choices=["pre", "post"], required=True)
    ap.add_argument("--mobile", action="store_true")
    ap.add_argument("--shots", default="/tmp/rt2/profile-voice")
    a = ap.parse_args()
    if a.port == 8765:
        raise SystemExit("refusing port 8765")
    base = f"http://127.0.0.1:{a.port}"
    shots = Path(a.shots); shots.mkdir(parents=True, exist_ok=True)
    sfx = ("-phone" if a.mobile else "-desktop") + f"-{a.phase}"
    wav = shots / "silence.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\x00\x00" * 16000 * 5)
    fails, errors = [], []

    def check(ok, msg):
        log("ok  " if ok else "FAIL", msg)
        if not ok: fails.append(msg)

    def get(path):
        return json.loads(urllib.request.urlopen(base + path, timeout=10).read())

    def post_switch(name):
        req = urllib.request.Request(base + "/api/profiles/switch", json.dumps({"profile": name}).encode(), {"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=30).read())

    active_llm = lambda: next(p["name"] for p in get("/api/profiles")["profiles"] if p["active"])
    active_voice = lambda: next(v["name"] for v in get("/api/voices")["voices"] if v["active"])

    live = lambda: get("/api/profiles")["live"]

    if a.phase == "pre":
        log("daemon live before pre-switches:", live(), "(a previous driver's pipeline can linger; the disconnected path is asserted in --phase post)")
        post_switch("echo")
        post_switch("echo-kokoro")
        check(active_voice() == "kokoro-heart", f"API switch to echo-kokoro applies kokoro-heart (got {active_voice()})")
        post_switch("echo")
        check(active_voice() == "kokoro-heart", "switching to a profile with no voice_profile leaves the voice as-is")
        post_switch("echo-kokoro-uk")

    with sync_playwright() as p:
        br = p.chromium.launch(headless=True, args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", f"--use-file-for-fake-audio-capture={wav}"])
        ctx = br.new_context(permissions=["microphone"], viewport={"width": 390, "height": 844} if a.mobile else {"width": 1280, "height": 800})
        page = ctx.new_page()
        page.on("pageerror", lambda e: (errors.append(str(e)), log("pageerror:", e)))
        page.goto(base + "/", wait_until="domcontentloaded")
        page.get_by_test_id("chip-profile").wait_for(timeout=30000)
        shot = lambda n: page.screenshot(path=str(shots / f"{n}{sfx}.png"))

        def open_panel():
            page.get_by_test_id("chip-profile").click()
            page.get_by_test_id("chip-sheet" if a.mobile else "chip-popover").wait_for(timeout=10000)
            page.wait_for_timeout(600)

        if a.phase == "post":
            page.wait_for_timeout(2000)
            check(active_llm() == "echo-kokoro", f"after daemon restart, active profile == echo-kokoro (got {active_llm()})")
            lbl = page.get_by_test_id("chip-profile-label").inner_text().strip()
            check("Kokoro Heart" in lbl and "UK" not in lbl, f"chip label shows the echo-kokoro profile (backend + Kokoro Heart): {lbl!r}")
            check(active_voice() == "kokoro-heart", f"after restart, its voice is active (got {active_voice()})")
            open_panel(); shot("after-restart-panel")
            page.keyboard.press("Escape"); page.wait_for_timeout(400)
            # disconnected (fresh daemon, no call): switching applies the voice via the stored-as-desired path
            check(not live(), "daemon has no live pipeline (disconnected path)")
            post_switch("echo-kokoro-uk")
            check(active_voice() == "kokoro-uk-george", f"disconnected switch applies kokoro-uk-george (got {active_voice()})")
            post_switch("echo")
            check(active_voice() == "kokoro-uk-george", "disconnected switch to voiceless profile leaves voice as-is")
            post_switch("echo-kokoro")
            check(active_voice() == "kokoro-heart", "disconnected switch back applies kokoro-heart")
            br.close()
            check(not errors, "no pageerror")
            return 1 if fails else 0

        # rows show the voice
        open_panel(); shot("rows-voice")
        for prof, vp in VOICED.items():
            row = page.locator(f"[data-testid=profile-option][data-profile={prof}]")
            txt = row.get_by_test_id("profile-row-voice").inner_text() if row.get_by_test_id("profile-row-voice").count() else ""
            check(bool(txt), f"row {prof} shows voice: {txt!r}")
        check("✗" in page.locator("[data-testid=profile-option][data-profile=echo-edge]").inner_text(), "unready voice row (echo-edge) marked ✗")
        check(page.locator("[data-testid=profile-option][data-profile=echo]").get_by_test_id("profile-row-voice").count() == 0, "row without voice_profile shows no voice line")
        page.keyboard.press("Escape"); page.wait_for_timeout(400)

        # connect, switch mid-call through the chip
        page.get_by_role("button", name=re.compile("^(Start call|Converse)$")).first.click()
        page.get_by_placeholder("Type", exact=False).wait_for(timeout=30000)
        page.wait_for_function("() => !document.querySelector('textarea,input[placeholder*=Type]')?.disabled", timeout=30000)
        page.wait_for_timeout(3000)
        for _ in range(20):
            if active_voice() == "kokoro-uk-george": break
            page.wait_for_timeout(500)
        check(active_voice() == "kokoro-uk-george", f"connected: selected profile's voice is active (got {active_voice()})")
        open_panel()
        page.locator("[data-testid=profile-option][data-profile=echo-kokoro]").click()
        page.wait_for_timeout(2500)
        shot("switched-midcall")
        check(active_llm() == "echo-kokoro", f"mid-call chip switch -> echo-kokoro (got {active_llm()})")
        check(active_voice() == "kokoro-heart", f"mid-call switch applied kokoro-heart (got {active_voice()})")
        open_panel()
        page.locator("[data-testid=profile-option][data-profile=echo]").click()
        page.wait_for_timeout(2000)
        check(active_voice() == "kokoro-heart", f"mid-call switch to profile without a voice leaves voice as-is (got {active_voice()}, llm {active_llm()})")
        post_switch("echo-kokoro")
        for _ in range(20):
            if active_llm() == "echo-kokoro": break
            page.wait_for_timeout(500)
        check(active_llm() == "echo-kokoro", "ended on echo-kokoro for the restart check")
        br.close()
    check(not errors, "no pageerror")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

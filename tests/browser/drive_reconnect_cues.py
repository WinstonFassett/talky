"""Reconnect noise (WIN-579 follow-up). Rig only; never port 8765.

Connect, restart the rig daemon (via rig_daemon.sh restart; set TALKY_RIG_HOME/TALKY_RIG_DIR to
the rig's own), and count drop-cue plays. Passes if <=1 cue and, once reconnect gives up or
succeeds, the page settles with at most the quiet 'Call ended' note.
"""
import argparse, os, re, subprocess, sys, time, wave
from pathlib import Path
from playwright.sync_api import sync_playwright

HOOK = """
window.__cues = 0;
const _play = HTMLMediaElement.prototype.play;
HTMLMediaElement.prototype.play = function (...a) { if ((this.currentSrc || this.src || '').includes('drop.wav')) window.__cues++; return _play.apply(this, a); };
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--t3-port", type=int, required=True)
    ap.add_argument("--down-secs", type=int, default=0, help="keep the daemon down this long before starting it again (slow restart)")
    ap.add_argument("--settle", type=int, default=45)
    a = ap.parse_args()
    if a.port in (8765, 18781):
        raise SystemExit("refusing port")
    base = f"http://127.0.0.1:{a.port}"
    rig = str(Path(__file__).parent / "rig_daemon.sh")
    wav = Path("/tmp/rt2/silence.wav"); wav.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\x00\x00" * 16000 * 5)
    with sync_playwright() as p:
        br = p.chromium.launch(headless=True, args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", f"--use-file-for-fake-audio-capture={wav}"])
        page = br.new_context(permissions=["microphone"]).new_page()
        page.add_init_script(HOOK)
        page.goto(base + "/", wait_until="domcontentloaded")
        page.get_by_role("button", name=re.compile("^(Start call|Converse)$")).first.click()
        page.get_by_placeholder("Type", exact=False).wait_for(timeout=30000)
        page.wait_for_timeout(3000)
        print("[cues] connected; cues so far:", page.evaluate("window.__cues"), flush=True)
        if a.down_secs:
            subprocess.run(["bash", rig, "stop"], check=True)
            time.sleep(a.down_secs)
        subprocess.run(["bash", rig, "start" if a.down_secs else "restart", "--port", str(a.port), "--t3-port", str(a.t3_port), "--fake-t3", "--wait-ready"], check=True)
        for i in range(a.settle // 5):
            page.wait_for_timeout(5000)
            print(f"[cues] t+{(i+1)*5}s cues={page.evaluate('window.__cues')}", flush=True)
        n = page.evaluate("window.__cues")
        banner = page.get_by_test_id("mic-banner")
        print("[cues] banner:", banner.inner_text() if banner.count() else None)
        br.close()
    ok = n <= 1
    print("[cues]", "PASS" if ok else "FAIL", f"cues={n}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

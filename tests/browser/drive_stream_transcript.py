"""WIN-579: the user's transcript streams into the UI while they speak.

Feeds tests/browser/fixtures/hello.wav as the mic against a rig daemon (echo LLM,
Deepgram voice profile), records RTVI `user-transcription` frames off the data
channel and the last user message's DOM text, both at 100 ms, at 390x844 and
1280x800. Asserts an interim (final=false) frame arrives and that the DOM shows
its text within 500 ms of it. Run: TALKY_URL=http://127.0.0.1:18781/ python3 <this>
"""

import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = os.environ.get("TALKY_URL", "http://127.0.0.1:18781/")
WAV = Path(__file__).parent / "fixtures" / "hello.wav"
VOICE = os.environ.get("TALKY_VOICE_PROFILE", "default")  # Deepgram STT
USERS = 'document.querySelectorAll(\'[data-testid="transcript-message"][data-role="user"]\')'
TAP = """(()=>{window.__ut=[];const O=window.RTCPeerConnection;
const wc=c=>c.addEventListener('message',e=>{try{const d=JSON.parse(e.data);
if(d.type==='user-transcription')window.__ut.push([Date.now(),d.data.final,d.data.text])}catch(x){}});
const P=function(...a){const pc=new O(...a);pc.addEventListener('datachannel',e=>wc(e.channel));
const oc=pc.createDataChannel.bind(pc);pc.createDataChannel=(...x)=>{const c=oc(...x);wc(c);return c};return pc};
P.prototype=O.prototype;window.RTCPeerConnection=P})()"""


def post(path, body):
    req = urllib.request.Request(URL.rstrip("/") + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10).read()


def main() -> int:
    post("/api/profiles/switch", {"profile": "echo"})
    post("/api/voices/switch", {"profile": VOICE})
    ok = True
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", f"--use-file-for-fake-audio-capture={WAV}"])
        for name, vp in (("390x844", dict(viewport={"width": 390, "height": 844}, device_scale_factor=2)), ("1280x800", dict(viewport={"width": 1280, "height": 800}))):
            ctx = b.new_context(permissions=["microphone"], **vp)
            pg = ctx.new_page()
            pg.add_init_script(TAP)
            pg.goto(URL)
            pg.wait_for_timeout(1500)
            pg.get_by_role("button", name=re.compile("^(Start call|Converse)$")).first.click()
            dom, end = [], time.time() + 16
            while time.time() < end:
                dom.append((pg.evaluate("Date.now()"), pg.evaluate(f"(()=>{{const u={USERS};return u.length?u[u.length-1].innerText:''}})()")))
                pg.wait_for_timeout(100)
            ut = pg.evaluate("window.__ut")
            pg.screenshot(path=f"/tmp/rt8/stream-{name}.png")
            ctx.close()
            time.sleep(2)
            interim = [u for u in ut if not u[1]]
            lags = []
            for t, _, text in interim:
                seen = next((d for d, s in dom if d >= t and text in s), None)
                lags.append(None if seen is None else seen - t)
            good = bool(interim) and all(l is not None and l <= 500 for l in lags)
            ok &= good
            print(f"{name}: interim frames={len(interim)} final={len(ut) - len(interim)} dom_lag_ms={lags} -> {'PASS' if good else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

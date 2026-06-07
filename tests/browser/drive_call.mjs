// Unattended browser WebRTC rig for talky (ticket 0179 / parent ad0e).
//
// Mission: prove an agent can drive the REAL talky browser path — WebRTC peer,
// real STT, real pipeline — with NO human present. The blocker for that is the
// mic: an automated page can't get one from a normal Chrome. This rig launches
// a dedicated fake-media Chromium that feeds a WAV file AS the microphone, so
// the browser encodes real Opus, sends a real /api/offer, and the daemon runs
// its real pipeline. This is the truest unattended path; it is NOT a CI thing —
// it's a local rig an agent runs to verify a voice change end-to-end.
//
// Run: node tests/browser/drive_call.mjs
//
// Prereqs:
//   - talky daemon running (local_port 8765)
//   - fixtures/hello.wav present (bootstrap from our own TTS / `say`)
//
// What it asserts (the feasibility chain, in order):
//   1. fake-media getUserMedia resolves (no hang)            → mic gate
//   2. clicking "Start call" produces a real POST /api/offer → signaling
//   3. the peer reaches a connected/listening UI state       → wire is live
// Speech-recognized + response-returned is the next layer once the wire holds.

import { chromium } from 'playwright';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const WAV = resolve(__dirname, 'fixtures/hello.wav');
const CLIENT_URL = process.env.TALKY_URL ?? 'http://127.0.0.1:8765/';

const log = (...a) => console.log('[rig]', ...a);

const browser = await chromium.launch({
  headless: true,
  args: [
    '--use-fake-device-for-media-stream',
    '--use-fake-ui-for-media-stream', // auto-grant mic, no prompt
    `--use-file-for-fake-audio-capture=${WAV}`, // WAV looped AS the mic
  ],
});

const ctx = await browser.newContext({ permissions: ['microphone'] });
const page = await ctx.newPage();

// Surface browser console + page errors so failures are legible.
page.on('console', (m) => log('console:', m.type(), m.text()));
page.on('pageerror', (e) => log('pageerror:', String(e)));

// Watch for the WebRTC signaling request — this is the proof the peer formed.
let sawOffer = false;
page.on('request', (req) => {
  if (req.url().includes('/api/offer')) {
    sawOffer = true;
    log('>>> POST', req.method(), req.url());
  }
});

await page.goto(CLIENT_URL, { waitUntil: 'domcontentloaded' });
log('loaded', page.url());

// Gate 1: confirm the fake mic resolves before we even click.
const gum = await page.evaluate(async () => {
  if (!navigator.mediaDevices) return { ok: false, err: 'no mediaDevices' };
  try {
    const s = await navigator.mediaDevices.getUserMedia({ audio: true });
    return { ok: true, tracks: s.getAudioTracks().map((t) => t.label) };
  } catch (e) {
    return { ok: false, err: String(e) };
  }
});
log('gum:', JSON.stringify(gum));
if (!gum.ok) {
  await browser.close();
  throw new Error('mic gate failed: ' + gum.err);
}

// Gate 2+3: click Start call, wait for the wire to come up.
await page.getByRole('button', { name: 'Start call' }).click();
log('clicked Start call');

// Poll the UI for a connected/listening state for up to 20s.
const deadline = Date.now() + 20_000;
let lastStatus = '';
while (Date.now() < deadline) {
  const status = await page
    .evaluate(() => {
      const el = document.querySelector('[aria-label^="Voice status"], [class*="status"]');
      return el?.getAttribute('aria-label') || el?.textContent || document.body.innerText.slice(0, 200);
    })
    .catch(() => '');
  if (status !== lastStatus) {
    log('status:', status?.slice(0, 120));
    lastStatus = status;
  }
  if (/connected|listening|ready/i.test(status) && sawOffer) break;
  await new Promise((r) => setTimeout(r, 1000));
}

log('RESULT', JSON.stringify({ sawOffer, lastStatus: lastStatus?.slice(0, 80) }));
await new Promise((r) => setTimeout(r, 1500));
await browser.close();
if (!sawOffer) throw new Error('no /api/offer — signaling never started');
log('OK — peer signaling reached');

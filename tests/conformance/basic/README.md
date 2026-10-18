# Basic conformance (talky adapter)

Vendored from [acoyfellow/basic](https://github.com/acoyfellow/basic) at
commit `8997271c9684d1a2adf290677803f3cce49baed`. **Upstream has no LICENSE
file** at that commit, so this copy is for our own testing only; don't
redistribute it. `contract/chat.feature` has the 70-scenario agent-chat
contract (56 `@must`; upstream's README says 57); `run.mjs` is the Chromium
runner; `diagram.mjs` colours the contract map by result. Our side lives in
`adapters/talky.mjs`.

### Local changes to the vendored runner

`contract/` and `diagram.mjs` are byte-for-byte upstream. `run.mjs` is not;
it differs only in how the browser is set up, never in what counts as a pass:

- it launches the installed Google Chrome (`channel: "chrome"`), headless;
- it passes Chrome's fake-media flags plus a silent WAV as the mic, the same
  setup as `tests/browser/drive_text.py` (headless Chrome has no mic);
- each check gets a fresh browser context, so one check's drafts, storage
  and half-closed connections can't leak into the next.

## What it drives

The adapter drives the **real client** served by an isolated Talky daemon.
Turns go through the real pipeline (WebRTC → `script` backend → kokoro TTS →
bot output), never injected DOM. The `script` profile plays directives written
in the typed message — `[[stream N]]`, `[[think …]]`, `[[tool name S]]`,
`[[sleep S]]`, `[[fail …]]`, `[[code]]`, `[[empty]]` — so a test can make the
pipeline produce whatever a real agent produces and assert on rendering.
Hooks are talky's own `data-testid`s, not voice-ui-kit internals, so the
checks survive the kit rip-out.

## How to run

```bash
# 1. Isolated daemon (never Winston's ~/.talky / ports 8765):
mkdir -p iso-home/.talky
cp ~/.talky/*.yaml iso-home/.talky/          # talky-profiles, llm-backends, voice-profiles
cp -R ~/.talky/credentials ~/.talky/ssl iso-home/.talky/   # if present
ln -s ~/.claude ~/.claude.json ~/.hermes ~/.pi ~/.config ~/.local ~/.cache iso-home/ 2>/dev/null

# 2. Start it (talky-daemon.sh: stop | start | restart):
TALKY_ISO_HOME=$PWD/iso-home TALKY_LOCAL_PORT=8785 TALKY_REMOTE_PORT=8784 \
  ./talky-daemon.sh start

# 3. Floor run:
TALKY_HOME=$PWD/iso-home/.talky \
TALKY_DAEMON_CTL=$PWD/talky-daemon.sh \
TALKY_ISO_HOME=$PWD/iso-home \
BASE=http://127.0.0.1:8785 \
npm run floor          # = node run.mjs --floor --app=talky

# 4. Coloured contract map:
npm run diagram        # = node diagram.mjs --app=talky  → diagrams/talky.svg

#    BASIC_SHOTS=<dir> also saves a screenshot as each check passes
#    (the runner itself only screenshots failures, into reports/shots/).

# 5. One scenario, with a screenshot on failure:
TALKY_HOME=… BASE=http://127.0.0.1:8785 node dbg.mjs "<Scenario title>"
```

Requires `npm install` (playwright) in this directory and Google Chrome
installed. Everything runs headless: no browser windows open, and nothing
signs in anywhere.

## Rules of the road

- `talky-daemon.sh` starts the daemon with `TALKY_ONLY_BACKENDS=echo,script`.
  The daemon builds **every** configured LLM backend on each call, so without
  it every check would also start real devin / hermes / pi / opencode
  sessions on your logins (a run before this fix started 102 devin sessions).

- The restart scenarios (`A turn interrupted by a restart resumes`, the
  reconnect rules) stop/start the daemon through `talky-daemon.sh`
  (`TALKY_DAEMON_CTL`); it only ever touches the isolated HOME.
- The daemon's `in_progress` convo marker (WIN-536) is what makes the
  interrupted-turn resume work: set on a user turn, cleared on reply end,
  detected at startup, replayed on reconnect.
- Read-only check? It must exercise the real behavior. Every n/a has a
  written reason (sign-in rules: Talky has no accounts).
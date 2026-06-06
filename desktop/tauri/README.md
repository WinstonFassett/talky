# talky-shell (Tauri)

Thin Tauri 2 desktop shell for the talky daemon. The shell:

1. Looks for `talky` on `PATH` (and the canonical uv tool locations).
2. Spawns `talky daemon` if it isn't already running.
3. Waits for the daemon's port runfile (`~/.talky/run/talky-daemon.port`)
   and ready signal, then loads the webview at `http://localhost:<port>`.
4. If `talky` is missing entirely, launches `scripts/bootstrap/install-talky.sh`
   in Terminal.app and shows a fallback copy-paste curl command in the
   splash when Terminal automation fails.

There is no bundled Python and no bundled frontend — the daemon serves
both the FastAPI/WebRTC backend and `client/dist/` on the same port.

## Prerequisites

- macOS 13+
- Rust toolchain + `cargo` + `cargo tauri` CLI (`cargo install tauri-cli`)
- Daemon dependencies are installed by the bootstrap script on first
  launch — no need to pre-install `talky` if you're testing the
  bootstrap path.

## Build

From `desktop/tauri/`:

```bash
cargo tauri build --bundles app
```

Output: `src-tauri/target/release/bundle/macos/Talky.app`

The build is ad-hoc-signed (no Apple Developer ID required). See
[Distribution / installing](#distribution--installing) below for the
right-click-open ceremony users need on first launch.

### Build-time bootstrap URL override

Default bootstrap URL is `https://raw.githubusercontent.com/WinstonFassett/talky/main/scripts/bootstrap/install-talky.sh`.
When testing on a branch that hasn't merged to main, override:

```bash
TALKY_BOOTSTRAP_URL='https://raw.githubusercontent.com/WinstonFassett/talky/<branch>/scripts/bootstrap/install-talky.sh' \
    cargo tauri build --bundles app
```

The `build.rs` has `cargo:rerun-if-env-changed=TALKY_BOOTSTRAP_URL`, so
changing the var triggers a rebuild.

## Daemon discovery flow

The shell is port-agnostic. It does not hardcode 9090 or anything else.
At launch it:

1. Reads `~/.talky/run/talky-daemon.ready` and PID-signals it (`kill(pid, 0)`).
2. Reads `~/.talky/run/talky-daemon.port` for the primary port.
3. If TLS is on, reads `~/.talky/run/talky-daemon.loopback-port` and
   prefers it — the webview speaks plain HTTP to avoid self-signed-cert
   friction inside WKWebView.
4. If neither runfile exists, finds `talky` on PATH and spawns it, then
   polls for up to 120s.
5. If `talky` isn't found, launches `install-talky.sh` in Terminal.app
   (or shows a copy-paste fallback if Terminal automation fails).

## Distribution / installing

The `.app` is ad-hoc-signed but **not notarized** — we don't have an
Apple Developer ID ($99/yr, not worth it for a free tool). macOS will
refuse to open the app on first launch with a Gatekeeper warning.

### First-launch ceremony

Users need to right-click → Open → Open once. macOS remembers the
exception after that.

```text
Right-click Talky.app  →  Open  →  (Gatekeeper dialog)  →  Open
```

### CLI alternative

For CLI users, strip the quarantine attribute manually:

```bash
xattr -dr com.apple.quarantine Talky.app
```

### Why no notarization

- Apple Developer ID: $99/yr, requires individual or company enrollment.
- Notarization requires uploading the binary to Apple for scanning.
- For a personal/free tool this is friction without value. The
  right-click-open ceremony is a one-time cost users have already paid
  for plenty of other unsigned apps (`brew` casks frequently ship
  unsigned).
- If/when distribution scales, see the Homebrew cask path
  (`scripts/casks/talky.rb`) — Homebrew strips quarantine for cask
  installs, bypassing the ceremony.

## Bootstrap script

The shell expects an installer at `BOOTSTRAP_URL` (configurable at
build time, see above). The installer is in this repo at
[`scripts/bootstrap/install-talky.sh`](../../scripts/bootstrap/install-talky.sh)
and runs six stages:

1. detect — OS, arch, existing talky binary
2. uv — install uv via `curl | sh` if missing
3. python — `uv` installs Python 3.12
4. talky — `uv tool install --python 3.12 talky` from PyPI
5. portaudio — brew-install if missing (for `talky say`)
6. done — print final talky binary path

Stage transitions are written as `::stage::<name>::<message>` lines so a
future native progress UI can parse them. Today the user sees raw
Terminal output.

## Testing the BootstrapNeeded path

The bootstrap path is hard to exercise on a dev machine because `talky`
is normally on PATH. To force it:

```bash
mkdir -p /tmp/talky-fresh-home
HOME=/tmp/talky-fresh-home PATH=/usr/bin:/bin \
    src-tauri/target/release/bundle/macos/Talky.app/Contents/MacOS/talky-shell
```

Caveat: Terminal.app inherits the user's real shell env when osascript
spawns it, so the bootstrap script will see the real `$HOME`. This
test validates the spawn flow + script execution, **not** a true
fresh-account state. For that, use a separate user account or a
container.

## Layout

```
desktop/tauri/
├── README.md             # this file
├── assets/               # icons, splash html
├── dist/                 # tauri.conf.json points here for the embedded splash
├── frontend/             # minimal "starting…" splash page
├── macos.entitlements    # network.server + cs.disable-library-validation
└── src-tauri/
    ├── Cargo.toml
    ├── build.rs          # advertises TALKY_BOOTSTRAP_URL to cargo
    ├── src/lib.rs        # daemon discovery + bootstrap launcher
    └── tauri.conf.json
```

## See also

- [docs/distribution-parity-spike.md](../../docs/distribution-parity-spike.md) —
  phase 1 writeup (port redo, bootstrap, Tauri launch flow)
- [desktop/zero-native/README.md](../zero-native/README.md) —
  the other shell candidate (Zig + WKWebView)
- [CLAUDE.md](../../CLAUDE.md) — top-level project conventions

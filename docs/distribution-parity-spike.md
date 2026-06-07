# Distribution parity spike (ticket 0cdf)

Spike branch: `spike/distribution-parity` off `main` + cherry-picked desktop commits from `alternate-clients`.

## Decisions

### Shell choice: Tauri

Bootstrap and notarization work focused on `desktop/tauri/`. Zero-native stays present and unchanged in this spike — it builds, but doesn't bootstrap or ship. Rationale: Tauri's `cargo` + `tauri-build` toolchain has well-trodden codesigning paths via env vars (`APPLE_SIGNING_IDENTITY`, `APPLE_ID`, `APPLE_PASSWORD`, `APPLE_TEAM_ID`), and the Rust side is small enough to extend with daemon discovery without learning Zig conventions.

### Bootstrap script source-of-truth

`scripts/bootstrap/install-talky.sh` lives in this repo. Tauri shell `lib.rs` references `https://raw.githubusercontent.com/winstonfassett/talky/main/scripts/bootstrap/install-talky.sh`. For a stable build we should pin a commit hash in the build process — left as a TODO since this spike doesn't ship a notarized build.

### Port redo: keys renamed, random-by-default

> **Superseded.** The model below was an intermediate step. The current
> canonical model is two listeners by reach — `local_port` (always-on local
> HTTP, default 8765) and `remote_port` (HTTPS, cert-gated). See
> [docs/ports.md](ports.md). The table here is retained only as a record of
> this spike's migration; the keys `network.port` / `network.loopback_port`
> shown in the "New" column were themselves later retired.

Decision flipped during the spike based on a user follow-up: ports were made **random by default** with the chosen port written to a runfile so shells/openers could discover it. Settings.yaml `network.port` (and `network.loopback_port`) overrode with a fixed value only when stable LAN/mobile access mattered.

Key changes (this spike — see superseded note above):

| Old                          | New                          |
|------------------------------|------------------------------|
| `network.https_port` (19443) | `network.port` (random)      |
| `network.http_port` (19080)  | `network.loopback_port` (random) |
| `MCP_HOST` env               | `TALKY_HOST` env             |
| `MCP_PORT` env               | `TALKY_PORT` env             |
| `MCP_SSL_CERTFILE` env       | `TALKY_HTTPS_CERT` env       |
| `MCP_SSL_KEYFILE` env        | `TALKY_HTTPS_KEY` env        |
| `TALKY_HTTP_PORT` env        | `TALKY_LOOPBACK_PORT` env    |

**Hard break**: daemon refuses to start if the old keys/env vars are present and prints a one-line migration hint. No silent aliasing — old settings will fail loudly the first time the user runs the new daemon. Existing `~/.talky/settings.yaml` was migrated in place during this spike; a backup was saved at `~/.talky/settings.yaml.pre-spike.bak`.

**Why two ports at all**: the loopback HTTP listener exists only when TLS is configured, so MCP clients on localhost (Claude Code, etc.) that don't speak self-signed HTTPS can still reach `/mcp` over plain HTTP. With TLS off (typical desktop case) only the primary port is bound. The split is structural for the LAN+mobile case; not a 80/443-mimicry artifact.

**Discovery for clients**:
- Production / desktop shell: daemon serves the client SPA itself, so the client uses `window.location.origin` — **no port coordination needed**.
- Pi voice extension (`extensions/pi-voice/extension.ts`): reads `~/.talky/run/talky-daemon.loopback-port` if present (TLS on), else `~/.talky/run/talky-daemon.port`.
- Vite dev (`client/vite.config.ts`): reads `~/.talky/run/talky-daemon.port` to proxy — daemon must be running before `npm run dev`.
- Mobile LAN: user explicitly pins `network.port: <fixed>` in settings.yaml.

### Local-audio install-extra name: `local_audio`

Already present (`[project.optional-dependencies]` in `pyproject.toml`). Verified by installing talky into a throwaway venv without extras and confirming:
1. `talky --help`, `talky daemon`, `talky profile` all work without pyaudio.
2. `talky say "..."` triggers the existing `_ensure_local_audio_extra()` detect-then-instruct flow, which installs the extra on demand and exits cleanly.

The pattern was already in place from `feat/availability`. No further extraction needed — the work was already done.

## Landmines hit

1. **`alternate-clients` was 160+ files behind main.** It predates the `server/` → `src/talky/` repackaging on main. A merge produced massive conflicts but the actual interesting commits (6 of them, all under `desktop/`) were additive. Solved by cherry-picking those 6 commits onto a fresh branch off main instead of attempting the merge.

2. **Daemon writes port runfile before lifespan is ready.** With a fresh `$HOME` (test isolation), warmup downloads the STT model on first run, which takes ~30s before uvicorn binds. The port file appears immediately (allocated in `main()` before `uvicorn.run()`) but the port isn't actually bound yet. Shells must poll for `~/.talky/run/talky-daemon.ready` — not just the port file — before connecting. This is documented in the Tauri shell flow (`daemon_is_ready()` checks both).

3. **Vite config now eager-fails without a daemon runfile.** If you run `npm run dev` before starting the daemon, vite errors out. That's intentional — there's no useful default to fall back to. But it's a small DX regression and worth documenting in `client/README.md`.

## DX gaps surfaced during the spike

These came up while debugging — left as follow-up tickets:

1. **No single "where's the daemon and is it ok" command.** Today I had to combine `lsof -ti :PORT`, `ls ~/.talky/run/`, `cat ~/.talky/run/talky-daemon.port`, `tail ~/.talky/run/talky-daemon.log` to figure out daemon state. With random ports, `lsof` by port is moot. Want: `talky status --json` that prints `{ ready, pid, port, loopback_port, profile, voice }` from the runfiles + `/api/ready`. There's a `cmd_talkystatus` but it doesn't surface this shape.

2. **Daemon failures before logging initializes are invisible.** During the random-port test, the daemon exited with no stdout/stderr and no log file. The bash output file was zero bytes. Want: a stub log file written from the *very first* line of `__main__.py`'s `main()`, before any imports that might fail; or at minimum a wrapper in `cmd_daemon`'s foreground path that captures the subprocess's stderr to disk regardless.

3. **Tool-venv ↔ project-venv drift after pyproject edits.** Per the existing CLAUDE.md note. The implication for distribution: any code path that runs in a fresh `uv tool install` venv must be tested there explicitly. I caught the local-audio extra working only by spinning up a throwaway venv; the project venv had pyaudio already and would have masked a regression.

## Open follow-ups (not done in this spike)

- Actually sign + notarize a Tauri build (needs Apple Developer ID + creds).
- Wire a hosted version of `install-talky.sh` at a pinned commit / CDN.
- Replace the Terminal.app bootstrap fallback with a native progress UI (parse `::stage::` lines from the shell).
- Update `client/README.md` to note the daemon-must-be-running prereq for `npm run dev`. *(done in phase 3)*
- Cross-platform bootstrap (PowerShell variant for Windows). Out of scope per the ticket — mac-only.
- **Homebrew cask.** Decided not to scaffold in this spike — needs a stable GH Releases artifact URL + sha256, neither of which exists yet, so a checked-in `talky.rb` would be mostly placeholders. Sketch for when the release pipeline is ready:
  - Tap repo at `WinstonFassett/homebrew-talky` (separate from this repo so cask updates don't churn here).
  - Cask installs `Talky.app` from a tagged GH Release; depends on a `talky` formula or a post-install `uv tool install talky`.
  - User flow: `brew tap WinstonFassett/talky && brew install --cask talky`.
  - Homebrew strips quarantine on cask installs → no right-click-open ceremony.
  - Decision deferred: bundle the CLI install into the cask, or ship the cask `.app`-only and require a separate `brew install talky-cli` formula. The latter aligns better with the "thin shell + bootstrap" architecture but doubles the user's tap commands.

## Phase 3 additions (2026-06-06)

Picked up after phases 1 + 2:

1. **BootstrapNeeded path actually exercised** with a hidden talky binary (commit `fe0d30a`). Three real bugs found and fixed:
   - osascript failures were silently swallowed (splash claimed "Installing Talky…" with no Terminal window); now captured + surfaced with a copy-paste curl fallback.
   - Default bootstrap URL had wrong-case owner (`winstonfassett/talky` → 404 on raw.githubusercontent.com, which is case-sensitive); fixed to `WinstonFassett/talky`.
   - URL pinned to `main` blocks spike-branch dogfood; added `TALKY_BOOTSTRAP_URL` build-time override.
   - End-to-end verified: fresh `$HOME`, hidden PATH → splash → Terminal opens → curl|bash → all 6 bootstrap stages succeed.
   - **Caveat:** Terminal inherits the user's real shell env when spawned by osascript, so the recipe doesn't simulate a true fresh-account state. For that, use a separate user account or container.

2. **Venv drift checker** at `scripts/check-venv-drift.py` (commit `78b9886`). Compares the global `talky` tool venv against the project `.venv`; flags version mismatches with critical-package highlighting. Exit codes 0/1/2 for tooling integration. Initial run surfaced 12 mismatches in the current state — none critical, but proves the drift is real.

3. **README rewrites** (commit `ba813ff`):
   - `desktop/tauri/README.md` — was stale (described the Zig shell). Replaced end-to-end with the actual Tauri shell story: daemon-discovery flow, build commands, bootstrap URL override, BootstrapNeeded test recipe, ad-hoc sign + right-click-open ceremony.
   - `client/README.md` — new, documents the vite-needs-daemon-runfile prereq.

## Files touched

Port redo:
- `src/talky/server/__main__.py` — rename keys, hard-break on old ones, write port runfiles
- `src/talky/cli.py` — rewrite port resolution (env → settings → runfile), drop legacy
- `src/talky/shared/client_launcher.py` — check ready file instead of sniffing a fixed port
- `src/talky/config/defaults/settings.yaml` — new key names, commented out (random by default)
- `extensions/pi-voice/extension.ts` — read runfile instead of hardcoded port
- `client/vite.config.ts`, `client/vite.config.https.ts` — read runfile for proxy target
- `~/.talky/settings.yaml` — migrated in place (backup at `.pre-spike.bak`)

Bootstrap:
- `scripts/bootstrap/install-talky.sh` — new
- `desktop/tauri/src-tauri/src/lib.rs` — daemon discovery + spawn + bootstrap fallback
- `desktop/tauri/src-tauri/Cargo.toml` — add `libc` for pid liveness check
- `desktop/tauri/src-tauri/tauri.conf.json` — codesigning scaffolding (no creds committed)
- `desktop/tauri/src-tauri/entitlements.plist` — network.server + cs.disable-library-validation
- `desktop/tauri/dist/index.html` — splash UI with spinner

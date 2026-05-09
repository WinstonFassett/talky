# CLAUDE.md

## Commands

```bash
uv tool install --editable . --python 3.12   # install/update global `talky` binary
uv sync                                      # sync project venv (dev + after dep changes)
talky daemon                          # ensure the daemon is running (listens on 9090)
talky <profile>                       # e.g. `talky openclaw` — ensures daemon and switches its active LLM
talky profile [<name>]                # explicit daemon profile control (list / switch)
talky kill                            # reclaim port 9090
uv run ruff check . && uv run pyright
```

### Desktop shells (desktop/)

Two candidates under `desktop/` — bake-off in progress:

```bash
# zero-native shell
git submodule update --init desktop/zero-native/vendor/zero-native
cd desktop/zero-native && zig build package
open desktop/zero-native/zig-out/package/talky-shell-0.1.0-macos-Debug.app

# tauri shell — see src-tauri/ (existing)
```

zero-native requires: Zig 0.16+, `zero-native` CLI (`npm install -g zero-native`).
`desktop/zero-native/vendor/zero-native` tracks `talky-integration` branch (mic permission, setSinkId shim).

After changing pyproject.toml deps: `uv sync && uv tool install --editable . --force --python 3.12`. Both venvs must stay in sync — the tool venv resolves independently from pyproject.toml, not from the lockfile. Pin critical deps with `==`.

## Architecture

One unified daemon. There is no legacy standalone bot anymore (ripped in 5098).

- **talky daemon** — `talky daemon` runs a single process on `:9090` that embeds the WebRTC handler, serves `client/dist/`, hosts FastMCP tools, and owns the in-process voice pipeline. The pipeline includes an `LLMSwitcher` whose slot contains `MCPDriverLLMService` (null passthrough — the default) plus every configured LLM backend. Switch via `talky profile openclaw` or the shortcut `talky openclaw`. Any daemon-dependent command auto-spawns the daemon if it isn't already running (`ensure_mcp_daemon()`, 9d02 / d239c5d). This is the 58db / ea77 / c3a1 architecture.

Pipeline shape: `Mic → VAD → STT → LLMSwitcher → TTS → Speaker`. The switcher routes frames to whichever LLM is active. `MCPDriverLLMService` consumes `LLMContextFrame` by pushing the latest user message onto the daemon's speech queue (read by `convo_listen`) and passes injected `LLMTextFrame`s through to TTS. Real LLMs (openclaw, moltis, etc.) run inference against their own remote sessions.

- `server/` — voice daemon (local audio `talky say` / `talky ask`), LLM backends, transcribe, voice client
- `mcp-server/` — talky daemon entry point, in-process voice channel (`channel.py`), FastMCP tools, embedded WebRTC handler, profile switching
- `skills/talky-skill/` — agent skill for voice prompt mode + voice conversation

Config: `~/.talky/*.yaml` + `credentials/*.json`. No .env.

LLM backends in `server/backends/` extend Pipecat's `LLMService`.

### Voice daemon (local audio)

`server/voice_daemon.py` — always-on daemon for local audio TTS+STT. Auto-starts on first `talky say` or `talky ask`. Communicates via unix socket at `/tmp/talky_voice_daemon.sock`. Separate from the talky daemon (two daemons, 9d02 unification deferred).

### Talky daemon

`talky daemon` runs a unified process on port 9090. The voice pipeline is **in-process** on uvicorn's event loop — there is no separate pipecat child. Restart to pick up code changes: `talky kill && talky daemon`. `talky kill` reclaims 9090 via PID-by-port.

**Do not** use `pkill -f "talky daemon"` — it only matches the parent and can orphan children. **Do not** run via `uv run talky daemon` either — the `uv run` wrapper inserts an intermediate process that breaks process-group signal delivery. Run `talky daemon` directly.

If another daemon is already running on 9090, the new one refuses to start with a clear error. To take over: `talky daemon --force` (or `TALKY_MCP_FORCE=1 talky daemon`).

### Profile switching

When the daemon is running and a browser peer is connected:

```bash
talky profile                  # list available profiles, show active
talky profile openclaw         # switch to openclaw
talky openclaw                 # shortcut: same as `talky profile openclaw`
talky profile __mcp__          # switch to the null MCP passthrough (so an MCP agent can drive via convo_speak)
talky __mcp__                  # shortcut
```

Switching uses `ManuallySwitchServiceFrame` — the transport stays connected, only the active LLM changes. No new pipeline, no peer disconnect. The soft switch (3769fe4) works even before the pipeline is live — the profile is remembered and applied at pipeline build.

Ports: 9090 is the only port. 7860 and 5173 are dead (ripped in 5098).

## Debugging

- Voice daemon: run with `--foreground` to see logs.
- Talky daemon logs are in `~/.talky/run/talky-daemon.log` (persistent across restarts, already captures everything). **Read that file** — don't redirect a fresh daemon's output to a side file unless you have a reason.
- If you're restarting the daemon from an agent's Bash tool, use `run_in_background=true` with a plain `talky daemon` command. **Do not** try to manually background it with `&` plus a redirect, especially not inside a compound chain like `talky kill && talky daemon > log 2>&1 &`. That shape hangs the Bash tool: `&` backgrounds the whole chain into a subshell, but `> log 2>&1` is scoped only to `talky daemon` — the subshell itself still holds the inherited stdout/stderr pipes open, so the tool never sees EOF and waits forever. The symptom from a voice session is total silence for minutes, because the agent is stuck in the Bash call. If you genuinely need manual backgrounding from one shell line, wrap the group so the redirect covers the subshell too: `(talky kill && sleep 1 && talky daemon) > /tmp/talky_daemon.log 2>&1 &`. But prefer `run_in_background=true`.
- Check logs first before forming hypotheses.

## Conventions

- uv only (no pip/venv)
- YAML config, JSON credentials
- `AGENTS.md` → this file

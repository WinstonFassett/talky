# Changelog

All notable changes to Talky are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased] — changes since v0.1.0

Range: [`v0.1.0...main`](https://github.com/WinstonFassett/talky/compare/v0.1.0...main) — 471 commits, 301 files, 2026-02-20 to 2026-10-18. v0.1.0 is the only tag and the only GitHub release. `pyproject.toml` reads `0.1.2.dev0`; `0.1.1` was bumped in [953bcd2](https://github.com/WinstonFassett/talky/commit/953bcd2) but never tagged.

Citations: `#N` is a merged PR. Bare hashes are direct-to-main commits that never had a PR.

### Breaking

- **OpenClaw backend removed.** `src/talky/backends/openclaw.py`, `docs/integrations/openclaw.md`, its profile and its CLI shortcut (`talky openclaw`) are gone. ([#15](https://github.com/WinstonFassett/talky/pull/15))
- **`talky mcp` renamed to `talky daemon`**, with no alias. `TALKY_MCP_FORCE` became `TALKY_DAEMON_FORCE` (the old name is still read as a fallback). The log moved from `~/.talky/run/mcp-daemon.log` to `~/.talky/run/talky-daemon.log`. [4ffcd42](https://github.com/WinstonFassett/talky/commit/4ffcd42)
- **Ports changed.** v0.1.0 served on `7860` (bot) and `5173` (client). Now: `local_port` is plain HTTP on `127.0.0.1` (default `8765`), always bound. `remote_port` is HTTPS (conventional `8764`), bound only when a TLS cert resolves. The intermediate `9090` and `19080`/`19443` values are superseded. [7ff44fe](https://github.com/WinstonFassett/talky/commit/7ff44fe)
- **Remote listener binds the tailnet IP by default.** `network.host` now defaults to `tailscale` (this machine's tailnet IPv4) instead of all interfaces. Set `0.0.0.0` to restore LAN exposure, which has no access control. ([#13](https://github.com/WinstonFassett/talky/pull/13))
- **MCP tools `end_convo` and `leave_convo` removed.** Replaced by `request_leave(agent_id, grace_seconds)`, which plays the profile's `signoff` first. [c671a32](https://github.com/WinstonFassett/talky/commit/c671a32)
- **Layout and entry point.** The `server/` + `mcp-server/` workspaces and the top-level `talky_cli.py` collapsed into one `src/talky` package (`talky = "talky.cli:main"`). The legacy standalone bot path (`server/bot.py`, `main.py`) was removed. [146b377](https://github.com/WinstonFassett/talky/commit/146b377), [c75e713](https://github.com/WinstonFassett/talky/commit/c75e713)
- **Claude backend SDK swap.** The `claude-code` extra now pins `claude-agent-sdk==0.2.165`, replacing the deprecated `claude-code-sdk`. ([#7](https://github.com/WinstonFassett/talky/pull/7))
- **Pipecat pinned to 0.0.102 and Python `<3.13`.** [60e0533](https://github.com/WinstonFassett/talky/commit/60e0533)
- **Dependencies and config.** `uv.lock` is no longer tracked. `.env.example` was deleted. User config now lives in `~/.talky/` (voice profiles, `llm-backends.yaml`, `credentials/`). [29c61f3](https://github.com/WinstonFassett/talky/commit/29c61f3)

### Added

**LLM backends** (v0.1.0 had OpenClaw, Moltis and Pi only)
- Claude Code backend (Agent SDK) with permission prompts, reasoning and tool events, and resume. [a5a5c09](https://github.com/WinstonFassett/talky/commit/a5a5c09), [af685fa](https://github.com/WinstonFassett/talky/commit/af685fa)
- Hermes (NousResearch) backend with steer mode, session resume and approvals wired into the permission UI. [5e46dab](https://github.com/WinstonFassett/talky/commit/5e46dab), [4dca74c](https://github.com/WinstonFassett/talky/commit/4dca74c), [3571e81](https://github.com/WinstonFassett/talky/commit/3571e81)
- Opencode (sst/opencode) backend with a permission-ask flow. [e9fae5b](https://github.com/WinstonFassett/talky/commit/e9fae5b)
- Devin backend driving `devin acp` over stdio, with barge-in via `session/cancel`, `session/load` resume and `--cloud` relay. ([#2](https://github.com/WinstonFassett/talky/pull/2), [5372a75](https://github.com/WinstonFassett/talky/commit/5372a75))
- Pi rebuilt as an interruptible RPC backend with tool and thinking events. [8e6adc4](https://github.com/WinstonFassett/talky/commit/8e6adc4)
- `echo` and `script` test backends. ([#8](https://github.com/WinstonFassett/talky/pull/8))

**Conversations and sessions**
- Listable, resumable conversations across all backends, a `ConvoStore` (uuid ids, `0700`/`0600` modes), and `talky convos list|new|show`. ([#5](https://github.com/WinstonFassett/talky/pull/5))
- Sessions sidebar: browse, search, rename, pin, archive, delete, reopen and continue past conversations. Adds the `/api/convos` API and `convoChanged` SSE events. ([#6](https://github.com/WinstonFassett/talky/pull/6))
- `talky launch --resume/-r`, profile-switch auto-greeting, and a pipeline that survives browser disconnect/reconnect. [0ad30ed](https://github.com/WinstonFassett/talky/commit/0ad30ed), [4bdf4c7](https://github.com/WinstonFassett/talky/commit/4bdf4c7), [beb234e](https://github.com/WinstonFassett/talky/commit/beb234e)
- Idle-TTL for empty rooms. [c5d1462](https://github.com/WinstonFassett/talky/commit/c5d1462)
- Transcript export to md/jsonl/csv. [8cebfb2](https://github.com/WinstonFassett/talky/commit/8cebfb2)

**Voice**
- TTS: Cartesia, ElevenLabs, and Microsoft Edge Read Aloud (free, no key). STT: AssemblyAI and Google. [ee2b046](https://github.com/WinstonFassett/talky/commit/ee2b046), [37f6c12](https://github.com/WinstonFassett/talky/commit/37f6c12), [9fb2a09](https://github.com/WinstonFassett/talky/commit/9fb2a09), [a6c3828](https://github.com/WinstonFassett/talky/commit/a6c3828), [8a06da2](https://github.com/WinstonFassett/talky/commit/8a06da2)
- Live voice-profile switching, including across providers. [fcdd32b](https://github.com/WinstonFassett/talky/commit/fcdd32b)
- A voice daemon with `talky ask`, `talky say`, `talky transcribe`, `talky kill` and MCP voice tools. [b174400](https://github.com/WinstonFassett/talky/commit/b174400), [8de2fdf](https://github.com/WinstonFassett/talky/commit/8de2fdf), [a72d202](https://github.com/WinstonFassett/talky/commit/a72d202)
- Audio cues (three-beep, drop cue) and the `audio_cues.enabled` flag. [226e4d0](https://github.com/WinstonFassett/talky/commit/226e4d0), [dacf74d](https://github.com/WinstonFassett/talky/commit/dacf74d)
- Voice Provider Status and Backend Status gates. [5c28a61](https://github.com/WinstonFassett/talky/commit/5c28a61), [57e795b](https://github.com/WinstonFassett/talky/commit/57e795b)

**CLI**
- `talky auth` (credential TUI), `talky certs`, `talky status --json`, `talky profile`, `talky voice`, `talky resume`, `talky convos`. [5a8f0e3](https://github.com/WinstonFassett/talky/commit/5a8f0e3), [c9f1cc3](https://github.com/WinstonFassett/talky/commit/c9f1cc3), [21d7a3e](https://github.com/WinstonFassett/talky/commit/21d7a3e), [32feadb](https://github.com/WinstonFassett/talky/commit/32feadb)
- Auto-install of provider extras and PortAudio detection. `--log-level`, automatic port management and daemon auto-spawn. [035c768](https://github.com/WinstonFassett/talky/commit/035c768), [5586050](https://github.com/WinstonFassett/talky/commit/5586050), [d239c5d](https://github.com/WinstonFassett/talky/commit/d239c5d)

**Web client** (rebuilt: React/Vite, Tailwind v4, shadcn)
- v5 design system, flowed transcript, reasoning/thinking collapsibles, markdown via Streamdown with karaoke highlighting, and slash palette. [066577d](https://github.com/WinstonFassett/talky/commit/066577d), [fa7e575](https://github.com/WinstonFassett/talky/commit/fa7e575), [a598837](https://github.com/WinstonFassett/talky/commit/a598837)
- Mic/speaker pickers, backend and voice pickers (REST+SSE), mobile Settings sheet, permission banner, steer-mode chip, text input. [08e90e9](https://github.com/WinstonFassett/talky/commit/08e90e9), [af9ef04](https://github.com/WinstonFassett/talky/commit/af9ef04), [188c372](https://github.com/WinstonFassett/talky/commit/188c372)
- Remote daemon access via `VITE_DAEMON_URL` and `dev:https`. [7e3a388](https://github.com/WinstonFassett/talky/commit/7e3a388)
- Bot visualizer, custom favicon. [f6cc707](https://github.com/WinstonFassett/talky/commit/f6cc707), [2479614](https://github.com/WinstonFassett/talky/commit/2479614)

**Packaging and desktop**
- The wheel bundles the built client, the skill and the Pi extension (`extensions/pi-voice`). `scripts/bootstrap/install-talky.sh`. [9584c78](https://github.com/WinstonFassett/talky/commit/9584c78), [dc7192d](https://github.com/WinstonFassett/talky/commit/dc7192d), [b479e4e](https://github.com/WinstonFassett/talky/commit/b479e4e)
- Tauri 2 desktop shell with bootstrap progress UI, app icon, and routing of voice convos into `Talky.app` when installed. A zero-native WKWebView shell also exists. [b9c473d](https://github.com/WinstonFassett/talky/commit/b9c473d), [099c3cd](https://github.com/WinstonFassett/talky/commit/099c3cd), [1d4dab3](https://github.com/WinstonFassett/talky/commit/1d4dab3), [b43cc59](https://github.com/WinstonFassett/talky/commit/b43cc59)

**Remote access**
- HTTPS support with `talky certs`, `allowedHosts` and `external_host`, Tailscale hostname handling. [f4fe5d1](https://github.com/WinstonFassett/talky/commit/f4fe5d1), [3a33903](https://github.com/WinstonFassett/talky/commit/3a33903)

**Skill**
- The `talky` skill (renamed from `talky-skill`) with the Executive Assistant posture, standby mode and ambient awareness. [165222a](https://github.com/WinstonFassett/talky/commit/165222a), [1d8fffa](https://github.com/WinstonFassett/talky/commit/1d8fffa), [f214993](https://github.com/WinstonFassett/talky/commit/f214993)

### Changed

- LLM backends start lazily: only the active profile's backend is built per call, and others are added to the running switcher on first switch (`LazyLLMSwitcher`). ([#11](https://github.com/WinstonFassett/talky/pull/11))
- Devin backend: no ACP `authenticate` (it opened a browser login tab). It injects `WINDSURF_API_KEY` from the Devin credentials file into the child's isolated `HOME`. A failed login fails once with a clear message, and respawns back off (10 s doubling, stop after 5). ([#9](https://github.com/WinstonFassett/talky/pull/9))
- Hermes uses the resolved runtime provider and persists/restores sessions. Opencode handles `message.part.delta`. ([#16](https://github.com/WinstonFassett/talky/pull/16))
- Opencode default model is now `big-pickle`. ([#17](https://github.com/WinstonFassett/talky/pull/17))
- Profiles renamed to `<agent>` / `<agent>-tui`, autoconnect on by default. [f07345c](https://github.com/WinstonFassett/talky/commit/f07345c)
- No more hardcoded Pi provider/model; Pi uses its own defaults. [7b1e006](https://github.com/WinstonFassett/talky/commit/7b1e006)
- LLMContext aggregation dropped from the voice pipeline. [fc51414](https://github.com/WinstonFassett/talky/commit/fc51414)
- Daemon is MIT-licensed per workspace member at the time; `readme` field added for PyPI. [f2fc0a3](https://github.com/WinstonFassett/talky/commit/f2fc0a3), [53590a1](https://github.com/WinstonFassett/talky/commit/53590a1)

### Fixed

- A failed turn is now reported as `backendError` for claude, hermes and opencode (it was silent). ([#14](https://github.com/WinstonFassett/talky/pull/14))
- MCP mode no longer takes over a session's profile. ([#10](https://github.com/WinstonFassett/talky/pull/10))
- `ClaudeAgentOptions` NameError when the SDK is absent. ([#17](https://github.com/WinstonFassett/talky/pull/17))
- Conformance-floor fixes: thinking indicator keys on first LLM token, stale session-search responses ignored, drafts restore at first paint, client reconnects when the daemon returns, interrupted-turn markers older than 10 min are cleared and resumes are limited to 30 min, and the `script` backend emits `LLMTextFrame`. ([#8](https://github.com/WinstonFassett/talky/pull/8), [#12](https://github.com/WinstonFassett/talky/pull/12))
- Claude replies were silently dropped on the CLI's `rate_limit_event` (the SDK swap removed the monkeypatch). ([#7](https://github.com/WinstonFassett/talky/pull/7))
- `talky kill` no longer kills the calling agent. [560a9dc](https://github.com/WinstonFassett/talky/commit/560a9dc)
- Port-leak and zombie-daemon defences (pid file, `killpg` fan-out, startup port check, zombie recovery). [d073204](https://github.com/WinstonFassett/talky/commit/d073204), [e82f1a4](https://github.com/WinstonFassett/talky/commit/e82f1a4), [d24b7be](https://github.com/WinstonFassett/talky/commit/d24b7be)
- Speech onset detected during the grace window; Pipecat 5-minute idle timeout disabled. [6767b0e](https://github.com/WinstonFassett/talky/commit/6767b0e), [1c96f7c](https://github.com/WinstonFassett/talky/commit/1c96f7c)
- SSL: hostname mismatch for Tailscale, cert validation, external binding. [3a33903](https://github.com/WinstonFassett/talky/commit/3a33903), [99d66a3](https://github.com/WinstonFassett/talky/commit/99d66a3)
- Dependency installer: infinite install loop, Python 3.14 incompatibility, `uv tool` environment handling. [00e2996](https://github.com/WinstonFassett/talky/commit/00e2996), [4bdd91d](https://github.com/WinstonFassett/talky/commit/4bdd91d)
- Daemon startup stderr captured to `~/.talky/run/voice-daemon.log`; `TALKY_LOG_LEVEL` honoured. [6d7a212](https://github.com/WinstonFassett/talky/commit/6d7a212), [ecd2da4](https://github.com/WinstonFassett/talky/commit/ecd2da4)
- OpenClaw device-auth and WS protocol v4 fixes landed in this range before OpenClaw was removed. ([#3](https://github.com/WinstonFassett/talky/pull/3), [947534d](https://github.com/WinstonFassett/talky/commit/947534d))

### Removed

- OpenClaw backend (breaking, see above). ([#15](https://github.com/WinstonFassett/talky/pull/15))
- Amp backend (Amp went paid). [ec14538](https://github.com/WinstonFassett/talky/commit/ec14538)
- Vision/screen-capture processors from the MCP server. [b7a3ab5](https://github.com/WinstonFassett/talky/commit/b7a3ab5)
- Dead Pi extension and `PiLLMService`, replaced by a generic agent-extension LLM service. [4720b85](https://github.com/WinstonFassett/talky/commit/4720b85), [eb53a4c](https://github.com/WinstonFassett/talky/commit/eb53a4c)
- Legacy bot path and shims (see Breaking). [0c44d13](https://github.com/WinstonFassett/talky/commit/0c44d13)

### Internal

- **Test coverage grew from 7 test files to a unit suite plus an E2E rig.** A browser WebRTC rig with fake media, a steel-thread pipeline test and an interruption test. ([#8](https://github.com/WinstonFassett/talky/pull/8), [7c97dd4](https://github.com/WinstonFassett/talky/commit/7c97dd4), [96c94a1](https://github.com/WinstonFassett/talky/commit/96c94a1))
- Basic conformance floor (`tests/conformance/basic`): 49 pass, 0 fail, 7 n/a, per the #12 commit log. `TALKY_ONLY_BACKENDS` allowlist keeps the rig from starting real agents (it had started 102 Devin sessions). ([#8](https://github.com/WinstonFassett/talky/pull/8), [#12](https://github.com/WinstonFassett/talky/pull/12))
- Docs: `PRODUCT.md`, `DESIGN.md`, `LOGGING.md`, `UBIQUITOUS_LANGUAGE.md`, per-backend integration docs, ports doc.
- Code-health passes (desloppify), distribution-parity spike notes, `scripts/check-venv-drift.py`.

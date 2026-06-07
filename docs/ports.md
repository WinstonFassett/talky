# Ports

The canonical truth for how the talky daemon binds and how clients find it.

## Two listeners, named by reach

The daemon serves **one collapsed app** — the web client (`/*`), the API
(`/api`, `/ws`, `/start`), and MCP (`/mcp`) all live on the same Starlette app.
Client-vs-server is a route distinction, never a port one. That one app is
exposed through up to two listeners, named by who can reach them:

| Listener | Binds | Protocol | Bound when | Job |
|----------|-------|----------|-----------|-----|
| **`local_port`** | `127.0.0.1` | HTTP | **always** | The local browser and local MCP clients (Claude Code, etc.) — they always have a plain-HTTP path. |
| **`remote_port`** | `host` (e.g. `0.0.0.0`) | HTTPS | only when a TLS cert resolves | Off-box reach: LAN, mobile, ngrok, tailnet. Browsers require HTTPS for mic access over a network. |

The local HTTP listener is **always on**, so a plain-HTTP path is always
available locally. HTTPS is purely *additive* for remote reach — turning on TLS
never takes the simple local path away.

## Defaults & pinning

`local_port` defaults to **8765** (pinned) so a static MCP config and bookmarks
have a stable target. `remote_port` is **random** unless pinned, and only binds
when a cert is configured.

Pin or change ports in `~/.talky/settings.yaml`:

```yaml
network:
  host: "0.0.0.0"          # remote (HTTPS) bind host
  local_port: 8765         # always-on local HTTP. null/omit → random free port.
  remote_port: 8764        # HTTPS listener. omit → random (when a cert resolves).
  https:                   # uncomment to serve the remote HTTPS listener
    cert: "~/.talky/ssl/server-cert.pem"   # run `talky certs` first
    key:  "~/.talky/ssl/server-key.pem"
```

A **pinned** port that's already held = the daemon refuses to start (loud), not
a silent random fallback. You pinned it; it's honored or it errors.

When a port is left random, the chosen value is written to a runfile so shells,
openers, and the desktop shell can discover it:

```
~/.talky/run/talky-daemon.local-port    # always written
~/.talky/run/talky-daemon.remote-port   # only when the HTTPS listener binds
~/.talky/run/talky-daemon.ready          # pid, written after bind + startup
```

```bash
cat ~/.talky/run/talky-daemon.local-port
```

Clients should discover the port via the runfile rather than assuming a value.

## Settings / env reference

| Setting | Env override | Default |
|---------|-------------|---------|
| `network.host` | `TALKY_HOST` | `localhost` (`0.0.0.0` for remote) |
| `network.local_port` | `TALKY_LOCAL_PORT` | `8765` (pinned) |
| `network.remote_port` | `TALKY_REMOTE_PORT` | random free port (HTTPS only) |
| `network.https.cert` | `TALKY_HTTPS_CERT` | none (TLS off → no remote listener) |
| `network.https.key` | `TALKY_HTTPS_KEY` | none (TLS off → no remote listener) |

### Retired (hard break)

The daemon **refuses to start** if any of these are present — no aliasing, no
migration hint. Rename to `local_port` / `remote_port`:

- settings: `network.port`, `network.https_port`, `network.http_port`, `network.loopback_port`
- env: `MCP_*`, `TALKY_PORT`, `TALKY_HTTP_PORT`, `TALKY_LOOPBACK_PORT`

## For client / extension authors

- **Browser / desktop shell:** the daemon serves the client SPA, so the client
  uses `window.location.origin` — no port coordination needed.
- **Local MCP clients (Claude Code):** point at the local HTTP listener,
  `http://localhost:<local_port>/mcp` (8765 by default). `talky claude`
  registers it automatically at the live port.
- **Pi voice extension:** reads `~/.talky/run/talky-daemon.local-port`.
- **Vite dev:** reads the local-port runfile to proxy — start the daemon before
  `npm run dev`.

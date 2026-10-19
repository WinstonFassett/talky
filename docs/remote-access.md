# Remote Access

Use Talky from other devices on your network (e.g. phone over Tailscale).

Remote access reaches the daemon's **remote HTTPS listener** — the `remote_port` (conventionally **8764**), which binds on `network.host` (default `tailscale` = this machine's tailnet IPv4 only) and only comes up when a TLS cert resolves. The always-on local HTTP listener (`local_port`, 8765) is loopback-only and not used for remote access. See [docs/ports.md](ports.md) for the full two-listener model.

To reach the daemon remotely you need (a) the remote listener bound to an address the other device can reach — the shipped default, `network.host: tailscale`, binds only the machine's tailnet IPv4 (resolved with `tailscale ip -4`; the daemon refuses to start if Tailscale is down or has no IPv4), so no LAN exposure, (b) HTTPS (browsers require it for mic access on non-localhost URLs), and (c) a cert the browser will accept.

## Production flow (remote HTTPS listener)

1. **Generate SSL certificates** with your external hostname as SAN:
   ```bash
   talky certs --hostname macbook-pro.tailc3138.ts.net
   ```
   (Wheel-friendly — no repo checkout needed. The legacy `./scripts/generate-certs.sh`
   still works for repo dev, but is deprecated for one release.)

2. **Configure `~/.talky/settings.yaml`** (one-time):
   ```yaml
   network:
     host: "tailscale"   # default: tailnet IPv4 only
     remote_port: 8764
     https:
       cert: "~/.talky/ssl/server-cert.pem"
       key:  "~/.talky/ssl/server-key.pem"
   ```

3. **Start the daemon** — it picks up the config automatically:
   ```bash
   talky daemon
   ```

   Or override for a single run with env vars (`TALKY_HOST` overrides `network.host`):
   ```bash
   TALKY_HTTPS_CERT=~/.talky/ssl/server-cert.pem \
     TALKY_HTTPS_KEY=~/.talky/ssl/server-key.pem \
     talky daemon
   ```

4. **Open on the remote device:**
   ```
   https://macbook-pro.tailc3138.ts.net:8764
   ```
   Accept the self-signed cert warning, or install the cert on the device.

## Binding `0.0.0.0` (explicit opt-in — not recommended)

`network.host: "0.0.0.0"` (or `TALKY_HOST=0.0.0.0`) binds the remote HTTPS listener on **all interfaces**. That exposes the daemon beyond the tailnet — to your LAN and anything else that can route to this machine. There is **no access control**: no bearer token or login is implemented (WIN-555 shipped tailnet-only binding as the sole protection), so anyone who can reach the port can drive the daemon. Only do this on a network you fully trust.

## Dev client flow (5173 → 8764)

For hot-reload dev against a remote daemon. The Vite dev server proxies API calls to the daemon.

**One-time setup** — trust the server cert in macOS login keychain so Node's proxy accepts it:
```bash
security add-trusted-cert -d -r trustRoot \
  -k ~/Library/Keychains/login.keychain-db \
  ~/.talky/ssl/server-cert.pem
```

**Start dev server pointing at remote daemon:**
```bash
cd client
VITE_HOST=0.0.0.0 \
  VITE_ALLOWED_HOSTS="macbook-pro.tailc3138.ts.net,localhost" \
  VITE_DAEMON_URL="https://macbook-pro.tailc3138.ts.net:8764" \
  npm run dev:https
```

The `dev:https` script uses `NODE_OPTIONS=--use-system-ca` so Node trusts your login keychain.

## Troubleshooting

**"Site not reachable"**
```bash
lsof -i :8764   # remote HTTPS listener should bind <tailnet-ip>:8764 (or 0.0.0.0:8764 if you opted in), not 127.0.0.1
```
The remote HTTPS listener binds `network.host` on `:8764` (by default the tailnet IPv4); the always-on local HTTP listener separately binds `127.0.0.1:8765`. If `:8764` isn't listening, no TLS cert resolved (so the remote listener never came up).

**"HTTPS required" / mic not working**
- Must use HTTPS, not HTTP, from non-localhost URLs

**Vite proxy error: self-signed certificate**
- Run the one-time `security add-trusted-cert` step above
- Cert must be regenerated with the correct hostname SAN (`talky certs --hostname <name>`)

**Browser cert warning**
- Self-signed certs show a warning — click Advanced → Proceed, or install the cert on the device

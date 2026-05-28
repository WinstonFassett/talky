# Remote Access

Use Talky from other devices on your network (e.g. phone over Tailscale).

One port: **9090**. The daemon serves WebRTC, the browser UI, and MCP tools. To reach it remotely you need (a) the daemon bound to `0.0.0.0`, (b) HTTPS (browsers require it for mic access on non-localhost URLs), and (c) a cert the browser will accept.

## Production flow (9090 only)

1. **Generate SSL certificates** with your external hostname as SAN:
   ```bash
   ./scripts/generate-certs.sh macbook-pro.tailc3138.ts.net
   ```

2. **Configure `~/.talky/settings.yaml`** (one-time):
   ```yaml
   network:
     host: "0.0.0.0"
     port: 9090
     https:
       cert: "~/.talky/ssl/server-cert.pem"
       key:  "~/.talky/ssl/server-key.pem"
   ```

3. **Start the daemon** — it picks up the config automatically:
   ```bash
   talky daemon
   ```

   Or override for a single run with env vars:
   ```bash
   TALKY_HOST=0.0.0.0 \
     TALKY_HTTPS_CERT=~/.talky/ssl/server-cert.pem \
     TALKY_HTTPS_KEY=~/.talky/ssl/server-key.pem \
     talky daemon
   ```

4. **Open on the remote device:**
   ```
   https://macbook-pro.tailc3138.ts.net:9090
   ```
   Accept the self-signed cert warning, or install the cert on the device.

## Dev client flow (5173 → 9090)

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
  VITE_DAEMON_URL="https://macbook-pro.tailc3138.ts.net:9090" \
  npm run dev:https
```

The `dev:https` script uses `NODE_OPTIONS=--use-system-ca` so Node trusts your login keychain.

## Troubleshooting

**"Site not reachable"**
```bash
lsof -i :9090   # Should show *:9090 or 0.0.0.0:9090, not 127.0.0.1:9090
```

**"HTTPS required" / mic not working**
- Must use HTTPS, not HTTP, from non-localhost URLs

**Vite proxy error: self-signed certificate**
- Run the one-time `security add-trusted-cert` step above
- Cert must be regenerated with the correct hostname SAN (`generate-certs.sh <hostname>`)

**Browser cert warning**
- Self-signed certs show a warning — click Advanced → Proceed, or install the cert on the device

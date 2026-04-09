# Remote Access

Use Talky from other devices on your network.

> **Note:** This doc was written for the pre-daemon architecture (Vite on :5173 + pipecat on :7860) and is **stale** after 5098. The current talky daemon is a single process on :9090 that serves WebRTC, the browser UI, and MCP tools all from one port. The remote-access story needs to be re-thought against the daemon — the bits below are kept as rough guidance only.

## Current reality (post-5098)

- One port: **9090**. That's the daemon, the WebRTC signaling, the browser UI, and the MCP endpoint.
- To reach it from another device you need (a) the daemon bound to `0.0.0.0` and (b) HTTPS, because browsers won't grant mic permission to plain HTTP over a non-localhost URL.

## Quick start

1. **Generate SSL certificates** (required for WebRTC mic access over non-localhost):
   ```bash
   ./scripts/generate-certs.sh
   ```

2. **Bind the daemon externally** (see `mcp-server/src/pipecat_mcp_server/server.py` for the `MCP_HOST` env var):
   ```bash
   MCP_HOST=0.0.0.0 talky daemon
   ```

3. **Access from another device**:
   ```
   https://YOUR_IP:9090
   ```

## Environment variables

```bash
export MCP_HOST="0.0.0.0"                # Daemon bind host (default: localhost)
export MCP_PORT="9090"                   # Daemon port (default: 9090)
```

## Troubleshooting

**"Site not reachable"**
```bash
lsof -i :9090   # Should show the daemon bound to *:9090 or 0.0.0.0:9090
```

**"HTTPS required"**
```bash
./scripts/generate-certs.sh  # Missing certificates
```

**"Microphone not working"**
- Use HTTPS (not HTTP) when accessing from a non-localhost URL
- Grant browser microphone permissions

## TODO

This doc deserves a proper rewrite once someone actually runs the daemon over the network. File a ticket if you hit rough edges.

# talky client (SPA)

React + Vite single-page app served by the talky daemon. In production
the daemon serves `client/dist/` directly. In development you run vite
alongside the daemon and vite proxies API/WebSocket requests to it.

## Develop

The daemon must be running **before** `npm run dev` — vite reads
`~/.talky/run/talky-daemon.port` to set its proxy target and will
fail to start without it.

```bash
# terminal 1 — daemon
talky daemon

# terminal 2 — vite
cd client && npm run dev
```

Vite's HMR connects to the daemon on whatever port the runfile reports.
If you kill and restart the daemon, vite will pick up the new port on
the next browser reload — no need to restart vite.

## Build

```bash
npm run build
```

Output: `client/dist/`. The daemon picks this up automatically — no
config wiring needed.

## Why no dev port hardcoded

The daemon picks a random ephemeral port on every start (unless
`network.port` is set in `~/.talky/settings.yaml`). Vite reads the
runfile so both stay in sync. See
[docs/distribution-parity-spike.md](../docs/distribution-parity-spike.md)
for the port redo.

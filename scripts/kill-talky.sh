#!/usr/bin/env bash
# kill-talky.sh — reliably free Talky's ports.
#
# Why this exists: `pkill -f "talky mcp"` only kills the parent process.
# The child pipecat and vite processes have their own cmdlines and survive,
# keeping sockets bound. Killing by port reaches whatever is actually holding
# the listener, regardless of name.
#
# Ports:
#   9090 — talky mcp (parent)
#   7860 — pipecat webrtc (child of talky mcp)
#   5173 — vite dev server (child of talky mcp)
#
# Voice daemon unix socket is intentionally NOT touched — its lifecycle is
# separate and well-behaved. If you need to bounce it too, run:
#   pkill -f talky_voice_daemon

set -u

PORTS=(9090 7860 5173)
ANY_KILLED=0

for port in "${PORTS[@]}"; do
  pids=$(lsof -ti:"$port" 2>/dev/null || true)
  if [ -n "${pids:-}" ]; then
    echo "port ${port}: killing ${pids}" | tr '\n' ' '; echo
    # shellcheck disable=SC2086
    kill -9 ${pids} 2>/dev/null || true
    ANY_KILLED=1
  else
    echo "port ${port}: clear"
  fi
done

# Brief settle, then verify nothing snuck back in.
sleep 0.3

FAILED=0
for port in "${PORTS[@]}"; do
  if lsof -ti:"$port" >/dev/null 2>&1; then
    echo "port ${port}: STILL HELD after kill -9" >&2
    FAILED=1
  fi
done

if [ "${FAILED}" -eq 1 ]; then
  exit 1
fi

if [ "${ANY_KILLED}" -eq 0 ]; then
  echo "nothing to kill — all ports were already clear"
fi

exit 0

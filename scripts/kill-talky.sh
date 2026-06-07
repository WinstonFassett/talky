#!/usr/bin/env bash
# kill-talky.sh — brute-force stop the talky daemon by port.
#
# Prefer `talky kill` — it discovers the daemon via its runfiles and stops it
# cleanly. This script is the fallback when the CLI is unavailable: it reads
# the port runfiles and kills whatever is holding each listener.
#
# Why kill by port: `pkill -f "talky daemon"` only matches by cmdline and can
# miss the detached child. Killing by port reaches whatever is actually holding
# the listener, regardless of name.
#
# Voice daemon unix socket is intentionally NOT touched — its lifecycle is
# separate and well-behaved. If you need to bounce it too, run:
#   pkill -f talky_voice_daemon

set -u

RUN_DIR="${HOME}/.talky/run"
ANY_KILLED=0

read_port() {
  # Echo the integer port from a runfile, or nothing.
  local f="${RUN_DIR}/$1"
  [ -f "$f" ] && tr -d '[:space:]' < "$f" || true
}

PORTS=""
for f in talky-daemon.local-port talky-daemon.remote-port; do
  p=$(read_port "$f")
  [ -n "${p:-}" ] && PORTS="${PORTS} ${p}"
done

if [ -z "${PORTS// /}" ]; then
  echo "no port runfiles in ${RUN_DIR} — talky daemon was not running"
  exit 0
fi

for PORT in $PORTS; do
  pids=$(lsof -ti:"$PORT" -sTCP:LISTEN 2>/dev/null || true)
  if [ -n "${pids:-}" ]; then
    echo "port ${PORT}: killing ${pids}" | tr '\n' ' '; echo
    # shellcheck disable=SC2086
    kill -9 ${pids} 2>/dev/null || true
    ANY_KILLED=1
  else
    echo "port ${PORT}: clear"
  fi
done

# Brief settle, then verify nothing snuck back in.
sleep 0.3

for PORT in $PORTS; do
  if lsof -ti:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "port ${PORT}: STILL HELD after kill -9" >&2
    exit 1
  fi
done

if [ "${ANY_KILLED}" -eq 0 ]; then
  echo "nothing to kill — talky daemon was not running"
fi

exit 0

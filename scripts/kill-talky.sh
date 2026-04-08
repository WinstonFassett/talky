#!/usr/bin/env bash
# kill-talky.sh — stop the talky daemon on :9090.
#
# Why this exists: `pkill -f "talky daemon"` only matches by cmdline and
# can miss the detached child. Killing by port reaches whatever is actually
# holding the listener, regardless of name.
#
# Voice daemon unix socket is intentionally NOT touched — its lifecycle is
# separate and well-behaved. If you need to bounce it too, run:
#   pkill -f talky_voice_daemon

set -u

PORT=9090
ANY_KILLED=0

pids=$(lsof -ti:"$PORT" 2>/dev/null || true)
if [ -n "${pids:-}" ]; then
  echo "port ${PORT}: killing ${pids}" | tr '\n' ' '; echo
  # shellcheck disable=SC2086
  kill -9 ${pids} 2>/dev/null || true
  ANY_KILLED=1
else
  echo "port ${PORT}: clear"
fi

# Brief settle, then verify nothing snuck back in.
sleep 0.3

if lsof -ti:"$PORT" >/dev/null 2>&1; then
  echo "port ${PORT}: STILL HELD after kill -9" >&2
  exit 1
fi

if [ "${ANY_KILLED}" -eq 0 ]; then
  echo "nothing to kill — talky daemon was not running"
fi

exit 0

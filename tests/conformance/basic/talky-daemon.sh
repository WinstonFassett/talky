#!/bin/bash
# Run an isolated Talky daemon for the conformance suite: stop | start | restart.
#
#   TALKY_ISO_HOME   a throwaway HOME whose .talky/ holds copies of your *.yaml
#                    and credentials/ (never your real ~/.talky)
#   TALKY_LOCAL_PORT / TALKY_REMOTE_PORT   ports for that daemon (default 8785/8784)
#
#   TALKY_ONLY_BACKENDS   LLM backends the daemon may build (default echo,script).
#                    Every call builds every allowed backend, so without this a
#                    test run would start real devin / hermes / pi sessions.
#
# Starts the repo's own .venv/bin/talky on the `script` profile + `local-test` voice.
set -u
ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
ISO=${TALKY_ISO_HOME:?set TALKY_ISO_HOME}
LP=${TALKY_LOCAL_PORT:-8785}
RP=${TALKY_REMOTE_PORT:-8784}
ONLY=${TALKY_ONLY_BACKENDS:-echo,script}
LOG=$ISO/conformance-daemon.out
TALKY="$ROOT/.venv/bin/talky"

up() { curl -s -m 1 "localhost:$LP/api/profiles" >/dev/null 2>&1; }

stop() {
  # `talky kill` finds the daemon through $HOME/.talky/run, i.e. only this isolated one.
  (cd "$ROOT" && HOME=$ISO TALKY_LOCAL_PORT=$LP TALKY_REMOTE_PORT=$RP "$TALKY" kill >>"$LOG" 2>&1)
  for _ in $(seq 1 60); do up || break; sleep 0.25; done
}

start() {
  up && return 0
  (cd "$ROOT" && HOME=$ISO TALKY_ONLY_BACKENDS=$ONLY TALKY_LOCAL_PORT=$LP TALKY_REMOTE_PORT=$RP nohup "$TALKY" daemon --foreground >>"$LOG" 2>&1 </dev/null &)
  for _ in $(seq 1 240); do
    [ -f "$ISO/.talky/run/talky-daemon.ready" ] && up && break
    sleep 0.5
  done
  curl -s -m 2 -X POST -H 'Content-Type: application/json' -d '{"profile":"script"}' "localhost:$LP/api/profiles/switch" >/dev/null
  curl -s -m 2 -X POST -H 'Content-Type: application/json' -d '{"profile":"local-test"}' "localhost:$LP/api/voices/switch" >/dev/null
}

case "${1:-}" in
  stop) stop ;;
  start) start ;;
  restart) stop; start ;;
  *) echo "usage: $0 stop|start|restart" >&2; exit 2 ;;
esac

#!/usr/bin/env bash
# Isolated Talky daemon + fake T3 for browser drivers (WIN-574).
#
#   rig_daemon.sh start [--port P] [--t3-port Q] [--fake-t3|--real-t3] [--wait-ready]
#   rig_daemon.sh stop | stop-fake-t3 | restart | status | fail-cli on|off
#
# HOME is a PERSISTENT dir ($TALKY_RIG_HOME, default ~/.talky-rig/home), shared by all runs and
# slices, so the ~6 min / 1.9 GB model download happens once. Per-run isolation comes from env
# (T3_TOKEN_FILE, TALKY_CONVOS_DIR, T3_BIN, T3_BASE_URL, TALKY_LOCAL_PORT) pointing into $RUN.
# `stop` kills only the exact recorded PIDs. Never `talky kill`, never pkill, never port 8765.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REAL_HOME="$HOME"
RIG_HOME="${TALKY_RIG_HOME:-$REAL_HOME/.talky-rig/home}"
RUN="${TALKY_RIG_DIR:-$ROOT/.rig}"
mkdir -p "$RIG_HOME" "$RUN"
RIG_HOME="$(cd "$RIG_HOME" && pwd -P)"
if [ "$RIG_HOME" = "$(cd "$REAL_HOME" && pwd -P)" ]; then
  echo "rig_daemon: refusing: TALKY_RIG_HOME resolves to the real \$HOME" >&2; exit 2
fi
RUN="$(cd "$RUN" && pwd -P)"
PIDFILE_IN_HOME="$RIG_HOME/.talky/run/talky-daemon.pid"

log() { echo "rig_daemon: $*" >&2; }
alive() { [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null; }
read_pid() { [ -f "$1" ] && tr -dc '0-9' < "$1" || true; }

# kill exactly PID (SIGTERM, then SIGKILL after 5 s) if it is alive
kill_exact() {
  local pid="$1"
  alive "$pid" || return 0
  kill "$pid" 2>/dev/null || true
  for _ in 1 2 3 4 5 6 7 8 9 10; do alive "$pid" || return 0; sleep 0.5; done
  kill -9 "$pid" 2>/dev/null || true
}

wait_http() { # url timeout_secs
  local url="$1" end=$((SECONDS + $2))
  while [ $SECONDS -lt $end ]; do
    curl -sf -o /dev/null --max-time 2 "$url" && return 0
    sleep 1
  done
  return 1
}

cmd_start() {
  local port=18781 t3port="" mode="" wait_ready=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --port) port="$2"; shift ;;
      --t3-port) t3port="$2"; shift ;;
      --fake-t3) mode=fake ;;
      --real-t3) mode=real ;;
      --wait-ready) wait_ready=1 ;;
      *) log "unknown arg $1"; exit 2 ;;
    esac
    shift
  done
  [ "$port" = 8765 ] && { log "refusing port 8765 (the real daemon)"; exit 2; }
  t3port="${t3port:-$((port + 1))}"
  [ "$t3port" = 8765 ] && { log "refusing port 8765"; exit 2; }
  if alive "$(read_pid "$RUN/daemon.pid")"; then log "rig daemon already running (pid $(read_pid "$RUN/daemon.pid")); stop it first"; exit 1; fi
  if curl -s -o /dev/null --max-time 1 "http://127.0.0.1:$port/"; then log "port $port is already serving something; refusing"; exit 1; fi

  # fresh per-run state (HOME and its models persist)
  rm -rf "$RUN/convos" "$RUN/t3.json" "$RUN/cli.log" "$RUN/fail.flag" "$RUN/daemon.pid" "$RUN/fake_t3.pid"
  mkdir -p "$RUN/convos"
  : > "$RUN/cli.log"
  rm -f "$RIG_HOME/.talky/run/talky-daemon."{pid,ready,lock,local-port,remote-port}

  local env_extra=()
  if [ "$mode" = fake ]; then
    if [ "$t3port" = "$port" ] || curl -s -o /dev/null --max-time 1 "http://127.0.0.1:$t3port/"; then log "fake T3 port $t3port busy"; exit 1; fi
    "$ROOT/.venv/bin/python" "$ROOT/tests/fakes/fake_t3.py" --port "$t3port" > "$RUN/fake_t3.log" 2>&1 &
    echo $! > "$RUN/fake_t3.pid"
    wait_http "http://127.0.0.1:$t3port/" 15 || { log "fake T3 did not start; see $RUN/fake_t3.log"; kill_exact "$(read_pid "$RUN/fake_t3.pid")"; exit 1; }
    env_extra=(T3_BIN="$ROOT/tests/fakes/fake_t3_cli.sh" T3_BASE_URL="http://127.0.0.1:$t3port"
               FAKE_T3_CLI_LOG="$RUN/cli.log" FAKE_T3_FAIL_FLAG="$RUN/fail.flag")
  elif [ "$mode" = real ]; then
    env_extra=(T3_BASE_URL="http://127.0.0.1:3773")  # real T3 + real CLI: read-only drivers only
  fi

  local t0=$SECONDS
  log "starting daemon on :$port (HOME=$RIG_HOME, run=$RUN)"
  # `talky daemon` spawns the daemon detached and prints "failed to come up within 30s" while
  # it is still starting (cold start downloads models): ignore its verdict, poll HTTP ourselves.
  env HOME="$RIG_HOME" TALKY_LOCAL_PORT="$port" T3_TOKEN_FILE="$RUN/t3.json" TALKY_CONVOS_DIR="$RUN/convos" \
      ${env_extra[@]+"${env_extra[@]}"} "$ROOT/.venv/bin/talky" daemon > "$RUN/daemon_cli.log" 2>&1 || true

  # record the exact pid first so `stop` (and the failure paths below) can signal it
  local pid=""
  for _ in $(seq 1 30); do pid="$(read_pid "$PIDFILE_IN_HOME")"; alive "$pid" && break; sleep 1; done
  alive "$pid" || { log "no live daemon pid in $PIDFILE_IN_HOME"; cmd_stop_fake_t3; exit 1; }
  echo "$pid" > "$RUN/daemon.pid"
  wait_http "http://127.0.0.1:$port/api/profiles" 600 || { log "daemon never answered /api/profiles; see $RIG_HOME/.talky/run/talky-daemon.log"; exit 1; }
  echo "$port" > "$RUN/port"
  log "up: /api/profiles after $((SECONDS - t0))s (pid $pid)"
  if [ "$wait_ready" = 1 ]; then
    wait_http "http://127.0.0.1:$port/api/ready" 600 || { log "daemon not /api/ready within 600s"; exit 1; }
    log "ready after $((SECONDS - t0))s"
  fi
}

cmd_stop_fake_t3() {
  local pid; pid="$(read_pid "$RUN/fake_t3.pid")"
  kill_exact "$pid"
  rm -f "$RUN/fake_t3.pid"
}

cmd_stop() {
  local pid; pid="$(read_pid "$RUN/daemon.pid")"
  if [ -n "$pid" ]; then
    # exact recorded PID only; sanity-check it is still a talky process before signalling
    if alive "$pid" && ps -p "$pid" -o command= | grep -qi talky; then kill_exact "$pid"; else log "recorded pid $pid is gone or not talky; not signalling"; fi
  fi
  cmd_stop_fake_t3
  rm -f "$RUN/daemon.pid" "$RIG_HOME/.talky/run/talky-daemon."{pid,ready,lock,local-port,remote-port}
  log "stopped"
}

cmd_status() {
  local pid; pid="$(read_pid "$RUN/daemon.pid")"
  if alive "$pid"; then echo "daemon: up pid=$pid port=$(cat "$RUN/port" 2>/dev/null)"; else echo "daemon: down"; fi
  if alive "$(read_pid "$RUN/fake_t3.pid")"; then echo "fake t3: up"; else echo "fake t3: down"; fi
}

case "${1:-}" in
  start) shift; cmd_start "$@" ;;
  stop) cmd_stop ;;
  stop-fake-t3) cmd_stop_fake_t3 ;;
  restart) shift; cmd_stop; cmd_start "$@" ;;
  status) cmd_status ;;
  fail-cli) case "${2:-}" in on) touch "$RUN/fail.flag" ;; off) rm -f "$RUN/fail.flag" ;; *) echo "fail-cli on|off" >&2; exit 2 ;; esac ;;
  *) echo "usage: $0 start|stop|stop-fake-t3|restart|status|fail-cli on|off" >&2; exit 2 ;;
esac

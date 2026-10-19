#!/bin/sh
# Fake `t3` CLI for the test rig (WIN-574). Never prints or logs a token.
# FAKE_T3_FAIL_FLAG: if that file exists, `auth session issue` fails.
# FAKE_T3_CLI_LOG:   every call appends its argv (and `revoke <id>`) here.
LOG="${FAKE_T3_CLI_LOG:-/dev/null}"
echo "argv: $*" >> "$LOG"
if [ "$1" = auth ] && [ "$2" = session ]; then
  case "$3" in
    issue)
      if [ -n "$FAKE_T3_FAIL_FLAG" ] && [ -e "$FAKE_T3_FAIL_FLAG" ]; then
        echo "fake t3: issue failed (fail flag set)" >&2
        exit 1
      fi
      scopes=""; shift 3
      while [ $# -gt 0 ]; do
        if [ "$1" = --scope ]; then scopes="$scopes\"$2\","; shift; fi
        shift
      done
      scopes="[${scopes%,}]"
      id=$(uuidgen | tr 'A-Z' 'a-z')
      exp=$(date -u -v+8H +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '+8 hours' +%Y-%m-%dT%H:%M:%SZ)
      printf '{"sessionId":"%s","token":"fake-%s","method":"bearer-access-token","scopes":%s,"expiresAt":"%s"}\n' "$id" "$id" "$scopes" "$exp"
      exit 0 ;;
    revoke)
      echo "revoke $4" >> "$LOG"
      exit 0 ;;
  esac
fi
echo "fake t3: unsupported: $*" >&2
exit 2

"""Smoke test for the test rig (WIN-574). Assumes `rig_daemon.sh start --port P --fake-t3` is up.

    python3 tests/browser/drive_rig_smoke.py [--port 18781]

Seeds the token via the fake CLI (as `talky t3 login` would), then asserts
GET /api/t3/threads returns the fixture threads and the fake's /__log is reachable.
Stdlib only. Exit 0 on pass.
"""

import argparse
import json
import os
import stat
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUN = Path(os.environ.get("TALKY_RIG_DIR") or ROOT / ".rig")


def get_json(url: str):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("RIG_PORT", 18781)))
    ap.add_argument("--t3-port", type=int, default=None)
    a = ap.parse_args()
    t3_port = a.t3_port or a.port + 1
    fixture = json.loads((ROOT / "tests/fakes/t3_shell.json").read_text())
    expected = {t["id"] for t in fixture["threads"] if not t.get("archivedAt") and not t.get("deletedAt")}

    # Seed the token with the fake CLI, the way the real login flow does.
    token_file = RUN / "t3.json"
    out = subprocess.run(
        [str(ROOT / "tests/fakes/fake_t3_cli.sh"), "auth", "session", "issue", "--ttl", "8h", "--json",
         "--scope", "orchestration:read", "--scope", "orchestration:operate"],
        capture_output=True, text=True, check=True,
        env={**os.environ, "FAKE_T3_CLI_LOG": str(RUN / "cli.log")},
    ).stdout
    token_file.write_text(out)
    token_file.chmod(0o600)
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600

    threads = get_json(f"http://127.0.0.1:{a.port}/api/t3/threads")["threads"]
    got = {t["id"] for t in threads}
    assert got == expected, f"thread mismatch: missing={sorted(expected - got)[:3]} extra={sorted(got - expected)[:3]}"
    titles = [t["title"] for t in threads]
    assert any("ctrl" in t for t in titles), "control-char title missing"
    assert {t["project"] for t in threads} >= {"alpha", "beta", "No project"}
    assert sum(t["status"] == "running" for t in threads) == 2, "expected 2 running threads"
    assert threads[0]["status"] == "running", "running threads should sort first"

    log = get_json(f"http://127.0.0.1:{t3_port}/__log")
    assert isinstance(log, list), "/__log not a list"
    print(f"PASS: {len(threads)} threads, /__log reachable ({len(log)} rpcs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

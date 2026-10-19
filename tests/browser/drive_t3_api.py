"""T3 projects/threads API drive (WIN-576 S1a). No browser: plain HTTP against the rig + fake T3.

    rig_daemon.sh start --port 18782 --fake-t3
    python3 tests/browser/drive_t3_api.py [--port 18782]

Asserts: project counts exclude archived/deleted, 20-row page + next_cursor, 304 on the same seq,
a linked thread carries convo_id, attach is idempotent (convo count unchanged on the 2nd attach).
Stdlib only. Never touches port 8765 or a real T3. Exit 0 on pass.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
fails: list[str] = []


def check(ok: bool, msg: str) -> None:
    print("[t3-api]", "ok  " if ok else "FAIL", msg, flush=True)
    if not ok:
        fails.append(msg)


def req(url: str, method: str = "GET", body: dict | None = None, headers: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"content-type": "application/json", **(headers or {})}
    r = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            raw = resp.read()
            return resp.status, dict(resp.headers), (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, dict(e.headers), (json.loads(raw) if raw else None)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("RIG_PORT", 18782)))
    a = ap.parse_args()
    if a.port == 8765:
        raise SystemExit("refusing port 8765")
    base = f"http://127.0.0.1:{a.port}"
    fixture = json.loads((ROOT / "tests/fakes/t3_shell.json").read_text())
    live = [t for t in fixture["threads"] if not t.get("archivedAt") and not t.get("deletedAt")]
    want: dict[str, int] = {}
    for t in live:
        want[t["projectId"]] = want.get(t["projectId"], 0) + 1

    st, hd, body = req(f"{base}/api/t3/projects")
    check(st == 200 and body["state"] == "connected", f"projects 200 connected (got {st})")
    projects = {p["id"]: p for p in body["projects"]}
    for pid, p in projects.items():
        check(p["thread_count"] == want.get(pid, 0), f"{p['title']} thread_count {p['thread_count']} == {want.get(pid, 0)} (archived/deleted excluded)")
    check(projects["project:alpha"]["thread_count"] == 28, "alpha = 28")
    check(body["projects"][-1]["thread_count"] == 0, "empty project sorts last")
    etag = hd.get("ETag") or hd.get("Etag") or hd.get("etag")
    check(bool(etag) and etag == f'"{body["seq"]}"', f"ETag {etag} matches seq {body['seq']}")

    st, _, nb = req(f"{base}/api/t3/projects", headers={"If-None-Match": etag})
    check(st == 304 and nb is None, f"304 with empty body on the same seq (got {st})")

    q = lambda **k: f"{base}/api/t3/threads?" + urllib.parse.urlencode(k)  # noqa: E731
    st, _, p1 = req(q(project="project:alpha", limit=20))
    check(st == 200 and len(p1["threads"]) == 20 and p1["next_cursor"] == "o:20", f"page 1 = 20 rows, next_cursor {p1.get('next_cursor')}")
    st, _, p2 = req(q(project="project:alpha", limit=20, cursor=p1["next_cursor"]))
    check(len(p2["threads"]) == 8 and p2["next_cursor"] is None, f"page 2 = {len(p2['threads'])} rows, no next_cursor")
    check(len({t["id"] for t in p1["threads"] + p2["threads"]}) == 28, "28 distinct threads across pages")
    st, _, nf = req(q(project="project:nope"))
    check(st == 404, f"unknown project 404 (got {st})")

    _, _, convos0 = req(f"{base}/api/convos?archived=all")
    n0 = len(convos0["convos"]) if isinstance(convos0, dict) and "convos" in convos0 else len(convos0)
    tid = p1["threads"][3]["id"]
    check(p1["threads"][3]["convo_id"] is None, "target thread starts unlinked")
    st, _, at1 = req(f"{base}/api/t3/attach", "POST", {"thread_id": tid})
    check(st == 200 and at1["created"] is True, f"attach #1 created (got {st})")
    st, _, at2 = req(f"{base}/api/t3/attach", "POST", {"thread_id": tid})
    check(st == 200 and at2["created"] is False and at2["convo_id"] == at1["convo_id"], "attach #2 reuses the same convo")
    _, _, convos2 = req(f"{base}/api/convos?archived=all")
    n2 = len(convos2["convos"]) if isinstance(convos2, dict) and "convos" in convos2 else len(convos2)
    check(n2 == n0 + 1, f"convo count {n0} -> {n2}: exactly +1 after two attaches")

    st, _, p1b = req(q(project="project:alpha", limit=20))
    row = next(t for t in p1b["threads"] if t["id"] == tid)
    check(row["convo_id"] == at1["convo_id"], "linked thread carries convo_id")

    st, _, flat = req(f"{base}/api/t3/threads?limit=100")
    check(st == 200 and len(flat["threads"]) == min(100, len(live)) and flat["total"] == len(live), "flat /api/t3/threads (no project) pages across all projects")
    check(flat["threads"][0]["pinned"] is True, "flat list leads with pinned threads")

    print("[t3-api]", "PASS" if not fails else f"FAIL ({len(fails)})")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

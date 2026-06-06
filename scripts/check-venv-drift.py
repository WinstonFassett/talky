#!/usr/bin/env python3
"""check-venv-drift.py — flag divergence between the global `talky`
tool venv and the project `.venv`.

Why this exists: pyproject.toml dep changes only take effect in the
project venv after `uv sync`, and only take effect in the global tool
venv after `uv tool install --editable . --force --python 3.12`. The
two resolve independently. If they drift, you can hit subtle bugs
where `talky <cmd>` (tool venv) behaves differently from `uv run talky
<cmd>` (project venv). CLAUDE.md documents this; this script catches
it before it bites.

Exit codes:
  0 — venvs agree on every package they both have installed
  1 — divergence found (versions differ for shared packages)
  2 — one or both venvs missing

Usage:
  python3 scripts/check-venv-drift.py              # human-readable
  python3 scripts/check-venv-drift.py --json       # JSON for tooling
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

CRITICAL_PKGS = {
    "pipecat-ai",
    "fastmcp",
    "pydantic",
    "pydantic-core",
    "fastapi",
    "uvicorn",
}


def uv_tool_dir() -> Path:
    result = subprocess.run(
        ["uv", "tool", "dir"], capture_output=True, text=True, check=True
    )
    return Path(result.stdout.strip())


def installed_packages(venv: Path) -> dict[str, str]:
    """Return {package_name_lowercase: version} for the given venv.

    Uses `uv pip list --python <venv>/bin/python` because uv-managed tool
    venvs don't ship pip — calling `python -m pip` would fail. uv resolves
    against the interpreter's site-packages directly.
    """
    py = venv / "bin" / "python"
    result = subprocess.run(
        ["uv", "pip", "list", "--format=freeze", "--python", str(py)],
        capture_output=True,
        text=True,
        check=True,
    )
    pkgs: dict[str, str] = {}
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("-e "):
            continue
        if "==" not in line:
            continue
        name, _, version = line.partition("==")
        pkgs[name.lower()] = version
    return pkgs


def main() -> int:
    json_output = "--json" in sys.argv[1:]

    project_root = Path(__file__).resolve().parent.parent
    project_venv = project_root / ".venv"
    tool_venv = uv_tool_dir() / "talky"

    missing = []
    if not tool_venv.is_dir():
        missing.append(
            f"tool venv missing — run: uv tool install --editable . --python 3.12 (looked at {tool_venv})"
        )
    if not project_venv.is_dir():
        missing.append(f"project venv missing — run: uv sync (looked at {project_venv})")
    if missing:
        for m in missing:
            print(f"ERROR: {m}", file=sys.stderr)
        return 2

    tool_pkgs = installed_packages(tool_venv)
    proj_pkgs = installed_packages(project_venv)

    shared = set(tool_pkgs) & set(proj_pkgs)
    tool_only = sorted(set(tool_pkgs) - set(proj_pkgs))
    proj_only = sorted(set(proj_pkgs) - set(tool_pkgs))

    mismatches = sorted(
        [
            (name, tool_pkgs[name], proj_pkgs[name])
            for name in shared
            if tool_pkgs[name] != proj_pkgs[name]
        ]
    )
    critical_drift = any(name in CRITICAL_PKGS for name, _, _ in mismatches)

    if json_output:
        print(
            json.dumps(
                {
                    "tool_venv": str(tool_venv),
                    "project_venv": str(project_venv),
                    "version_mismatches": [
                        {"package": n, "tool": t, "project": p}
                        for n, t, p in mismatches
                    ],
                    "tool_only": [{"package": n, "version": tool_pkgs[n]} for n in tool_only],
                    "project_only": [
                        {"package": n, "version": proj_pkgs[n]} for n in proj_only
                    ],
                    "critical_drift": critical_drift,
                },
                indent=2,
            )
        )
    else:
        print(f"tool venv:    {tool_venv}")
        print(f"project venv: {project_venv}")
        print()
        if not mismatches:
            print(f"OK — {len(shared)} shared packages, all versions aligned.")
        else:
            print(f"VERSION MISMATCHES ({len(mismatches)}):")
            for name, t, p in mismatches:
                marker = "  CRITICAL" if name in CRITICAL_PKGS else ""
                print(f"  {name}: tool={t}  project={p}{marker}")
        print()
        print(f"Tool-venv-only ({len(tool_only)}):")
        for name in tool_only[:10]:
            print(f"  {name} {tool_pkgs[name]}")
        if len(tool_only) > 10:
            print(f"  ... and {len(tool_only) - 10} more")
        print()
        print(f"Project-venv-only ({len(proj_only)}):")
        for name in proj_only[:10]:
            print(f"  {name} {proj_pkgs[name]}")
        if len(proj_only) > 10:
            print(f"  ... and {len(proj_only) - 10} more")
        print()
        if critical_drift:
            print("CRITICAL DRIFT — at least one core dep differs across venvs.")
            print("Fix:")
            print("  uv sync")
            print("  uv tool install --editable . --force --python 3.12")
        elif mismatches:
            print("Non-critical drift. To align:")
            print("  uv sync && uv tool install --editable . --force --python 3.12")

    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())

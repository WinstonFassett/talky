"""Enable `python -m talky` as an equivalent to the `talky` CLI shim.

Used by talky's own self-invocation paths (ensure_daemon, kill, etc.)
so we never rely on `talky` being on PATH — see `_self_argv()` in cli.py.
"""

from talky.cli import main

if __name__ == "__main__":
    raise SystemExit(main())

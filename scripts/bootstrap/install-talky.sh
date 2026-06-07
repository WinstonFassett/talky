#!/usr/bin/env bash
# install-talky.sh — one-shot installer for Talky.
#
# Designed to be `curl | bash`'d by the Tauri shell on first launch (or
# manually by a user). Idempotent — re-running upgrades in place.
#
# Stages (each printed on a line starting with "::stage::" so the shell
# can show progress):
#   1. detect: macOS arch, system Python, existing talky
#   2. uv: install uv via curl|sh if missing
#   3. python: uv-managed Python 3.12 (no embed; uv handles it)
#   4. talky: install/upgrade `talky` from PyPI into uv tool
#   5. done: print final talky binary path
#
# Scope note: this installer is for the desktop-app / WebRTC voice path.
# It does NOT install PortAudio. The CLI's local-audio path
# (`talky say` / `talky ask`) uses a separate daemon backed by PyAudio
# and requires PortAudio system-side — that's installed by the
# `local_audio` extra's on-demand installer when the user actually runs
# one of those commands. Keep this script lean: uv + python + wheel.
#
# Failure mode: prints "::error::<message>" and exits non-zero. The
# shell parses for these and surfaces them in the UI.

set -euo pipefail

stage() { echo "::stage::$1::$2"; }
emit_err() { echo "::error::$1" >&2; exit 1; }

# --- 1. detect ---------------------------------------------------------------
stage detect "Checking system"

OS="$(uname -s)"
ARCH="$(uname -m)"
case "$OS" in
  Darwin) ;;
  Linux)  ;;
  *) emit_err "Unsupported OS: $OS (Talky bootstrap supports macOS and Linux only)";;
esac
echo "  OS=$OS ARCH=$ARCH"

if command -v talky >/dev/null 2>&1; then
  EXISTING_TALKY="$(command -v talky)"
  echo "  found existing talky: $EXISTING_TALKY"
fi

# --- 2. uv -------------------------------------------------------------------
stage uv "Installing uv (Rust-based Python installer)"

if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # uv installer writes to ~/.local/bin (or ~/.cargo/bin on some setups).
  # Make sure we can find it in this shell.
  for d in "$HOME/.local/bin" "$HOME/.cargo/bin"; do
    [ -x "$d/uv" ] && export PATH="$d:$PATH"
  done
fi

command -v uv >/dev/null 2>&1 || emit_err "uv install failed — see log above"
echo "  uv: $(uv --version)"

# --- 3. python ---------------------------------------------------------------
stage python "Installing Python 3.12 via uv"

uv python install 3.12 >/dev/null
echo "  python 3.12 ready"

# --- 4. talky ---------------------------------------------------------------
# TEMP (spike/distribution-parity): install from a wheel scp'd to the
# target machine. Lets us dogfood end-to-end without publishing to PyPI
# yet. The wheel embeds the built SPA in `_client_dist/`. Revert this
# block to `uv tool install ... talky` (PyPI) before merging/releasing.
TALKY_WHEEL="${TALKY_WHEEL:-/tmp/talky-0.1.2.dev0-py3-none-any.whl}"
stage talky "Installing Talky from $TALKY_WHEEL"

if [ ! -f "$TALKY_WHEEL" ]; then
  emit_err "wheel not found at $TALKY_WHEEL (set TALKY_WHEEL=/path/to/talky-X.X.X-py3-none-any.whl)"
fi

# `uv tool install --force` upgrades in place if already installed.
uv tool install --python 3.12 --force "$TALKY_WHEEL"
echo "  talky: $(uv tool dir 2>/dev/null || echo '?')/talky/bin/talky"

# --- 5. done -----------------------------------------------------------------
TALKY_BIN="$(command -v talky 2>/dev/null || echo '')"
[ -n "$TALKY_BIN" ] || emit_err "talky not on PATH after install"
echo "::stage::done::Talky installed at $TALKY_BIN"
echo "::result::talky_bin=$TALKY_BIN"

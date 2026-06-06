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
#   5. portaudio: brew-install if missing (so `talky say` works later)
#   6. done: print final talky binary path
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
TALKY_WHEEL="${TALKY_WHEEL:-/tmp/talky-local.whl}"
stage talky "Installing Talky from $TALKY_WHEEL"

if [ ! -f "$TALKY_WHEEL" ]; then
  emit_err "wheel not found at $TALKY_WHEEL (set TALKY_WHEEL=/path/to/talky-X.X.X-py3-none-any.whl)"
fi

# `uv tool install --force` upgrades in place if already installed.
uv tool install --python 3.12 --force "$TALKY_WHEEL"
echo "  talky: $(uv tool dir 2>/dev/null || echo '?')/talky/bin/talky"

# --- 5. portaudio (optional, for local audio) --------------------------------
stage portaudio "Checking PortAudio (system dep for local audio)"

case "$OS" in
  Darwin)
    if command -v brew >/dev/null 2>&1; then
      if ! brew list portaudio >/dev/null 2>&1; then
        brew install portaudio || echo "  portaudio install failed; 'talky say' will be unavailable until you install it manually"
      else
        echo "  portaudio already installed"
      fi
    else
      echo "  Homebrew not found; skipping portaudio (install with: brew install portaudio)"
    fi
    ;;
  Linux)
    if command -v apt-get >/dev/null 2>&1; then
      echo "  on Debian/Ubuntu run: sudo apt-get install -y portaudio19-dev"
    elif command -v dnf >/dev/null 2>&1; then
      echo "  on Fedora run: sudo dnf install -y portaudio-devel"
    fi
    ;;
esac

# --- 6. done -----------------------------------------------------------------
TALKY_BIN="$(command -v talky 2>/dev/null || echo '')"
[ -n "$TALKY_BIN" ] || emit_err "talky not on PATH after install"
echo "::stage::done::Talky installed at $TALKY_BIN"
echo "::result::talky_bin=$TALKY_BIN"

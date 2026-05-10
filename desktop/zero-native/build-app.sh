#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

APP="zig-out/package/talky-shell-0.1.0-macos-Debug.app"

echo "Building..."
/opt/homebrew/bin/zig build

echo "Copying binary..."
cp zig-out/bin/talky-shell "$APP/Contents/MacOS/talky-shell"

echo "Signing..."
codesign --sign - --force --deep --entitlements macos.entitlements "$APP"

echo "Launching..."
open "$APP"

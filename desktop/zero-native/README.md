# talky-shell

WKWebView desktop shell for the talky daemon. Navigates to `http://localhost:9090` — no bundled frontend.

## Prerequisites

- [Zig](https://ziglang.org/download/) 0.16+
- [zero-native CLI](https://github.com/vercel-labs/zero-native): `npm install -g zero-native`
- `shell/vendor/zero-native` submodule initialized (see below)

## Setup

```bash
git submodule update --init shell/vendor/zero-native
```

## Build

Run from `shell/` (or `shell/` is the cwd):

```bash
# Debug binary only
zig build

# Package + codesign .app bundle
zig build package
```

Output: `shell/zig-out/package/talky-shell-0.1.0-macos-Debug.app`

## Run

Start the talky daemon first, then open the app:

```bash
talky daemon
open shell/zig-out/package/talky-shell-0.1.0-macos-Debug.app
```

## zero-native vendor

`vendor/zero-native` is a submodule on the `talky-integration` branch. That branch adds patches required for talky:

- `WKUIDelegate` + mic permission auto-grant (no OS prompt)
- `setSinkId` JS shim (`WKWebView` doesn't implement `HTMLMediaElement.setSinkId`)
- `NSMicrophoneUsageDescription` in the generated `Info.plist`

Override the framework path if needed: `zig build -Dzero-native-path=/path/to/zero-native`

# talky-shell

WKWebView desktop shell for the talky daemon. Navigates to `http://localhost:9090` — no bundled frontend.

## Prerequisites

- [Zig](https://ziglang.org/download/) 0.16+ (homebrew: `brew install zig`)
- `desktop/zero-native/vendor/zero-native` submodule initialized (see below)

## Setup

```bash
git submodule update --init desktop/zero-native/vendor/zero-native
```

## Build & run

From the repo root:

```bash
desktop/zero-native/build-app.sh
```

That script: compiles from vendor source, copies the binary into the `.app`, codesigns, and opens it.

> **Do not use `zig build package`** — it calls the precompiled `zero-native` CLI which doesn't include the talky-integration patches.

## Run (after building)

```bash
talky daemon
open desktop/zero-native/zig-out/package/talky-shell-0.1.0-macos-Debug.app
```

## zero-native vendor

`vendor/zero-native` is a submodule on the `talky-integration` branch. That branch adds patches required for talky:

- `WKUIDelegate` + mic permission auto-grant (no OS prompt)
- `setSinkId` JS shim (`WKWebView` doesn't implement `HTMLMediaElement.setSinkId`)
- `NSMicrophoneUsageDescription` in the generated `Info.plist`

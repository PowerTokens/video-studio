#!/usr/bin/env bash
# Smoke-check the unsigned Apple Silicon app on the Mac that just built it.
#
# Confirms the .app, .dmg, .zip, and checksums, then launches the packaged
# binary with a throw-away data directory. No real credentials, no paid API
# calls, and no signing secrets. This is a start-and-quit GUI check, not a
# generation run.
#
# Usage:
#   bash tools/macos_app_smoke.sh
#   bash tools/macos_app_smoke.sh dist/PowerTokensVideoStudio.app
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "macos_app_smoke.sh must run on macOS. Current host: $(uname -s)." >&2
  exit 1
fi
if [[ "$(uname -m)" != "arm64" ]]; then
  echo "Smoke check is for the Apple Silicon build. Current architecture: $(uname -m)." >&2
  exit 1
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

APP="${1:-dist/PowerTokensVideoStudio.app}"
BIN="$APP/Contents/MacOS/PowerTokensVideoStudio"
PLIST="$APP/Contents/Info.plist"
DMG="dist/PowerTokensVideoStudio-macOS-arm64.dmg"
ZIP="dist/PowerTokensVideoStudio-macOS-arm64.app.zip"
SUMS="dist/SHA256SUMS.txt"
SMOKE_DIR="dist/smoke"
mkdir -p "$SMOKE_DIR"

if [[ ! -d "$APP" || ! -x "$BIN" ]]; then
  echo "Missing packaged app or binary: $APP" >&2
  exit 1
fi

ARCHS="$(lipo -archs "$BIN")"
echo "Packaged binary architectures: $ARCHS"
if ! printf '%s\n' "$ARCHS" | grep -qw arm64; then
  echo "Expected an arm64 executable, got: $ARCHS" >&2
  exit 1
fi

VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$PLIST")"
echo "CFBundleShortVersionString: $VERSION"
if [[ -z "$VERSION" ]]; then
  echo "Info.plist is missing CFBundleShortVersionString" >&2
  exit 1
fi

SIGN_INFO="$(codesign -dv --verbose=4 "$APP" 2>&1 || true)"
printf '%s\n' "$SIGN_INFO" | tee "$SMOKE_DIR/codesign.txt"
if printf '%s\n' "$SIGN_INFO" | grep -E 'Authority=|Developer ID Application|Apple Development:' >/dev/null; then
  echo "Smoke check refuses a Developer ID or Apple Development signature." >&2
  exit 1
fi

for required in "$DMG" "$ZIP" "$SUMS" dist/GATEKEEPER.txt; do
  if [[ ! -f "$required" ]]; then
    echo "Missing package file: $required" >&2
    exit 1
  fi
done

echo "Verifying SHA-256 checksums"
# The sums file is written with paths relative to the process CWD at build time
# (dist/...). Recheck from the repository root.
shasum -a 256 -c "$SUMS"

echo "Mounting DMG read-only"
MOUNT="$(mktemp -d /tmp/pt-dmg-XXXXXX)"
cleanup_mount() {
  hdiutil detach "$MOUNT" -quiet -force >/dev/null 2>&1 || true
  rmdir "$MOUNT" >/dev/null 2>&1 || true
}
trap cleanup_mount EXIT
hdiutil attach "$DMG" -readonly -nobrowse -mountpoint "$MOUNT"
if [[ ! -d "$MOUNT/PowerTokensVideoStudio.app/Contents/MacOS" ]]; then
  echo "DMG does not contain PowerTokensVideoStudio.app" >&2
  exit 1
fi
if [[ ! -f "$MOUNT/README.txt" ]]; then
  echo "DMG is missing README.txt (GATEKEEPER note)" >&2
  exit 1
fi
echo "DMG volume listing:" | tee "$SMOKE_DIR/dmg-listing.txt"
ls -la "$MOUNT" | tee -a "$SMOKE_DIR/dmg-listing.txt"
hdiutil detach "$MOUNT" -quiet
rmdir "$MOUNT" >/dev/null 2>&1 || true
trap - EXIT

echo "Zip listing (first entries):" | tee "$SMOKE_DIR/zip-listing.txt"
unzip -Z1 "$ZIP" | tee -a "$SMOKE_DIR/zip-listing.txt" >/dev/null
if ! unzip -Z1 "$ZIP" | grep -q 'PowerTokensVideoStudio.app/Contents/MacOS/PowerTokensVideoStudio'; then
  echo "Zip does not contain the app binary" >&2
  exit 1
fi
if ! unzip -Z1 "$ZIP" | grep -q 'README.txt'; then
  echo "Zip is missing README.txt (GATEKEEPER note)" >&2
  exit 1
fi

# Isolated data dir: platform_paths.user_data_dir() honors LOCALAPPDATA on every OS.
SMOKE_DATA="${LOCALAPPDATA:-}"
if [[ -z "$SMOKE_DATA" ]]; then
  SMOKE_DATA="$(mktemp -d /tmp/pt-smoke-data-XXXXXX)"
  export LOCALAPPDATA="$SMOKE_DATA"
fi
mkdir -p "$SMOKE_DATA"
echo "LOCALAPPDATA=$LOCALAPPDATA"

# Do not inherit secrets or point the client at a surprise host.
unset POWERTOKENS_API_KEY POWERTOKENS_API_KEYS POWERTOKENS_API_TOKEN POWERTOKENS_API_BASE || true

SMOKE_LOG="$SMOKE_DIR/launch.log"
: > "$SMOKE_LOG"
echo "Launching $BIN"
# Launch the packaged binary directly so we own the PID. The locally built
# unsigned app is not a Gatekeeper-quarantined download.
"$BIN" >>"$SMOKE_LOG" 2>&1 &
PID=$!
echo "PID=$PID" | tee -a "$SMOKE_LOG"

cleanup_app() {
  if kill -0 "$PID" >/dev/null 2>&1; then
    kill -TERM "$PID" >/dev/null 2>&1 || true
    # Give the process a moment to exit, then force it.
    waited=0
    while kill -0 "$PID" >/dev/null 2>&1 && [[ "$waited" -lt 10 ]]; do
      sleep 1
      waited=$((waited + 1))
    done
    if kill -0 "$PID" >/dev/null 2>&1; then
      kill -KILL "$PID" >/dev/null 2>&1 || true
    fi
  fi
  wait "$PID" >/dev/null 2>&1 || true
}
trap cleanup_app EXIT

saw_window=0
alive_seconds=0
deadline=30
i=0
while [[ "$i" -lt "$deadline" ]]; do
  sleep 1
  i=$((i + 1))
  if ! kill -0 "$PID" >/dev/null 2>&1; then
    wait "$PID" >/dev/null 2>&1 || status=$?
    echo "Packaged app exited after ${i}s (status=${status:-unknown})" >&2
    cat "$SMOKE_LOG" >&2 || true
    exit 1
  fi
  alive_seconds=$i

  # Best-effort window check. System Events may lack Accessibility on some runners.
  win_count="$(osascript -e 'tell application "System Events"
    if exists process "PowerTokensVideoStudio" then
      count of windows of process "PowerTokensVideoStudio"
    else
      0
    end if
  end tell' 2>>"$SMOKE_LOG" || true)"
  win_count="$(printf '%s' "$win_count" | tr -cd '0-9')"
  win_count="${win_count:-0}"
  if [[ "$win_count" -gt 0 ]]; then
    saw_window=1
    echo "Window count after ${i}s: $win_count"
    break
  fi
done

# Desktop screenshot for evidence, whether or not we could count a window.
screencapture -x "$SMOKE_DIR/desktop.png" 2>>"$SMOKE_LOG" || true

if ! kill -0 "$PID" >/dev/null 2>&1; then
  echo "Packaged app died before the smoke check finished" >&2
  cat "$SMOKE_LOG" >&2 || true
  exit 1
fi

if [[ "$saw_window" -eq 1 ]]; then
  echo "Packaged app started and showed a window (alive ${alive_seconds}s)."
else
  # Process stayed up for the full wait. Treat that as a start success, but
  # record that the GUI window could not be enumerated (common if
  # Accessibility is denied). The screenshot is still saved when possible.
  if [[ "$alive_seconds" -lt 8 ]]; then
    echo "Packaged app did not stay running long enough and no window was seen." >&2
    cat "$SMOKE_LOG" >&2 || true
    exit 1
  fi
  echo "Packaged app stayed running for ${alive_seconds}s, but System Events did not list a window." | tee "$SMOKE_DIR/window-limitation.txt"
fi

# Confirm the isolated data dir was used (settings or the app folder appear).
if ! find "$SMOKE_DATA" -mindepth 1 | grep -q .; then
  echo "Warning: no files were written under LOCALAPPDATA=$SMOKE_DATA" | tee "$SMOKE_DIR/data-dir-warning.txt"
else
  echo "Isolated data directory contents:" | tee "$SMOKE_DIR/data-dir.txt"
  find "$SMOKE_DATA" | tee -a "$SMOKE_DIR/data-dir.txt"
fi

echo "Quitting packaged app (pid $PID)"
cleanup_app
trap - EXIT

echo "Smoke check passed."

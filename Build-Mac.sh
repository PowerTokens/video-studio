#!/usr/bin/env bash
# Unsigned Apple Silicon build of PowerTokens Video Studio.
#
# Produces:
#   dist/PowerTokensVideoStudio.app
#   dist/PowerTokensVideoStudio-macOS-arm64.dmg
#   dist/PowerTokensVideoStudio-macOS-arm64.app.zip
#   dist/GATEKEEPER.txt
#
# There is no Apple Developer account. This script does not codesign with a
# Developer ID and does not notarize. PyInstaller may apply an ad-hoc signature
# so the kernel can execute an arm64 binary; Gatekeeper still treats a
# downloaded copy as untrusted. See dist/GATEKEEPER.txt.
#
# This script cannot produce a Mac binary on Linux or Windows. Do not invent one.
#
#   ./Build-Mac.sh                  local build (venv, tests, app, dmg, zip)
#   ./Build-Mac.sh --skip-tests     tests already ran
#   ./Build-Mac.sh --no-venv        use the current Python (CI)
set -euo pipefail

cd "$(dirname "$0")"

SKIP_TESTS=0
USE_VENV=1
for arg in "$@"; do
  case "$arg" in
    --skip-tests) SKIP_TESTS=1 ;;
    --no-venv) USE_VENV=0 ;;
    *)
      echo "Unknown argument: $arg" >&2
      exit 1
      ;;
  esac
done

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Build-Mac.sh must run on macOS. It does not produce a Mac app on this host ($(uname -s))." >&2
  exit 1
fi
if [[ "$(uname -m)" != "arm64" ]]; then
  echo "This script builds Apple Silicon (arm64) only. Current architecture: $(uname -m)." >&2
  echo "A universal2 or Intel build is intentionally not produced here." >&2
  exit 1
fi

export PYTHONUTF8=1

if [[ "$USE_VENV" -eq 1 ]]; then
  python3 -m venv .build-venv
  # shellcheck disable=SC1091
  source .build-venv/bin/activate
  PYTHON="python"
elif command -v python >/dev/null 2>&1; then
  # CI (setup-python) exposes the interpreter as `python`.
  PYTHON="python"
else
  PYTHON="python3"
fi

"$PYTHON" -m pip install "pyinstaller>=6,<7" -r requirements.txt

if [[ "$SKIP_TESTS" -eq 0 ]]; then
  "$PYTHON" -m unittest discover -s tests -v
fi

bash tools/make_icns.sh
ICON="build/logo.icns"
if [[ ! -f "$ICON" ]]; then
  echo "Missing $ICON" >&2
  exit 1
fi

# --windowed (not --onefile) so the deliverable is a real .app bundle.
# The glob must stay quoted: bash would otherwise expand *.xlsx itself.
"$PYTHON" -m PyInstaller --noconfirm --clean --windowed \
  --name PowerTokensVideoStudio \
  --icon "$ICON" \
  --osx-bundle-identifier ai.powertokens.videostudio \
  --target-arch arm64 \
  --add-data "assets:assets" \
  --add-data "*.xlsx:." \
  --collect-all imageio_ffmpeg \
  app.py

APP="dist/PowerTokensVideoStudio.app"
BIN="$APP/Contents/MacOS/PowerTokensVideoStudio"
PLIST="$APP/Contents/Info.plist"
if [[ ! -d "$APP" || ! -x "$BIN" ]]; then
  echo "PyInstaller did not produce $APP" >&2
  exit 1
fi

VERSION="$("$PYTHON" -c 'import wan_core; print(wan_core.APP_VERSION)')"
plist_set() {
  local key="$1" kind="$2" value="$3"
  if [[ "$kind" == "bool" ]]; then
    if ! /usr/libexec/PlistBuddy -c "Set :${key} ${value}" "$PLIST" 2>/dev/null; then
      /usr/libexec/PlistBuddy -c "Add :${key} bool ${value}" "$PLIST"
    fi
  else
    # Single quotes keep display names that contain spaces intact.
    if ! /usr/libexec/PlistBuddy -c "Set :${key} '${value}'" "$PLIST" 2>/dev/null; then
      /usr/libexec/PlistBuddy -c "Add :${key} ${kind} '${value}'" "$PLIST"
    fi
  fi
}
plist_set CFBundleShortVersionString string "$VERSION"
plist_set CFBundleVersion string "$VERSION"
plist_set CFBundleName string "PowerTokens Video Studio"
plist_set CFBundleDisplayName string "PowerTokens Video Studio"
plist_set NSHighResolutionCapable bool true

ARCHS="$(lipo -archs "$BIN")"
echo "Architectures: $ARCHS"
if ! printf '%s\n' "$ARCHS" | grep -qw arm64; then
  echo "Expected an arm64 executable, got: $ARCHS" >&2
  exit 1
fi

if ! find "$APP" -name 'logo.png' | grep -q .; then
  echo "logo.png is missing from the app bundle" >&2
  exit 1
fi
if ! find "$APP/Contents/Resources" -name '*.icns' | grep -q .; then
  echo "The .icns icon is missing from the app bundle" >&2
  exit 1
fi
FOUND_XLSX="$(find "$APP" -name '*.xlsx')"
printf '%s\n' "$FOUND_XLSX"
if ! printf '%s\n' "$FOUND_XLSX" | grep -q 'short-drama-batch-template.xlsx'; then
  echo "English batch template is missing from the app bundle" >&2
  exit 1
fi
XLSX_COUNT="$(printf '%s\n' "$FOUND_XLSX" | grep -c '\.xlsx$' || true)"
if [[ "${XLSX_COUNT:-0}" -lt 2 ]]; then
  echo "Expected at least two .xlsx templates in the app bundle" >&2
  exit 1
fi
if ! find "$APP" -iname '*ffmpeg*' | grep -q .; then
  echo "Bundled ffmpeg (imageio-ffmpeg) is missing from the app bundle" >&2
  exit 1
fi

# Ad-hoc signing (Signature=adhoc, no Authority=) is PyInstaller's arm64
# requirement. A Developer ID or Apple Development identity must not appear.
SIGN_INFO="$(codesign -dv --verbose=4 "$APP" 2>&1 || true)"
printf '%s\n' "$SIGN_INFO"
if printf '%s\n' "$SIGN_INFO" | grep -E 'Authority=|Developer ID Application|Apple Development:' >/dev/null; then
  echo "This build must stay unsigned. Refusing a Developer ID or Apple Development signature." >&2
  exit 1
fi

cat > dist/GATEKEEPER.txt << 'EOF'
PowerTokens Video Studio for macOS (Apple Silicon) is unsigned.
There is no Apple Developer ID signature and the app is not notarized.
PyInstaller may attach an ad-hoc signature so Apple Silicon can launch the
binary. That is not a distribution signature. Gatekeeper still blocks a
downloaded copy until you allow it.

Do one of the following the first time you open the app:
1. In Finder, right-click (or Control-click) PowerTokensVideoStudio.app and
   choose Open, then Open again.
2. Try to open the app once, then go to
   System Settings → Privacy & Security and click Open Anyway.

The packaged app does not need Python. Windows users should keep using
PowerTokensVideoStudio.exe.

本 macOS 版本未使用 Apple 开发者证书签名，也未经过公证。
首次打开时，请在 Finder 中右键该 App 并选择「打开」，
或到「系统设置 → 隐私与安全性」点击「仍要打开」。
EOF

DMG_ROOT="dist/dmg-root"
rm -rf "$DMG_ROOT"
mkdir -p "$DMG_ROOT"
cp -R "$APP" "$DMG_ROOT/"
ln -s /Applications "$DMG_ROOT/Applications"
cp dist/GATEKEEPER.txt "$DMG_ROOT/README.txt"

DMG="dist/PowerTokensVideoStudio-macOS-arm64.dmg"
rm -f "$DMG"
hdiutil create -volname "PowerTokens Video Studio" -srcfolder "$DMG_ROOT" -ov -format UDZO "$DMG"

ZIP_ROOT="dist/zip-root"
rm -rf "$ZIP_ROOT"
mkdir -p "$ZIP_ROOT"
cp -R "$APP" "$ZIP_ROOT/"
cp dist/GATEKEEPER.txt "$ZIP_ROOT/README.txt"
ZIP="dist/PowerTokensVideoStudio-macOS-arm64.app.zip"
rm -f "$ZIP"
ditto -c -k "$ZIP_ROOT" "$ZIP"

shasum -a 256 "$DMG" "$ZIP" | tee dist/SHA256SUMS.txt

echo
echo "Done (unsigned Apple Silicon build):"
echo "  $APP"
echo "  $DMG"
echo "  $ZIP"
echo "First launch: right-click Open, or System Settings → Privacy & Security → Open Anyway."
echo "See dist/GATEKEEPER.txt."

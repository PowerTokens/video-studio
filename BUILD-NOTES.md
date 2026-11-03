# Build notes

## Windows EXE (`Build-EXE.bat`)

- Installs `requirements.txt` into `.build-venv` (includes `imageio-ffmpeg`).
- PyInstaller flags:
  - `--collect-all imageio_ffmpeg` — packs the package and its `binaries/` folder (the platform `ffmpeg` executable ships as package data, not a DLL; `--collect-binaries` alone does not include it).
- **Installer / EXE size:** bundling `imageio-ffmpeg` adds about **70–80 MB** (Windows x86_64 ffmpeg binary). Linux wheels measured ~76 MB for the same major version; Windows is in the same range.
- Runtime preference: the app uses the bundled binary first, then falls back to `ffmpeg` on `PATH` if the bundle is missing.

A full Windows build cannot be verified on Linux CI; confirm on a Windows machine that `dist\PowerTokensVideoStudio.exe` contains the binary (e.g. after extract, or by exercising **Continue from previous clip** without a system ffmpeg).

## Packaging check (Linux)

`PyInstaller.utils.hooks.collect_all("imageio_ffmpeg")` / `collect_data_files("imageio_ffmpeg")` lists the ffmpeg binary under `imageio_ffmpeg/binaries/` (~76 MB on Linux wheels; Windows wheels are in the same ~70–80 MB range). A full Windows EXE build must still be run on Windows.

## Follow-up: desktop download UTM jump (deferred)

In-app and OAuth already send `utm_source=videostudio&utm_medium=app&utm_campaign=video-studio`
(with `utm_content=app.oauth_authorize` on authorize). A powertokens.ai download jump page
that 302s to the GitHub `.exe` with UTM is **not** built yet — track as a follow-up so
Release/download attribution stops landing as unknown.

## Billing outbound UTM (product-forced)

Credits chip / recharge opens `https://powertokens.ai[/zh-Hans]/billing` with
`utm_source=videostudio&utm_medium=app&utm_campaign=video-studio&utm_content=app.billing`.
Product fields are **forced** even if last-touch was an ad; click ids still forward.
OAuth authorize still uses last-touch (not forced).

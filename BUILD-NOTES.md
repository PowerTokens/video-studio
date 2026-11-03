# Build notes

## Windows EXE (`Build-EXE.bat`)

- Installs `requirements.txt` into `.build-venv` (includes `imageio-ffmpeg` and `pillow` for first-frame encode/resize).
- PyInstaller flags:
  - `--collect-all imageio_ffmpeg` — packs the package and its `binaries/` folder (the platform `ffmpeg` executable ships as package data, not a DLL; `--collect-binaries` alone does not include it).
- **Installer / EXE size:** bundling `imageio-ffmpeg` adds about **70–80 MB** (Windows x86_64 ffmpeg binary). Linux wheels measured ~76 MB for the same major version; Windows is in the same range.
- Runtime preference: the app uses the bundled binary first, then falls back to `ffmpeg` on `PATH` if the bundle is missing.

A full Windows build cannot be verified on Linux CI; confirm on a Windows machine that `dist\PowerTokensVideoStudio.exe` contains the binary (e.g. after extract, or by exercising **Continue from previous clip** without a system ffmpeg).

## Packaging check (Linux)

`PyInstaller.utils.hooks.collect_all("imageio_ffmpeg")` / `collect_data_files("imageio_ffmpeg")` lists the ffmpeg binary under `imageio_ffmpeg/binaries/` (~76 MB on Linux wheels; Windows wheels are in the same ~70–80 MB range). A full Windows EXE build must still be run on Windows.

## Desktop download UTM jump (web Pages)

In-app and OAuth send `utm_source=videostudio&utm_medium=app&utm_campaign=video-studio`
(with `utm_content=app.oauth_authorize` on authorize).

Public download attribution uses the GitHub Pages jump (static site cannot true-HTTP 302):

```
https://powertokens.github.io/video-studio-web/download.html
```

It captures inbound `utm_*` / click ids (same keys as web `js/utm.js`), defaults missing
product fields, sets `utm_content=web.download_jump` when absent, then redirects to
`…/releases/latest/download/PowerTokensVideoStudio.exe` **without** UTM on the binary URL.

**官网 powertokens.ai** should link or 302 download CTAs to that jump URL (or host the
same path). Direct GitHub asset links remain unattributed.

## Billing outbound UTM (product-forced)

Credits chip / recharge opens `https://powertokens.ai[/zh-Hans]/billing` with
`utm_source=videostudio&utm_medium=app&utm_campaign=video-studio&utm_content=app.billing`.
Product fields are **forced** even if last-touch was an ad; click ids still forward.
OAuth authorize still uses last-touch (not forced).

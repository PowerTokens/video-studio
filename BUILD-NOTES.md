# Build notes

## Windows EXE (`Build-EXE.bat`)

- Installs `requirements.txt` into `.build-venv` (includes `imageio-ffmpeg` and `pillow` for first-frame encode/resize).
- PyInstaller flags:
  - `--collect-all imageio_ffmpeg` — packs the package and its `binaries/` folder (the platform `ffmpeg` executable ships as package data, not a DLL; `--collect-binaries` alone does not include it).
- **Installer / EXE size:** bundling `imageio-ffmpeg` adds about **70–80 MB** (Windows x86_64 ffmpeg binary). Linux wheels measured ~76 MB for the same major version; Windows is in the same range.
- Runtime preference: the app uses the bundled binary first, then falls back to `ffmpeg` on `PATH` if the bundle is missing.

A full Windows build cannot be verified on Linux CI; confirm on a Windows machine that `dist\PowerTokensVideoStudio.exe` contains the binary (e.g. after extract, or by exercising **Continue from previous clip** without a system ffmpeg).

## Analytics packaging

The public PostHog project capture token (`phc_...` only) ships in `analytics_config.py`.
`Build-EXE.bat` and the Windows workflows also import `analytics` / `analytics_config`
(and `analytics_config_local` when that untracked file is present). Personal or admin
keys are refused. Unit tests set `VS_ANALYTICS_DISABLE=1` so they never contact ingest.
Tag builds of **Build Windows executable** attach `PowerTokensVideoStudio.exe` to that
tag's GitHub Release. UI screenshots run only on `workflow_dispatch` so a hanging
screenshot job cannot cancel a release build.

## Packaging check (Linux)

`PyInstaller.utils.hooks.collect_all("imageio_ffmpeg")` / `collect_data_files("imageio_ffmpeg")` lists the ffmpeg binary under `imageio_ffmpeg/binaries/` (~76 MB on Linux wheels; Windows wheels are in the same ~70–80 MB range). A full Windows EXE build must still be run on Windows.

## Desktop download UTM jump (web Pages)

In-app signup, OAuth, and billing force
`utm_source=videostudio&utm_medium=app&utm_campaign=video-studio`
(`utm_content=app.signup` or `utm_content=app.oauth_authorize` on auth jumps).
An ad last-touch cannot replace those three fields.

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

## Auth outbound UTM (product-forced)

Same rule as billing for every desktop jump to register or sign in:

- OAuth authorize (`utm_content=app.oauth_authorize`)
- Onboarding signup, official API key / register, and the quota-tip link (`utm_content=app.signup`)

Example (Chinese API keys page, after an ad click that also left a `gclid`):

`https://powertokens.ai/zh-Hans/api-keys?utm_source=videostudio&utm_medium=app&utm_campaign=video-studio&gclid=…&utm_content=app.signup`

GitHub release assets, the EXE redirect, and template raw URLs stay without UTM.

## Tag CI (`build-windows.yml`)

Tag pushes (`v*`) and workflow_dispatch run tests after `pip install -r requirements.txt`
(Pillow + imageio-ffmpeg), then PyInstaller with `--collect-all imageio_ffmpeg` (same as
`Build-EXE.bat`). The older tag job that ran tests on a bare Python install failed once
Pillow became required by chain tests.


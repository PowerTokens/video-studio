# Video Studio desktop — usage analytics

Minimal PostHog *capture* of critical events. This is not a PostHog SDK install:
autocapture, session replay, heatmaps, and person-profile enrichment are off.

No events are stored or sent until the user opts in **and** a project key is
configured for an official ingest host. The committed key is empty; nothing is
live, and offline tests are not a verified backend integration.

## Config names (required before any real event can be verified)

Set one of the following. Do not commit a real key.

| Name | Where | Value |
| --- | --- | --- |
| `VS_POSTHOG_PROJECT_KEY` | Environment (dev, CI, or the EXE build machine) | PostHog project API key, starts with `phc_` |
| `VS_POSTHOG_HOST` | Environment | Only `https://us.i.posthog.com` or `https://eu.i.posthog.com` (see [Capture API](https://posthog.com/docs/api/capture)) |
| `POSTHOG_PROJECT_KEY` / `POSTHOG_HOST` | `analytics_config.py` (committed defaults stay empty) | Same meaning as the env vars |
| `POSTHOG_PROJECT_KEY` / `POSTHOG_HOST` | Untracked `analytics_config_local.py` | Local override, gitignored |

The client posts to `{host}/i/v0/e/` with the documented single-event fields
`api_key`, `event`, `distinct_id`, optional `properties` and `timestamp`. Any
other host is ignored (no traffic). Until a key is present, opted-in events are
not queued for later upload.

### Desktop packaging

`app.py` imports `analytics`, and `analytics.py` imports `analytics_config` at
module level so PyInstaller can see both. Build-EXE.bat and the Windows
workflows also pass `--hidden-import analytics --hidden-import analytics_config`.
The project key is still injected at build or runtime; it is not baked into
git. A release EXE will not send traffic until that key is present *and* the
user has opted in.

### Project settings (after a key exists, before claiming anonymity)

Per [Controlling data collection](https://posthog.com/docs/privacy/data-collection)
and [data storage](https://posthog.com/docs/privacy/data-storage):

- Every event sets `$process_person_profile: false` (official anonymous capture).
- Every event sets `$geoip_disable: true` so GeoIP enrichment is skipped for that event.
- `$ip` is **omitted**. Setting it to null does not hide the request IP; the
  ingest host still sees the TCP/TLS client IP. To avoid *storing* IPs, enable
  **Discard client IP data** in PostHog project settings. To also skip
  transformation-time GeoIP if you change the per-event flag, disable the GeoIP
  transformation in the project.
- This client does **not** claim IP anonymity.

## Consent

- Default is unset: no `install_id`, no session, no event queue, no network.
- The first-run notice must be accepted before any identifier is minted.
- Denied: only the local denial flag is kept; the send queue is dropped.
- The persistent checkbox lives on the API Key tab.
- Opting in later does not upload activity that happened before consent.

## Identity (MVP)

`distinct_id` is a random install UUID created only after opt-in, plus a
process/session UUID. This MVP does **not** link events to a PowerTokens
account. A short sequential `user_id` would not be anonymous even if hashed.
Login still emits `login_success` as a funnel step, without an account id.

## Event schema

Shared properties: `platform=desktop`, `app_version`, `session_id`,
`install_id`. Switching tabs does not fire `app_open`. A new session starts
after 30 minutes idle.

| Event | When it fires | Extra properties |
| --- | --- | --- |
| `app_open` | Once per session, after opt-in (or on startup if already opted in) | — |
| `login_success` | Once per session after a successful OAuth sign-in, not on session restore | `key_applied` (boolean only) |
| `generation_start` | New `Client.submit` only (Generate, Compare, or Batch). Resume is not a start | `generation_id` (= task `local_id`), `model` (catalog-like token only), `surface`, optional `requested_duration_s` |
| `generation_success` | That submit, or a later **resume** of the same `local_id`, finishes | `duration_ms` |
| `generation_failed` | Only irreversible failures (`AUTH`, `QUOTA`, `PARAM`, `REJECTED`, `UNCERTAIN` with no task, `SERVER_FAILED`, …). Pause, timeout, and query-down are not terminal, so a later resume can still record success | safe `error_code` only |
| `video_download` | Not used for the automatic save inside a successful generate. Fired only for an explicit download-from-link | `model`, `surface` |
| `chat_submit` | User confirmed **Send** with a non-empty message after the login/key gate. This is the click, not a completed reply | `chat_id`, `surface=chat`, optional `model` |
| `chat_success` | The chat API/model reply for that `chat_id` completed | `chat_id`, `surface=chat`, optional `model` |
| `chat_failed` | The chat API/model reply for that `chat_id` failed | `chat_id`, safe `error_code` only |


Compare/batch: each model or job is its own `generation_start` / terminal pair
(the correct per-generation denominator). Chat-pilot events are not sent.

`model` must match `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`. Prompts, sentences,
URLs, and unknown property names are dropped.

Never sent: prompts, API keys, tokens, OAuth codes, URL query strings, email,
name, avatar, images, videos, credits, raw error / HTTP bodies, or account ids.

## Dashboard (setup notes only — not proof of ingestion)

1. **Daily opens** — `app_open`, count or unique `install_id`, `platform=desktop`.
2. **DAU** — unique `install_id` / `distinct_id` per day on `app_open`. Do not
   treat GitHub EXE `download_count` as DAU.
3. **Funnel** — `app_open` → `login_success` → `generation_start` →
   `generation_success`. Do not expect a trailing `video_download` on desktop.
4. **Retention** — unique `install_id`, weekly, first event `app_open`.
5. **Generation health** — success / start by `model` and `surface`; failures
   by `error_code`. Compare runs contribute one start per model.

Do not compute revenue or credits from these client events.

## Tests

```text
python -m unittest tests.test_analytics -v
```

Covers the property and model allowlists, no pre-consent queue or identifiers,
opt-out, transport failure, official capture flags, duplicate prevention, and
timeout-then-resume success.

## Build packaging

The committed `analytics_config.py` key stays empty, so a default unconfigured
build sends no traffic. To bake the *public* project capture token (`phc_...`)
into a release EXE:

1. Set `VS_POSTHOG_PROJECT_KEY` (and optionally `VS_POSTHOG_HOST`) on the build
   machine, or write an untracked `analytics_config_local.py` with those names.
2. `Build-EXE.bat` and the Windows workflows call
   `tools/prepare_analytics_config.py` and add `--hidden-import analytics_config_local`
   when that file exists. `build-windows.yml` also checks that `analytics.py` and
   `analytics_config.py` are present in the PyInstaller archive, and the local
   module if it was baked.
3. Personal or admin keys (`phx_...` and anything that is not `phc_`) are
   refused by `public_project_key()` and will not be written or used.

A Linux box cannot produce the Windows EXE; confirm the bundle list on the
Windows builder before a release.

## Synthetic / test markers

Set `VS_ANALYTICS_ENVIRONMENT=synthetic` (or `test`) and
`VS_ANALYTICS_TEST_RUN_ID=<token>` to tag events so they can be excluded from
production insights. Those properties are enum/token-allowlisted; arbitrary
text is dropped. Shipped clients set `environment=production` by default. Filter production dashboards with `environment = production` (or `environment != synthetic`).

## Step chain (nonlinear)

These are independent steps related by the same anonymous `install_id` / `session_id`. There is no required order, and none of the later steps is implied by an earlier one.

1. **Daily opens** — `app_open` (`platform=desktop` or `web`).
2. **Actual chat occurred** — `chat_submit` means the user confirmed Send. `chat_success` means a server reply completed for that `chat_id`. A local typo-clarify (web) can emit `chat_submit` with no success/failure, because no model call ran. Old `chat_*` pilot events (for example `chat_user_message`, `chat_flash_ok`) are still dropped by the PostHog allowlist.
3. **Video generated** — `generation_start` then `generation_success` (or `generation_failed` for an irreversible failure). Compare/batch is one start per model/job.
4. **Added the web app to the phone home screen** — **not observable as an in-product install.** The web app has no Web App Manifest, no service worker, and no install CTA, so `beforeinstallprompt` / `appinstalled` / accepted-or-dismissed are not instrumented and must not be faked. iOS "Add to Home Screen" cannot be reliably confirmed. Web may emit `pwa_standalone_launch` when the page is already running as `display-mode: standalone` or `navigator.standalone` (a later launch signal only). Desktop has no such event.
5. **Social sharing** — **no existing share feature.** There is no `navigator.share`, no social-network buttons, and no share-complete callback. Clipboard copy of task IDs or prompts is not a social publish and is not captured. Do not invent `share_published`.

Dashboard notes: filter `environment = production` for live traffic (exclude `synthetic` / `test`). Do not treat GitHub download counts as DAU. Do not compute revenue from these events.

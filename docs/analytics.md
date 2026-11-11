# Video Studio desktop — usage analytics

Minimal PostHog *capture* of critical events. This is not a PostHog SDK install:
autocapture, session replay, heatmaps, and person-profile enrichment are off.
No events are sent until the user grants consent **and** a project key is
configured. An empty key is the default in this repository; nothing is live yet.

## Config names (required before any real event can be verified)

Set one of the following. Do not commit a real key.

| Name | Where | Value |
| --- | --- | --- |
| `VS_POSTHOG_PROJECT_KEY` | Environment (dev, CI, or the EXE build machine) | PostHog project API key, starts with `phc_` |
| `VS_POSTHOG_HOST` | Environment | Ingest host, usually `https://us.i.posthog.com` or `https://eu.i.posthog.com` |
| `POSTHOG_PROJECT_KEY` / `POSTHOG_HOST` | `analytics_config.py` (committed defaults stay empty) | Same meaning as the env vars |
| `POSTHOG_PROJECT_KEY` / `POSTHOG_HOST` | Untracked `analytics_config_local.py` | Local override, gitignored |

The client posts to `{VS_POSTHOG_HOST}/i/v0/e/`. Until a key is present, events
may be queued on disk after consent (`%LOCALAPPDATA%/PowerTokensWan/analytics.json`)
but they are not uploaded. Offline tests are not a live integration.

Build note: PyInstaller picks up `analytics.py` via the `app.py` import. To ship
a key in a release EXE, inject `VS_POSTHOG_PROJECT_KEY` at build time or generate
`analytics_config_local.py` in the build workspace; do not paste it into git.

## Event schema

Shared properties on every accepted event:

- `platform` = `desktop`
- `app_version` (for example `1.16`)
- `session_id` — one id per process, or a new id after 30 minutes idle. Switching
  tabs does **not** start a session or fire `app_open`.
- `install_id` — anonymous UUID stored in `analytics.json`
- `account_linked` — `true` only when a hashed internal user id is present

`distinct_id` is the install id, or `ptu_` plus a SHA-256 prefix of the
PowerTokens `user_id` (never the raw id, never email, never `sub` if it looks
like an email).

| Event | When it fires | Extra properties |
| --- | --- | --- |
| `app_open` | Once per session, at process start (after consent, or flushed when consent is granted) | — |
| `login_success` | Once per session after a successful OAuth sign-in, not on startup session restore | `key_applied` (boolean) |
| `generation_start` | `Client.submit` for a *new* task (Generate, Compare, or Batch). Resume / download-from-link are not starts | `generation_id`, `model`, `surface` (`generate` / `compare` / `batch`), optional `requested_duration_s` |
| `generation_success` | That submit later finishes | `generation_id`, `model`, `surface`, `duration_ms` (wall time) |
| `generation_failed` | That submit raises | same, plus `error_code` from a safe allowlist (`AUTH`, `QUOTA`, …). Raw error text is never sent |
| `video_download` | Not fired for the automatic save that is already part of a successful desktop generate. Reserved for an explicit user download if one is added later | `model`, `surface` |

Chat-pilot style events are **not** sent to PostHog.

Never sent: prompts, API keys, tokens, OAuth codes, URL query strings, email,
name, avatar, images, videos, credits, or raw error / HTTP bodies.

## Consent and opt-out

- First launch shows a notice with “Share anonymous stats” / “Don’t share”.
- Until the user chooses, events are held in a bounded local queue (max 50) and
  not uploaded.
- Denied / toggle off: the queue is dropped and later `track` calls are no-ops.
- The persistent checkbox lives on the API Key tab, under “Save on this PC”.

The client fails open: a network error never blocks the UI; the oldest events
are dropped if the queue is full.

## Dashboard (PostHog) — after a real project key is live

These are setup notes, not proof that data is flowing.

1. **Daily opens** — insight, trends, event `app_open`, count, day interval,
   filter `platform = desktop` (and/or web, once the web client uses the same
   project). Unique users variant: unique `install_id` or `distinct_id`.
2. **DAU** — unique `distinct_id` (or `install_id`) per day on any critical
   event, preferably `app_open`. Do not treat GitHub EXE `download_count` as DAU.
3. **Funnel** — `app_open` → `login_success` → `generation_start` →
   `generation_success`. On desktop, do not expect a trailing `video_download`
   step; the file is saved as part of `generation_success`.
4. **Retention** — unique `install_id`, weekly recurring, first event `app_open`
   or any critical event.
5. **Generation health** — `generation_success` / (`generation_start`) by
   `model` and `surface`; `generation_failed` broken down by `error_code`.

Do not compute revenue, credits, or spend from these client events. Billing
stays on the PowerTokens backend.

## Tests

```text
python -m unittest tests.test_analytics -v
python -m unittest discover -s tests -v
```

Coverage includes the property allowlist, opt-out, transport failure, duplicate
`app_open` / generation terminal events, and “no project key ⇒ no HTTP”.

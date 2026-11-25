# Video Studio desktop — usage analytics

Minimal PostHog *capture* of critical events. This is not a PostHog SDK install:
autocapture, session replay, heatmaps, and person-profile enrichment are off.

Capture starts automatically when a project key is
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
other host is ignored (no traffic). Until a key is present, events are
not queued for later upload.

### Desktop packaging

`app.py` imports `analytics`, and `analytics.py` imports `analytics_config` at
module level so PyInstaller can see both. Build-EXE.bat and the Windows
workflows also pass `--hidden-import analytics --hidden-import analytics_config`.
The project key is still injected at build or runtime; it is not baked into
git. A release EXE will not send traffic until that key is present.

### Project settings (after a key exists, before claiming anonymity)

Per [Controlling data collection](https://posthog.com/docs/privacy/data-collection)
and [data storage](https://posthog.com/docs/privacy/data-storage):

- Every event sets `$process_person_profile: false`.
- Events omit `$geoip_disable` so the existing GeoIP transformation
  (`template-geoip`, execution order 1) can enrich country server-side.
- A separate Filter Properties project transformation after enrichment will keep
  only `$geoip_country_code` and `$geoip_country_name` (and their
  `$initial_geoip_country_*` counterparts if they appear). City, subdivision,
  coordinates, postal code, timezone, and continent are not kept.
- `$ip` is omitted; the client never sends a client-generated IP or coordinates.
  **Discard client IP data** (`anonymize_ips`) is already enabled in the project.
  Ingest still sees the request IP in transit; this client does **not** claim IP
  anonymity.
- Historical events cannot be backfilled with country. Only events captured
  after this change ships will have country.

## Default-on policy (2026-11-12)

Analytics starts automatically with an official public project key. Stored denied
and unset states migrate to granted on load. There is no consent banner or
account toggle. Without a destination, no events are queued or sent.

## Identity (MVP)

`distinct_id` is a random install UUID created locally, plus a
process/session UUID. This MVP does **not** link events to a PowerTokens
account. A short sequential `user_id` would not be anonymous even if hashed.
Login still emits `login_success` as a funnel step, without an account id.

## Event schema

Shared properties: `platform=desktop`, `app_version`, `session_id`,
`install_id`. Switching tabs does not fire `app_open` (it may fire
`surface_open` once per tab per session). Losing focus pauses `active_ms`
but does not end the session. A new session starts after 30 minutes idle, or
after the window closes; `session_end` is emitted first. The next startup
ends an unterminated previous session (crash recovery) before `app_open`.

| Event | When it fires | Extra properties |
| --- | --- | --- |
| `app_open` | Once per session, on startup | — |
| `session_end` | Once per `session_id` on window close, after 30 minutes idle, or on the next start if a previous session was not ended (crash) | `duration_ms` (wall time), `active_ms` (time the window was focused/visible). Integers, each capped at 12 hours |
| `login_start` | User started the OAuth sign-in (login button). Not a completed sign-in | — |
| `login_success` | Once per session after a successful OAuth sign-in, not on session restore | `key_applied` (boolean only) |
| `login_failed` | OAuth/login finished unsuccessfully | safe `error_code` only |
| `logout` | User confirmed Sign out | — |
| `billing_click` | User clicked through to the official credits/billing page. Not a completed payment | `source`: `header`, `account`, `quota`, `credits_chip`, or `chat` |
| `surface_open` | Once per surface per session when the user actually opens that tab | `surface`: `generate`, `chat`, `compare`, `batch`, `history`, `keys` |
| `key_verified` | Advanced Key-pool verify-and-save succeeded | — |
| `key_verify_failed` | That same verify-and-save failed | safe `error_code` only |
| `language_set` | User manually changed the UI language (not first-run detection) | `language`: `zh`, `en`, `es`, `pt` (unknown codes dropped; `ja`/`ko` are allowed for the paired web app but not offered in this UI) |
| `ai_plan_click` | User clicks the AI-plan help button on Generate | `source=generate` |
| `ai_plan_applied` | User applies a Chat plan to the Generate form | `surface=generate`, `source=ai_plan` after entering through the AI-plan button, otherwise `source=chat` |
| `generation_start` | New `Client.submit` only (Generate, Compare, or Batch). Resume is not a start | `generation_id` (= task `local_id`), `model` (catalog-like token only), `surface`, optional `requested_duration_s`, `input_mode` (`t2v` or `i2v` when a first-frame image/url was or was not attached; omitted if unknown). The image and prompt are never sent. Optional `source=ai_plan` for a following generation using the applied AI plan |
| `generation_success` | That submit, or a later **resume** of the same `local_id`, finishes | `duration_ms`, optional `input_mode`, optional `source=ai_plan` carried from the applied plan |
| `generation_failed` | Only irreversible failures (`AUTH`, `QUOTA`, `PARAM`, `REJECTED`, `UNCERTAIN` with no task, `SERVER_FAILED`, …). Pause, timeout, and query-down are not terminal, so a later resume can still record success | safe `error_code` only, optional `input_mode`, optional `source=ai_plan` carried from the applied plan |
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
into a release EXE or Mac app:

1. Set `VS_POSTHOG_PROJECT_KEY` (and optionally `VS_POSTHOG_HOST`) on the build
   machine, or write an untracked `analytics_config_local.py` with those names.
2. `Build-EXE.bat`, `Build-Mac.sh`, and the Windows and macOS workflows call
   `tools/prepare_analytics_config.py` and add `--hidden-import analytics_config_local`
   when that file exists. `build-windows.yml` and `Build-Mac.sh` also check that
   `analytics` and `analytics_config` are present in the PyInstaller archive, and
   the local module if it was baked. The Mac workflow bakes the token on
   `workflow_dispatch` and `v*` tags, not on pull-request builds.
3. Personal or admin keys (`phx_...` and anything that is not `phc_`) are
   refused by `public_project_key()` and will not be written or used.

A Linux box cannot produce the Windows EXE or the Mac app; confirm the bundle
list on the respective builder before a release.

## Synthetic / test markers

Set `VS_ANALYTICS_ENVIRONMENT=synthetic` (or `test`) and
`VS_ANALYTICS_TEST_RUN_ID=<token>` to tag events so they can be excluded from
production insights. Those properties are enum/token-allowlisted; arbitrary
text is dropped. Shipped clients set `environment=production` by default. Filter production dashboards with `environment = production` (or `environment != synthetic`).

## Step chain (nonlinear)

These are independent steps related by the same anonymous `install_id` / `session_id`. There is no required order, and none of the later steps is implied by an earlier one.

1. **Daily opens and session length** — `app_open` (`platform=desktop` or `web`) and later `session_end` with `duration_ms` / `active_ms`.
2. **Actual chat occurred** — `chat_submit` means the user confirmed Send. `chat_success` means a server reply completed for that `chat_id`. A local typo-clarify (web) can emit `chat_submit` with no success/failure, because no model call ran. Old `chat_*` pilot events (for example `chat_user_message`, `chat_flash_ok`) are still dropped by the PostHog allowlist.
3. **Video generated** — `generation_start` then `generation_success` (or `generation_failed` for an irreversible failure), with `input_mode` `t2v` or `i2v` when known. Compare/batch is one start per model/job.
4. **Added the web app to the phone home screen** — a **web-only** surface. The web app now has an Add to Home Screen offer; measure it there as `pwa_offer_shown` → `pwa_install_click` → `pwa_install_accepted` (Chromium `userChoice=accepted` or `appinstalled`, deduped). iOS only opens a guide sheet, so the add itself cannot be confirmed until a later `pwa_standalone_launch`. Desktop has no home-screen or social-share UI and does not emit `pwa_*` events.
5. **Social sharing** — **no existing share feature.** There is no `navigator.share`, no social-network buttons, and no share-complete callback. Clipboard copy of task IDs or prompts is not a social publish and is not captured. Do not invent `share_published`.

Dashboard notes: filter `environment = production` for live traffic (exclude `synthetic` / `test`). Do not treat GitHub download counts as DAU. Do not compute revenue from these events.

Desktop attribution is attached centrally at enqueue for every event, including
session_end. First and last touch come from utm_attr in the client data directory.
Only the five UTM fields are accepted, using plain initial_utm_* for first touch.
Values are trimmed ASCII tokens of at most 80 characters; unsafe values are dropped
and source/medium/campaign fall back to product defaults. Click IDs are excluded.

Payment funnel: payment_start, payment_checkout_opened (validated create redirect),
payment_failed (normalized code), payment_success (confirmed paid polling status).
`payment_package_select` records explicit card activations only, with `selection`
(`preset-5`, `preset-20`, `preset-50`, `custom`) and `package_id` (35, 22, 23,
33 respectively, serialized as catalog strings). `payment_channel_select` records
explicit method activations with `channel` (`limeng` or `paypal`). Initial $5 and
limeng defaults emit neither selection event.
Live official package ids are 35/22/23 (fixed $5/$20/$50) and custom 33
(amount_type 2, $5–$2000). Only the default fixed $5 card has the Recommended
badge. Package 24 is historical only, not a current official package; the
sanitizer continues to accept it.

`payment_dialog_closed` records user dismissal/withdrawal with a real boolean
`pending_checkout`. `payment_cancelled` records official status 2 from polling or
the manual check once per pending checkout, with its `package_id` and `channel`,
then clears pending state. Cancellation does not emit `payment_failed`.

Only `payment_failed` accepts these strictly validated diagnostics; unknown values
are omitted:

- `fail_stage`: `transport` (no usable response), `response` (HTTP, JSON/body or
  business rejection), or `checkout` (missing reference or unsafe redirect).
- `http_status`: integer 100–599; `api_status`: integer 0–99999 from business status.
- `message_category`: `absent`, `authentication` (token, unauthori, credential,
  登录 or 令牌, case insensitive), or `other-redacted`. Raw messages are excluded.
- `has_payment_ref` and `redirect_ok`: real booleans.
- `redirect_protocol`: `https`, `http`, `other`, `invalid`.
- `redirect_host`: `gateway.powerwinpay.com`, `powertokens.ai`,
  `www.powertokens.ai`, `unlisted`, `invalid`.

`error_code` is NETWORK for transport failures, AUTH for HTTP/business 401/403,
VALIDATION for checkout gates, and UNKNOWN for other response failures. Payload
validation before Buy emits no payment event. HTTP-error body inspection is bounded
and retains only business status and message category. The create POST remains
exactly `{amount, package_id, payment_channel}`.

Only catalog package IDs and limeng/paypal channels are recorded. No monetary,
order, checkout URL, account, or credential data is included. Tracking fails open.


## Funnel and login diagnostics

`example_click` and `prompt_edited` accept `surface` and `example_id` from shopping, canyon, seal, locker. Example cycles live in memory. Manual edits emit once per cycle. Submission metadata accepts `from_example`, `prompt_edited`, `prompt_matches_example`, `example_id`, `is_repeat`, `after_success`, and `text_len_bucket` from b0_20, b21_50, b51_100, b101_200, b201_plus. Prompt comparisons normalize whitespace and use bundled translations.

`quota_spent` and `quota_estimate` accept integers from 0 through 1000000. The latter uses an existing catalog estimate. `quota_spent` is the known job cost at generation success, omitted when unknown or invalid. It is not a wallet delta. Shared balance refreshes are never used. Never use credits, balance, amount, or credit-prefixed property names. Forbidden keys remain rejected even if allowlisted.

`login_start` accepts header, account, chat, generate, billing, quota, credits_chip, dialog, other sources. `login_return_unsigned` accepts back, reopen, missing_code reasons. Web hooks pending PKCE on pageshow and reopen and emits missing_code from the callback. Desktop uses ephemeral loopback OAuth and emits missing_code when that callback lacks a code.

Every event includes device_class and os enums. Web also includes browser, is_pwa, is_in_app_browser. No raw user-agent is captured. Desktop has no example chips. `payment_success` retains its existing notification and deduplication flow.

Desktop update checks emit update_check_start, update_available, update_download_start, update_download_success, update_download_failed. update_version accepts only a short numeric dotted version. Failures use safe error_code values.

## Unshipped desktop captures

| Event | Trigger | Properties |
| --- | --- | --- |
| `payment_reconfirm_shown` | Reconfirm notice displayed | `reason=resume` for returning session, otherwise `signin` |
| `payment_reconfirm_continue` | Reconfirm CTA starts login | — |

Captures fail open; forbidden fields remain rejected even if allowlisted.

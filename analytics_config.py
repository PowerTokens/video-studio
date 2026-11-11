"""PostHog project settings for Video Studio desktop.

Leave the project key empty in git. Fill it at build or runtime; an empty key
means no events are sent. Do not put personal credentials here.

Config names (first non-empty wins for the key and host):

- Environment: VS_POSTHOG_PROJECT_KEY, VS_POSTHOG_HOST
- Optional untracked file: analytics_config_local.py with the same attributes
- The defaults below (host only)

Only a public PostHog *project* API key (phc_...) is accepted. Personal or
admin keys (for example phx_...) are ignored so they cannot be shipped or used
for capture.

Optional markers (not required in production):

- VS_ANALYTICS_ENVIRONMENT or ANALYTICS_ENVIRONMENT: synthetic | test | production
- VS_ANALYTICS_TEST_RUN_ID or ANALYTICS_TEST_RUN_ID: allowlisted token
"""
import os
import re

# Public PostHog project API key (phc_...). Empty = telemetry disabled.
POSTHOG_PROJECT_KEY = ''

# Cloud ingest host. Only official US/EU ingest hosts are accepted by the client.
# Use https://eu.i.posthog.com for an EU project.
POSTHOG_HOST = 'https://us.i.posthog.com'

ALLOWED_INGEST_HOSTS = (
    'https://us.i.posthog.com',
    'https://eu.i.posthog.com',
)

_PHC_RE = re.compile(r'^phc_[A-Za-z0-9_-]{4,}$')
_ENV_RE = re.compile(r'^(synthetic|test|production)$')
_RUN_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')


def public_project_key(raw) -> str:
    """Return raw only if it looks like a public project capture key."""
    key = str(raw or '').strip()
    if _PHC_RE.match(key):
        return key
    return ''


def official_host(raw) -> str:
    host = str(raw or '').strip().rstrip('/')
    return host if host in ALLOWED_INGEST_HOSTS else ''


def _marker_environment(*candidates) -> str:
    for raw in candidates:
        text = str(raw or '').strip().lower()
        if _ENV_RE.match(text):
            return text
    return ''


def _marker_test_run(*candidates) -> str:
    for raw in candidates:
        text = str(raw or '').strip()
        if text and _RUN_RE.match(text):
            return text
    return ''


def load():
    """Resolve key and host without logging or returning secrets to callers' logs."""
    key = public_project_key(os.environ.get('VS_POSTHOG_PROJECT_KEY'))
    host = official_host(os.environ.get('VS_POSTHOG_HOST'))
    environment = _marker_environment(os.environ.get('VS_ANALYTICS_ENVIRONMENT'))
    test_run_id = _marker_test_run(os.environ.get('VS_ANALYTICS_TEST_RUN_ID'))
    local = None
    try:
        import analytics_config_local as local  # type: ignore
        key = key or public_project_key(getattr(local, 'POSTHOG_PROJECT_KEY', ''))
        host = host or official_host(getattr(local, 'POSTHOG_HOST', ''))
        environment = environment or _marker_environment(getattr(local, 'ANALYTICS_ENVIRONMENT', ''))
        test_run_id = test_run_id or _marker_test_run(getattr(local, 'ANALYTICS_TEST_RUN_ID', ''))
    except ImportError:
        pass
    key = key or public_project_key(POSTHOG_PROJECT_KEY)
    host = host or official_host(POSTHOG_HOST) or 'https://us.i.posthog.com'
    environment = environment or _marker_environment(
        os.environ.get('ANALYTICS_ENVIRONMENT'),
        getattr(local, 'ANALYTICS_ENVIRONMENT', '') if local is not None else '',
    )
    test_run_id = test_run_id or _marker_test_run(
        os.environ.get('ANALYTICS_TEST_RUN_ID'),
        getattr(local, 'ANALYTICS_TEST_RUN_ID', '') if local is not None else '',
    )
    return {
        'project_key': key,
        'host': host,
        'environment': environment,
        'test_run_id': test_run_id,
    }

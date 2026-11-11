"""PostHog project settings for Video Studio desktop.

Leave the project key empty in git. Fill it at build or runtime; an empty key
means no events are sent. Do not put personal credentials here.

Config names (first non-empty wins for the key and host):

- Environment: VS_POSTHOG_PROJECT_KEY, VS_POSTHOG_HOST
- Optional untracked file: analytics_config_local.py with the same attributes
- The defaults below (host only)

The project key is the PostHog *project* API key (phc_...), which is safe to
ship in a desktop build, not a personal API key or a private write token.
"""
import os

# Public PostHog project API key (phc_...). Empty = telemetry disabled.
POSTHOG_PROJECT_KEY = ''

# Cloud ingest host. Use https://eu.i.posthog.com for an EU project.
POSTHOG_HOST = 'https://us.i.posthog.com'


def load():
    """Resolve key and host without logging or returning secrets to callers' logs."""
    key = (os.environ.get('VS_POSTHOG_PROJECT_KEY') or '').strip()
    host = (os.environ.get('VS_POSTHOG_HOST') or '').strip()
    try:
        import analytics_config_local as local  # type: ignore
        key = key or str(getattr(local, 'POSTHOG_PROJECT_KEY', '') or '').strip()
        host = host or str(getattr(local, 'POSTHOG_HOST', '') or '').strip()
    except ImportError:
        pass
    key = key or str(POSTHOG_PROJECT_KEY or '').strip()
    host = host or str(POSTHOG_HOST or '').strip() or 'https://us.i.posthog.com'
    return {'project_key': key, 'host': host.rstrip('/')}

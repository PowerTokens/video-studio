#!/usr/bin/env python3
"""Write untracked analytics_config_local.py from env or a dotenv file.

Only a public project capture key (phc_...) is accepted. Personal/admin keys
are rejected. This script never prints secret values.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import analytics_config  # noqa: E402

OUT_NAME = 'analytics_config_local.py'


def _read_dotenv(path: Path) -> dict[str, str]:
    values = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--from-env-file', default='', help='Optional dotenv path')
    parser.add_argument('--out', default='', help='Output path (default: repo analytics_config_local.py)')
    parser.add_argument('--allow-empty', action='store_true',
                        help='Exit 0 without writing when no public key is present')
    args = parser.parse_args()

    file_vals = _read_dotenv(Path(args.from_env_file)) if args.from_env_file else {}
    key = analytics_config.public_project_key(
        os.environ.get('VS_POSTHOG_PROJECT_KEY')
        or file_vals.get('VS_POSTHOG_PROJECT_KEY')
        or file_vals.get('POSTHOG_PROJECT_KEY')
        or file_vals.get('POSTHOG_PROJECT_TOKEN')
    )
    host = analytics_config.official_host(
        os.environ.get('VS_POSTHOG_HOST')
        or file_vals.get('VS_POSTHOG_HOST')
        or file_vals.get('POSTHOG_HOST')
    ) or 'https://us.i.posthog.com'

    raw_candidates = [
        os.environ.get('VS_POSTHOG_PROJECT_KEY'),
        file_vals.get('VS_POSTHOG_PROJECT_KEY'),
        file_vals.get('POSTHOG_PROJECT_KEY'),
        file_vals.get('POSTHOG_PROJECT_TOKEN'),
    ]
    rejected = any(str(v or '').strip() and not analytics_config.public_project_key(v) for v in raw_candidates)

    if not key:
        if rejected:
            print('prepare_analytics_config: refused a non-project (non-phc_) key; nothing written', file=sys.stderr)
            return 2
        if args.allow_empty:
            print('prepare_analytics_config: no public project key; skip write')
            return 0
        print('prepare_analytics_config: no public project key provided', file=sys.stderr)
        return 1

    out = Path(args.out) if args.out else ROOT / OUT_NAME
    body = (
        '# Generated local PostHog project (public capture) settings. Do not commit.\n'
        '# Personal or admin API keys must never be placed here.\n'
        f'POSTHOG_PROJECT_KEY = {key!r}\n'
        f'POSTHOG_HOST = {host!r}\n'
    )
    out.write_text(body, encoding='utf-8')
    try:
        out.chmod(0o600)
    except OSError:
        pass
    print(f'prepare_analytics_config: wrote {out.name} (key=phc_… len={len(key)}, host={host})')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

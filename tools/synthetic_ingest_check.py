#!/usr/bin/env python3
"""Send marked synthetic events through the real desktop Analytics client.

Does not perform paid generation or use real user data. HTTP 200 is not proof
of ingestion; receipts are written for later PostHog UI verification.
Never prints or writes the project token.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path
from urllib.request import urlopen as real_urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import analytics  # noqa: E402
import analytics_config  # noqa: E402

REDACT = '<redacted-project-key>'


def _redact(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in {'api_key', 'token', 'authorization', 'Authorization'}:
                raw = str(v or '')
                out[k] = REDACT
            else:
                out[k] = _redact(v)
        return out
    if isinstance(obj, list):
        return [_redact(x) for x in obj]
    if isinstance(obj, str) and obj.startswith('phc_'):
        return REDACT
    return obj


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--from-env-file', default='')
    parser.add_argument('--out', required=True, help='Receipt JSON path')
    parser.add_argument('--test-run-id', required=True)
    parser.add_argument('--environment', default='synthetic')
    args = parser.parse_args()

    if args.from_env_file:
        # Load into env without printing.
        from tools.prepare_analytics_config import _read_dotenv
        vals = _read_dotenv(Path(args.from_env_file))
        os.environ.setdefault('VS_POSTHOG_PROJECT_KEY', vals.get('POSTHOG_PROJECT_TOKEN') or vals.get('POSTHOG_PROJECT_KEY') or '')
        os.environ.setdefault('VS_POSTHOG_HOST', vals.get('POSTHOG_HOST') or '')

    os.environ['VS_ANALYTICS_ENVIRONMENT'] = args.environment
    os.environ['VS_ANALYTICS_TEST_RUN_ID'] = args.test_run_id

    cfg = analytics_config.load()
    if not cfg.get('project_key') or cfg.get('host') not in analytics.ALLOWED_INGEST_HOSTS:
        print('synthetic_ingest_check: no public project key or official host; abort', file=sys.stderr)
        return 1

    receipts = []

    def wrapped_urlopen(request, timeout=None):
        raw = request.data.decode('utf-8') if isinstance(request.data, (bytes, bytearray)) else str(request.data)
        parsed = json.loads(raw)
        status = None
        body_text = ''
        error = None
        started = time.time()
        try:
            response = real_urlopen(request, timeout=timeout)
            try:
                status = response.getcode()
                body_text = response.read(2048).decode('utf-8', 'replace')
            finally:
                try:
                    response.close()
                except Exception:
                    pass

            class _Resp:
                def __enter__(self_inner):
                    return self_inner
                def __exit__(self_inner, *exc):
                    return False
                def read(self_inner, n=-1):
                    return b''
                def getcode(self_inner):
                    return status

            wrapped = _Resp()
            wrapped.status = status
            return wrapped
        except Exception as exc:
            error = type(exc).__name__
            status = getattr(exc, 'code', None)
            try:
                body_text = exc.read().decode('utf-8', 'replace') if hasattr(exc, 'read') else str(exc)
            except Exception:
                body_text = str(exc)
            raise
        finally:
            elapsed_ms = int((time.time() - started) * 1000)
            receipts.append({
                'client': 'desktop',
                'url': getattr(request, 'full_url', None) or request.get_full_url(),
                'http_status': status,
                'response_body': body_text[:1000],
                'error': error,
                'elapsed_ms': elapsed_ms,
                'event': parsed.get('event'),
                'distinct_id': parsed.get('distinct_id'),
                'timestamp': parsed.get('timestamp'),
                'properties': _redact(parsed.get('properties') or {}),
                'request_body_redacted': _redact(parsed),
                'note': 'HTTP 200 is not proof of ingestion; confirm this event in the PostHog UI.',
            })

    data_dir = Path(tempfile.mkdtemp(prefix='vs-analytics-synthetic-'))
    analytics.reset_client()
    # Patch urlopen used by the client module.
    import urllib.request as ur
    original = ur.urlopen
    ur.urlopen = wrapped_urlopen
    try:
        client = analytics.boot(
            data_dir=data_dir,
            app_version='1.16-synthetic',
            config={
                'project_key': cfg['project_key'],
                'host': cfg['host'],
                'environment': args.environment,
                'test_run_id': args.test_run_id,
            },
        )
        client.set_consent(True)
        client.flush()
        client.notify_login_success(key_applied=False)
        client.flush()
        ok_id = f'{args.test_run_id}-ok'
        fail_id = f'{args.test_run_id}-fail'
        client.notify_generation_start(ok_id, model='synthetic-test-model', surface='generate',
                                       requested_duration_s=5)
        client.notify_generation_finish(ok_id, True, model='synthetic-test-model', surface='generate',
                                        duration_ms=1234)
        client.notify_generation_start(fail_id, model='synthetic-test-model', surface='compare')
        client.notify_generation_finish(fail_id, False, model='synthetic-test-model', surface='compare',
                                        error_code='PARAM')
        client.notify_video_download(model='synthetic-test-model', surface='generate')
        client.flush()
    finally:
        ur.urlopen = original
        analytics.reset_client()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'client': 'desktop',
        'environment': args.environment,
        'test_run_id': args.test_run_id,
        'host': cfg['host'],
        'project_id': os.environ.get('POSTHOG_PROJECT_ID') or '728258',
        'app_version': '1.16-synthetic',
        'data_dir_name': data_dir.name,
        'distinct_id': next((r['distinct_id'] for r in receipts if r.get('distinct_id')), None),
        'events': [r['event'] for r in receipts],
        'http_statuses': [r['http_status'] for r in receipts],
        'note': 'HTTP 200 is not proof of ingestion. Filter PostHog by environment=synthetic and this test_run_id.',
        'receipts': receipts,
    }
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f'synthetic_ingest_check desktop: {len(receipts)} requests, statuses={payload["http_statuses"]}, wrote {out}')
    return 0 if receipts and all(s == 200 for s in payload['http_statuses']) else 1


if __name__ == '__main__':
    raise SystemExit(main())

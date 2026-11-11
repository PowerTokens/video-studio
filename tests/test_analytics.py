import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import unittest.mock

import analytics


class MemoryClock:
    def __init__(self, start=1_700_000_000.0):
        self.value = start

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class AnalyticsCoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.sent = []
        self.clock = MemoryClock()
        self.client = analytics.Analytics(
            self.tmp, transport=self.sent.append, clock=self.clock, app_version='1.16',
            config={'project_key': 'phc_test', 'host': 'https://example.invalid'},
        )

    def grant(self):
        self.client.set_consent(True)
        # set_consent already captures app_open and flushes. Drain sent for later asserts.
        self.client.flush()

    def test_sanitize_drops_forbidden_and_unknown_keys(self):
        props = analytics.sanitize_properties('generation_failed', {
            'model': 'wan3.0-video',
            'error_code': 'QUOTA',
            'prompt': 'secret prompt text',
            'key': 'sk-live-secret',
            'api_key': 'sk-live-secret',
            'email': 'user@example.com',
            'token': 'oauth-token',
            'access_token': 'aaa',
            'code': 'oauth-code',
            'error': 'raw HTTP 401 body with key sk-xxx',
            'error_detail': 'traceback',
            'url': 'https://example.com/callback?code=abc&state=xyz',
            'search': '?code=abc',
            '$current_url': 'https://x/?gclid=1',
            'credits': 12,
            'name': 'Ada',
            'generation_id': 'gen1',
            'surface': 'generate',
            'extra_unknown': 'nope',
        })
        self.assertIsNotNone(props)
        blob = json.dumps(props)
        self.assertNotIn('secret prompt', blob)
        self.assertNotIn('sk-live', blob)
        self.assertNotIn('user@example.com', blob)
        self.assertNotIn('oauth-token', blob)
        self.assertNotIn('oauth-code', blob)
        self.assertNotIn('raw HTTP', blob)
        self.assertNotIn('traceback', blob)
        self.assertNotIn('gclid', blob)
        self.assertNotIn('code=abc', blob)
        self.assertNotIn('Ada', blob)
        self.assertNotIn('extra_unknown', blob)
        self.assertEqual(props['model'], 'wan3.0-video')
        self.assertEqual(props['error_code'], 'QUOTA')
        self.assertEqual(props['platform'], 'desktop')
        self.assertEqual(props['generation_id'], 'gen1')

    def test_unknown_event_rejected(self):
        self.assertIsNone(analytics.sanitize_properties('chat_user_message', {'len_bucket': 'short'}))
        self.assertIsNone(analytics.sanitize_properties('$pageview', {}))
        self.assertIsNone(analytics.sanitize_properties('$snapshot', {}))

    def test_unsafe_error_code_falls_back_to_unknown(self):
        props = analytics.sanitize_properties('generation_failed', {
            'generation_id': 'g',
            'error_code': 'HTTP 401: invalid token sk-secret',
        })
        self.assertEqual(props['error_code'], 'UNKNOWN')
        self.assertNotIn('sk-secret', json.dumps(props))

    def test_opaque_account_id_hashes_and_rejects_email(self):
        a = analytics.opaque_account_id('usr_12345')
        b = analytics.opaque_account_id('usr_12345')
        self.assertEqual(a, b)
        self.assertTrue(a.startswith('ptu_'))
        self.assertNotIn('usr_12345', a)
        self.assertIsNone(analytics.opaque_account_id('ada@example.com'))
        self.assertIsNone(analytics.opaque_account_id(''))
        self.assertIsNone(analytics.opaque_account_id(None))

    def test_opt_out_drops_events_and_clears_queue(self):
        self.client.track('app_open', {})
        self.assertTrue(self.client.pending())
        self.client.set_consent(False)
        self.assertEqual(self.client.pending(), [])
        self.assertFalse(self.client.track('app_open', {}))
        self.assertFalse(self.client.notify_login_success('usr_1'))
        self.assertEqual(self.sent, [])

    def test_consent_unset_holds_then_flush_on_grant(self):
        self.client.capture_app_open()
        self.assertEqual(self.sent, [])
        self.assertEqual(len(self.client.pending()), 1)
        self.client.set_consent(True)
        self.client.flush()
        events = [item['event'] for item in self.sent]
        self.assertEqual(events.count('app_open'), 1)

    def test_app_open_once_per_session(self):
        self.grant()
        self.sent.clear()
        # Same session: second open is dropped.
        self.assertFalse(self.client.capture_app_open())
        self.client.flush()
        self.assertEqual(self.sent, [])
        # Idle timeout starts a new session and allows another app_open.
        self.clock.advance(analytics.SESSION_IDLE_S + 1)
        self.assertTrue(self.client.capture_app_open())
        self.client.flush()
        self.assertEqual([item['event'] for item in self.sent], ['app_open'])

    def test_generation_duplicate_prevention(self):
        self.grant()
        self.sent.clear()
        self.assertTrue(self.client.notify_generation_start('g1', model='wan3.0-video'))
        self.assertFalse(self.client.notify_generation_start('g1', model='wan3.0-video'))
        self.assertTrue(self.client.notify_generation_finish('g1', True, model='wan3.0-video', duration_ms=1200))
        self.assertFalse(self.client.notify_generation_finish('g1', False, error_code='QUOTA'))
        self.client.flush()
        names = [item['event'] for item in self.sent]
        self.assertEqual(names, ['generation_start', 'generation_success'])
        self.assertEqual(self.sent[1]['properties']['duration_ms'], 1200)

    def test_login_success_once_per_session_and_hashes_user(self):
        self.grant()
        self.sent.clear()
        self.assertTrue(self.client.notify_login_success('usr_99', key_applied=True))
        self.assertFalse(self.client.notify_login_success('usr_99', key_applied=True))
        self.client.flush()
        self.assertEqual(len(self.sent), 1)
        item = self.sent[0]
        self.assertEqual(item['event'], 'login_success')
        self.assertTrue(item['distinct_id'].startswith('ptu_'))
        self.assertNotIn('usr_99', json.dumps(item))
        self.assertTrue(item['properties']['key_applied'])
        self.assertTrue(item['properties']['account_linked'])
        self.assertNotIn('email', item['properties'])

    def test_transport_failure_fails_open_and_keeps_queue(self):
        def boom(_item):
            raise RuntimeError('network down')
        client = analytics.Analytics(
            self.tmp / 'fail', transport=boom, clock=self.clock, app_version='1.16',
            config={'project_key': 'phc_test', 'host': 'https://example.invalid'},
        )
        client.set_consent(True)
        # First app_open is queued; flush swallows the error.
        client.flush()
        self.assertTrue(client.pending())
        # track() itself must not raise
        self.assertTrue(client.notify_generation_start('gx', model='kling'))

    def test_empty_project_key_does_not_call_http(self):
        hits = []

        def fake_urlopen(*_args, **_kwargs):
            hits.append(1)
            raise AssertionError('should not be called')

        client = analytics.Analytics(
            self.tmp / 'nokey', transport=None, clock=self.clock, app_version='1.16',
            config={'project_key': '', 'host': 'https://us.i.posthog.com'},
        )
        client.set_consent(True)
        with patch('urllib.request.urlopen', fake_urlopen):
            client.capture_app_open()
            client.flush()
        self.assertEqual(hits, [])
        # Event stays queued locally but is not sent.
        self.assertTrue(client.pending())

    def test_queue_is_bounded(self):
        self.client.set_consent(True)
        # Bypass uniqueness by using distinct generation ids.
        for i in range(analytics.QUEUE_MAX + 25):
            self.client._state['consent'] = analytics.CONSENT_GRANTED
            self.client._enqueue({'event': 'generation_start', 'distinct_id': 'x', 'properties': {'generation_id': str(i)}})
        self.assertLessEqual(len(self.client.pending()), analytics.QUEUE_MAX)

    def test_payload_never_includes_url_or_oauth_fields(self):
        self.grant()
        self.sent.clear()
        self.client.track('login_success', {
            'code': 'abc',
            'search': '?code=abc&state=1',
            'email': 'a@b.c',
        })
        self.client.flush()
        blob = json.dumps(self.sent)
        self.assertNotIn('code=abc', blob)
        self.assertNotIn('a@b.c', blob)
        for item in self.sent:
            keys = set(item['properties'])
            self.assertTrue(keys.issubset(analytics.ALLOWED_PROPS | {'platform', 'app_version', 'session_id', 'install_id', 'account_linked', 'key_applied'}))

    def test_video_download_allowlisted(self):
        self.grant()
        self.sent.clear()
        self.assertTrue(self.client.notify_video_download(model='wan3.0-video', surface='generate'))
        self.client.flush()
        self.assertEqual(self.sent[0]['event'], 'video_download')
        self.assertEqual(self.sent[0]['properties']['model'], 'wan3.0-video')

    def test_config_names_come_from_env(self):
        with patch.dict('os.environ', {
            'VS_POSTHOG_PROJECT_KEY': 'phc_from_env',
            'VS_POSTHOG_HOST': 'https://eu.i.posthog.com',
        }, clear=False):
            import importlib
            import analytics_config
            importlib.reload(analytics_config)
            loaded = analytics_config.load()
        self.assertEqual(loaded['project_key'], 'phc_from_env')
        self.assertEqual(loaded['host'], 'https://eu.i.posthog.com')
        # Default committed config is empty (no fabricated live key).
        import importlib
        import analytics_config
        # Restore module defaults after env patch is gone.
        importlib.reload(analytics_config)
        self.assertEqual(analytics_config.POSTHOG_PROJECT_KEY, '')

    def test_boot_singleton_fail_open(self):
        analytics.reset_client()
        client = analytics.boot(data_dir=self.tmp / 'boot', app_version='1.16',
                                transport=self.sent.append, config={'project_key': 'phc_test'})
        self.assertIs(analytics.get_client(), client)
        analytics.set_consent(True)
        self.assertTrue(analytics.is_enabled())
        analytics.reset_client()
        self.assertFalse(analytics.notify_generation_start('z'))


class AnalyticsHookImportTests(unittest.TestCase):
    def test_module_imports_without_network(self):
        import wan_core
        self.assertTrue(hasattr(wan_core, 'Client'))
        import analytics as a
        self.assertIn('app_open', a.EVENTS)


class ClientSubmitHookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.sent = []
        analytics.reset_client()
        analytics.boot(
            data_dir=self.tmp / 'analytics',
            app_version='1.16',
            transport=self.sent.append,
            config={'project_key': 'phc_test', 'host': 'https://example.invalid'},
        )
        analytics.set_consent(True)
        self.sent.clear()

    def tearDown(self):
        analytics.reset_client()

    def test_submit_failure_emits_safe_error_without_prompt(self):
        import wan_core as w
        out = self.tmp / 'out.mp4'
        client = w.Client(data_dir=self.tmp, report=lambda *_: None)
        with unittest.mock.patch.object(w, 'api', return_value=(None, {})):
            with self.assertRaises(w.TaskError):
                client.submit(['key1'], w.payload('super secret prompt text'), out)
        analytics.get_client().flush()
        names = [item['event'] for item in self.sent]
        self.assertIn('generation_start', names)
        self.assertIn('generation_failed', names)
        blob = json.dumps(self.sent)
        self.assertNotIn('super secret prompt text', blob)
        self.assertNotIn('key1', blob)
        failed = next(item for item in self.sent if item['event'] == 'generation_failed')
        self.assertEqual(failed['properties']['error_code'], 'UNCERTAIN')
        self.assertEqual(failed['properties']['surface'], 'generate')

    def test_resume_is_not_counted_as_generation_start(self):
        import wan_core as w
        out = self.tmp / 'out.mp4'
        client = w.Client(data_dir=self.tmp, report=lambda *_: None)
        with unittest.mock.patch.object(client, 'wait', side_effect=w.TaskError('timeout', 'TIMEOUT', 'task_123')):
            with self.assertRaises(w.TaskError):
                client.resume('key', 'task_123', out, record=None)
        analytics.get_client().flush()
        self.assertFalse(any(item['event'].startswith('generation_') for item in self.sent))


if __name__ == '__main__':
    unittest.main()

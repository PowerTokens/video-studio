import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import unittest.mock

import analytics



os.environ.setdefault('VS_ANALYTICS_DISABLE', '1')

class MemoryClock:
    def __init__(self, start=1_700_000_000.0):
        self.value = start

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class AnalyticsCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Never let unit tests talk to the live project, even if a public
        # capture token is present in committed config.
        os.environ['VS_ANALYTICS_DISABLE'] = '1'

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

    def test_sanitize_model_rejects_content_and_unknown_shapes(self):
        self.assertEqual(analytics.sanitize_model('wan3.0-video'), 'wan3.0-video')
        self.assertEqual(analytics.sanitize_model('MiniMax-H3'), 'MiniMax-H3')
        self.assertIsNone(analytics.sanitize_model('a long prompt about a golden retriever'))
        self.assertIsNone(analytics.sanitize_model('user@example.com'))
        self.assertIsNone(analytics.sanitize_model('https://cdn.example/video.mp4'))
        self.assertIsNone(analytics.sanitize_model(''))
        props = analytics.sanitize_properties('generation_start', {
            'generation_id': 'g',
            'model': 'please generate a cat in a hat',
            'surface': 'generate',
        })
        self.assertNotIn('model', props)
        self.assertNotIn('cat in a hat', json.dumps(props))

    def test_opt_out_drops_events_and_clears_queue(self):
        self.assertFalse(self.client.track('app_open', {}))
        self.assertEqual(self.client.pending(), [])
        self.grant()
        self.sent.clear()
        self.client.notify_generation_start('gx', model='kling-v3')
        self.client.flush()
        self.assertTrue(self.sent)
        self.client.set_consent(False)
        self.assertEqual(self.client.pending(), [])
        self.assertFalse(self.client.track('app_open', {}))
        self.assertFalse(self.client.notify_login_success(key_applied=True))
        after = list(self.sent)
        self.client.flush()
        self.assertEqual(self.sent, after)

    def test_consent_unset_does_not_queue_or_identify(self):
        self.client.capture_app_open()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.client.pending(), [])
        self.assertFalse((self.tmp / 'analytics.json').exists() or (self.tmp / analytics.STATE_NAME).exists())
        # No install id is minted before opt-in.
        self.assertEqual(self.client.install_id(), '')
        self.client.set_consent(True)
        self.client.flush()
        events = [item['event'] for item in self.sent]
        self.assertEqual(events.count('app_open'), 1)
        self.assertTrue(self.client.install_id())
        # Events use the random install id, never a hashed account id.
        self.assertEqual(self.sent[0]['distinct_id'], self.client.install_id())
        self.assertFalse(self.sent[0]['distinct_id'].startswith('ptu_'))

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

    def test_login_success_once_per_session_without_account_link(self):
        self.grant()
        self.sent.clear()
        self.assertTrue(self.client.notify_login_success('usr_99', key_applied=True))
        self.assertFalse(self.client.notify_login_success('usr_99', key_applied=True))
        self.client.flush()
        self.assertEqual(len(self.sent), 1)
        item = self.sent[0]
        self.assertEqual(item['event'], 'login_success')
        self.assertEqual(item['distinct_id'], self.client.install_id())
        self.assertNotIn('usr_99', json.dumps(item))
        self.assertFalse(item['distinct_id'].startswith('ptu_'))
        self.assertTrue(item['properties']['key_applied'])
        self.assertNotIn('account_linked', item['properties'])
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
        # Unconfigured: no HTTP and no persistent send queue.
        self.assertEqual(client.pending(), [])

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
            self.assertTrue(keys.issubset(analytics.ALLOWED_PROPS | {'platform', 'app_version', 'session_id', 'install_id', 'key_applied'}))

    def test_official_capture_payload_flags(self):
        body = analytics.build_capture_payload({
            'event': 'app_open',
            'distinct_id': 'install123',
            'properties': {'platform': 'desktop', '$ip': '1.2.3.4'},
            'timestamp': '2026-11-11T15:00:00Z',
        }, 'phc_test')
        self.assertEqual(body['api_key'], 'phc_test')
        self.assertEqual(body['event'], 'app_open')
        self.assertEqual(body['distinct_id'], 'install123')
        self.assertTrue(body['properties']['$geoip_disable'])
        self.assertIs(body['properties']['$process_person_profile'], False)
        self.assertNotIn('$ip', body['properties'])
        blob = json.dumps(body)
        self.assertNotIn('1.2.3.4', blob)

    def test_environment_marker_is_enum_only(self):
        self.assertEqual(analytics.sanitize_environment('synthetic'), 'synthetic')
        self.assertEqual(analytics.sanitize_environment('TEST'), 'test')
        self.assertIsNone(analytics.sanitize_environment('please generate a cat'))
        self.assertIsNone(analytics.sanitize_environment('staging'))
        self.assertIsNone(analytics.sanitize_test_run_id('not a run id with spaces'))
        self.assertEqual(analytics.sanitize_test_run_id('syn-20261111-abc'), 'syn-20261111-abc')
        props = analytics.sanitize_properties('app_open', {
            'environment': 'please generate a cat',
            'test_run_id': 'ok-run_1',
        })
        self.assertNotIn('environment', props)
        self.assertEqual(props['test_run_id'], 'ok-run_1')

    def test_environment_marker_auto_attached(self):
        client = analytics.Analytics(
            self.tmp / 'marked', transport=self.sent.append, clock=self.clock,
            app_version='1.16',
            config={'project_key': 'phc_test', 'host': 'https://us.i.posthog.com',
                    'environment': 'synthetic', 'test_run_id': 'syn-unit-1'},
        )
        client.set_consent(True)
        client.flush()
        self.sent.clear()
        self.assertTrue(client.notify_login_success(key_applied=False))
        client.flush()
        props = self.sent[0]['properties']
        self.assertEqual(props['environment'], 'synthetic')
        self.assertEqual(props['test_run_id'], 'syn-unit-1')

    def test_personal_api_key_is_rejected(self):
        import analytics_config
        self.assertEqual(analytics_config.public_project_key('phx_personal_secret'), '')
        self.assertEqual(analytics_config.public_project_key('sk-admin'), '')
        self.assertTrue(analytics_config.public_project_key('phc_testkey1'))
        client = analytics.Analytics(
            self.tmp / 'phx', transport=None, clock=self.clock, app_version='1.16',
            config={'project_key': 'phx_personal_secret', 'host': 'https://us.i.posthog.com'},
        )
        self.assertFalse(client.has_destination())


    def test_chat_submit_is_not_server_success(self):
        self.grant()
        self.sent.clear()
        self.assertTrue(self.client.notify_chat_submit('c1', model='glm-4.7-flash'))
        self.client.flush()
        self.assertEqual(self.sent[0]['event'], 'chat_submit')
        self.assertEqual(self.sent[0]['properties']['chat_id'], 'c1')
        self.assertEqual(self.sent[0]['properties']['surface'], 'chat')
        self.assertEqual(self.sent[0]['properties']['model'], 'glm-4.7-flash')
        self.assertNotEqual(self.sent[0]['event'], 'chat_success')
        self.sent.clear()
        self.assertTrue(self.client.notify_chat_finish('c1', True, model='glm-4.7-flash'))
        self.client.flush()
        self.assertEqual(self.sent[0]['event'], 'chat_success')
        self.assertEqual(self.sent[0]['properties']['chat_id'], 'c1')

    def test_chat_events_drop_text_and_old_names(self):
        self.assertIsNone(analytics.sanitize_properties('chat_user_message', {'len_bucket': 'short'}))
        self.assertIsNone(analytics.sanitize_properties('chat_flash_ok', {'model': 'glm-4.7-flash'}))
        props = analytics.sanitize_properties('chat_submit', {
            'chat_id': 'c2',
            'prompt': 'secret prompt text',
            'message': 'hello',
            'model': 'write me a cinematic prompt about a cat',
            'surface': 'chat',
        })
        self.assertNotIn('prompt', props)
        self.assertNotIn('message', props)
        self.assertNotIn('model', props)  # sentence rejected
        blob = json.dumps(props)
        self.assertNotIn('secret prompt text', blob)
        self.assertNotIn('hello', blob)

    def test_chat_failed_uses_safe_error_code(self):
        self.grant()
        self.sent.clear()
        self.client.notify_chat_submit('c3')
        self.client.flush()
        self.sent.clear()
        self.assertTrue(self.client.notify_chat_finish(
            'c3', False, error_code='HTTP 401: sk-live-secret please generate a cat'))
        self.client.flush()
        self.assertEqual(self.sent[0]['event'], 'chat_failed')
        self.assertEqual(self.sent[0]['properties']['error_code'], 'UNKNOWN')
        self.assertNotIn('sk-live-secret', json.dumps(self.sent))
        self.assertNotIn('please generate', json.dumps(self.sent))

    def test_chat_dedup_per_id(self):
        self.grant()
        self.sent.clear()
        self.assertTrue(self.client.notify_chat_submit('c4'))
        self.assertFalse(self.client.notify_chat_submit('c4'))
        self.assertTrue(self.client.notify_chat_finish('c4', False, error_code='TIMEOUT'))
        self.assertFalse(self.client.notify_chat_finish('c4', True))
        self.client.flush()
        names = [x['event'] for x in self.sent]
        self.assertEqual(names, ['chat_submit', 'chat_failed'])

    def test_share_and_install_events_not_invented(self):
        # No in-app social share or PWA install CTA exists; do not allow
        # unverified "published" or "installed" events.
        for name in ('share_click', 'share_resolved', 'share_published',
                     'pwa_prompt_shown', 'pwa_install_accepted', 'pwa_installed'):
            self.assertIsNone(analytics.sanitize_properties(name, {'platform': 'web'}))
        # Desktop has no standalone web display; the event is not in the
        # desktop allowlist (web-only observable).
        self.assertIsNone(analytics.sanitize_properties('pwa_standalone_launch', {}))

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
            'VS_ANALYTICS_DISABLE': '',
            'VS_ANALYTICS_ENVIRONMENT': 'test',
        }, clear=False):
            import importlib
            import analytics_config
            importlib.reload(analytics_config)
            loaded = analytics_config.load()
        self.assertEqual(loaded['project_key'], 'phc_from_env')
        self.assertEqual(loaded['host'], 'https://eu.i.posthog.com')
        import importlib
        import analytics_config
        # Restore module defaults after env patch is gone.
        importlib.reload(analytics_config)
        # Shipped config may contain the public capture token only.
        self.assertTrue(analytics_config.POSTHOG_PROJECT_KEY.startswith('phc_'))
        self.assertEqual(
            analytics_config.public_project_key(analytics_config.POSTHOG_PROJECT_KEY),
            analytics_config.POSTHOG_PROJECT_KEY,
        )
        self.assertFalse(analytics_config.POSTHOG_PROJECT_KEY.startswith('phx_'))

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

    def test_timeout_then_resume_counts_single_success(self):
        import wan_core as w
        out = self.tmp / 'out.mp4'
        client = w.Client(data_dir=self.tmp, report=lambda *_: None)
        with unittest.mock.patch.object(w, 'api', return_value=(201, {'id': 'task_123'})), \
             unittest.mock.patch.object(client, 'wait', side_effect=w.TaskError('timeout', 'TIMEOUT', 'task_123')):
            with self.assertRaises(w.TaskError) as caught:
                client.submit(['key'], w.payload('hi', model='wan3.0-video'), out)
            self.assertEqual(caught.exception.kind, 'TIMEOUT')
        analytics.get_client().flush()
        names = [item['event'] for item in self.sent]
        self.assertIn('generation_start', names)
        self.assertNotIn('generation_failed', names)
        self.assertNotIn('generation_success', names)
        gen_id = client.record['local_id']
        self.sent.clear()
        with unittest.mock.patch.object(client, 'wait', return_value=client.record):
            client.resume('key', 'task_123', out, record=client.record)
        analytics.get_client().flush()
        names = [item['event'] for item in self.sent]
        self.assertEqual(names, ['generation_success'])
        self.assertEqual(self.sent[0]['properties']['generation_id'], gen_id)
        self.assertNotIn('generation_start', names)

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

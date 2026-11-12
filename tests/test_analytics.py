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

    def test_default_capture_and_legacy_setter_cannot_disable(self):
        self.assertTrue(self.client.capture_app_open())
        self.client.flush()
        self.assertEqual(self.sent[0]['event'], 'app_open')
        self.client.set_consent(False)
        self.assertTrue(self.client.notify_login_success(key_applied=True))
        self.client.flush()
        self.assertEqual(self.sent[-1]['event'], 'login_success')

    def test_denied_and_unset_migrate_on_boot(self):
        for old in ('denied', 'unset'):
            directory = self.tmp / old
            directory.mkdir()
            (directory / analytics.STATE_NAME).write_text(json.dumps({'consent': old}))
            client = analytics.Analytics(directory, transport=self.sent.append,
                                         config={'project_key': 'phc_test'})
            self.assertEqual(client.consent_state(), 'granted')
            self.assertEqual(json.loads((directory / analytics.STATE_NAME).read_text())['consent'], 'granted')
            self.assertTrue(client.capture_app_open())
            client.flush()
        self.assertEqual(len(self.sent), 2)

    def test_no_consent_ui_and_update_bar_has_one_owner(self):
        import ast
        source = Path('app.py').read_text()
        self.assertNotIn('_build_analytics_banner', source)
        self.assertNotIn('_build_analytics_toggle', source)
        tree = ast.parse(source)
        owners = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                  and any(isinstance(x, ast.Assign) and any(isinstance(t, ast.Attribute)
                          and t.attr == 'update_progress_bar' for t in x.targets)
                          for x in ast.walk(n))]
        self.assertEqual(owners, ['_build_update_banner'])
        builder = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == '_build_update_banner')
        self.assertIn("ttk.Progressbar(box", ast.get_source_segment(source, builder))
        self.assertIn("style='Update.TFrame'", ast.get_source_segment(source, builder))

    def test_app_open_once_per_session(self):
        self.grant()
        self.sent.clear()
        # Same session: second open is dropped.
        self.assertFalse(self.client.capture_app_open())
        self.client.flush()
        self.assertEqual(self.sent, [])
        # Idle timeout ends the previous session, then allows another app_open.
        self.clock.advance(analytics.SESSION_IDLE_S + 1)
        self.assertTrue(self.client.capture_app_open())
        self.client.flush()
        self.assertEqual([item['event'] for item in self.sent], ['session_end', 'app_open'])

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
        # Add to Home Screen is a web-only surface. Desktop must not emit pwa_* events.
        self.assertIsNone(analytics.sanitize_properties('pwa_standalone_launch', {}))
        self.assertIsNone(analytics.sanitize_properties('desktop_download_click', {'source': 'cta'}))

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
        # Committed config stays empty; the public phc_ token is filled at release.
        self.assertEqual(analytics_config.POSTHOG_PROJECT_KEY, '')
        self.assertEqual(analytics_config.public_project_key(analytics_config.POSTHOG_PROJECT_KEY), '')
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



    def test_session_end_duration_vs_active_and_dedupe(self):
        focused = True
        self.client.set_focused_hook(lambda: focused)
        self.grant()
        self.sent.clear()
        self.clock.advance(5)
        focused = False
        self.client.handle_focus(False)
        self.clock.advance(2)
        focused = True
        self.client.handle_focus(True)
        self.clock.advance(3)
        self.assertTrue(self.client.end_session())
        self.client.flush()
        ends = [x for x in self.sent if x['event'] == 'session_end']
        self.assertEqual(len(ends), 1)
        self.assertEqual(ends[0]['properties']['duration_ms'], 10000)
        self.assertEqual(ends[0]['properties']['active_ms'], 8000)
        self.assertFalse(self.client.end_session())
        self.assertEqual(len([x for x in self.sent if x['event'] == 'session_end']), 1)
        capped = analytics.sanitize_properties('session_end', {'duration_ms': 50_000_000, 'active_ms': -3})
        self.assertEqual(capped['duration_ms'], analytics.SESSION_MAX_MS)
        self.assertNotIn('active_ms', capped)

    def test_focus_return_within_idle_does_not_end_session(self):
        focused = True
        self.client.set_focused_hook(lambda: focused)
        self.grant()
        self.sent.clear()
        focused = False
        self.client.handle_focus(False)
        self.clock.advance(60)
        focused = True
        self.client.handle_focus(True)
        self.client.flush()
        self.assertFalse(any(x['event'] == 'session_end' for x in self.sent))
        self.assertFalse(any(x['event'] == 'app_open' for x in self.sent))
        focused = False
        self.client.handle_focus(False)
        self.clock.advance(31 * 60)
        focused = True
        self.client.handle_focus(True)
        self.client.flush()
        names = [x['event'] for x in self.sent]
        self.assertIn('session_end', names)
        self.assertIn('app_open', names)
        self.assertLess(names.index('session_end'), names.index('app_open'))

    def test_crash_recovery_ends_previous_session_before_app_open(self):
        self.grant()
        self.clock.advance(4)
        self.assertTrue(self.client.notify_chat_submit('c-crash'))
        self.client.flush()
        sent2 = []
        clock2 = MemoryClock(self.clock.value)
        other = analytics.Analytics(
            self.tmp, transport=sent2.append, clock=clock2, app_version='1.16',
            config={'project_key': 'phc_test', 'host': 'https://example.invalid'},
        )
        self.assertEqual(other.consent_state(), analytics.CONSENT_GRANTED)
        other.recover_closed_session()
        other.ensure_session()
        other.capture_app_open()
        other.flush()
        names = [x['event'] for x in sent2]
        self.assertIn('session_end', names)
        self.assertIn('app_open', names)
        self.assertLess(names.index('session_end'), names.index('app_open'))
        end = next(x for x in sent2 if x['event'] == 'session_end')
        self.assertEqual(end['properties']['duration_ms'], 4000)

    def test_login_start_is_not_success(self):
        self.grant()
        self.sent.clear()
        self.assertTrue(self.client.notify_login_start())
        self.assertTrue(self.client.notify_login_success(key_applied=True))
        self.assertTrue(self.client.notify_login_failed('access_denied: token abc'))
        self.assertTrue(self.client.notify_logout())
        self.client.flush()
        names = [x['event'] for x in self.sent]
        self.assertIn('login_start', names)
        self.assertIn('login_success', names)
        failed = next(x for x in self.sent if x['event'] == 'login_failed')
        self.assertEqual(failed['properties']['error_code'], 'UNKNOWN')
        blob = json.dumps(self.sent)
        self.assertNotIn('token abc', blob)
        self.assertIn('logout', names)

    def test_billing_click_is_not_payment(self):
        self.grant()
        self.sent.clear()
        self.assertTrue(self.client.notify_billing_click('credits_chip'))
        self.assertTrue(self.client.notify_billing_click('quota'))
        self.assertIsNone(analytics.sanitize_properties('payment_completed', {'amount': 12}))
        self.client.flush()
        self.assertTrue(all(x['event'] == 'billing_click' for x in self.sent))
        self.assertEqual(self.sent[0]['properties']['source'], 'credits_chip')
        self.assertNotIn('payment', json.dumps(self.sent))

    def test_surface_open_once_per_session(self):
        self.grant()
        self.sent.clear()
        self.assertTrue(self.client.notify_surface_open('generate'))
        self.assertFalse(self.client.notify_surface_open('generate'))
        self.assertTrue(self.client.notify_surface_open('chat'))
        self.assertTrue(self.client.notify_surface_open('compare'))
        self.assertTrue(self.client.notify_surface_open('batch'))
        self.assertTrue(self.client.notify_surface_open('history'))
        self.assertTrue(self.client.notify_surface_open('keys'))
        self.assertFalse(self.client.notify_surface_open('settings'))
        self.client.flush()
        self.assertEqual(len([x for x in self.sent if x['event'] == 'surface_open']), 6)

    def test_key_verify_does_not_leak_key_text(self):
        self.grant()
        self.sent.clear()
        self.assertTrue(self.client.notify_key_verified())
        self.assertTrue(self.client.notify_key_verify_failed('AUTH'))
        leak = analytics.sanitize_properties('key_verify_failed', {
            'error_code': 'AUTH', 'key': 'sk-live-secret', 'error': 'invalid key sk-live-secret',
        })
        self.assertEqual(leak['error_code'], 'AUTH')
        self.assertNotIn('key', leak)
        self.assertNotIn('error', leak)
        self.client.flush()
        self.assertNotIn('sk-live', json.dumps(self.sent))

    def test_language_allowlist(self):
        self.grant()
        self.sent.clear()
        self.assertTrue(self.client.notify_language_set('zh'))
        self.assertTrue(self.client.notify_language_set('en'))
        self.assertIsNone(analytics.sanitize_properties('language_set', {'language': 'fr'}).get('language') if analytics.sanitize_properties('language_set', {'language': 'fr'}) else None)
        props = analytics.sanitize_properties('language_set', {'language': 'PT'})
        self.assertEqual(props['language'], 'pt')
        # ja/ko are allowlisted for the paired web app; desktop UI does not offer them.
        self.assertEqual(analytics.sanitize_properties('language_set', {'language': 'ja'})['language'], 'ja')

    def test_input_mode_i2v_vs_t2v(self):
        self.grant()
        self.sent.clear()
        self.assertTrue(self.client.notify_generation_start('g-i2v', model='wan3.0-video', input_mode='i2v'))
        self.assertTrue(self.client.notify_generation_finish('g-i2v', True, input_mode='i2v', duration_ms=1200))
        self.assertTrue(self.client.notify_generation_start('g-t2v', input_mode='t2v'))
        dirty = analytics.sanitize_properties('generation_start', {
            'generation_id': 'g3', 'surface': 'generate', 'input_mode': 'prompt-image', 'prompt': 'secret',
        })
        self.assertNotIn('input_mode', dirty)
        self.assertNotIn('prompt', dirty)
        self.client.flush()
        start_i2v = next(x for x in self.sent if x['event'] == 'generation_start' and x['properties']['generation_id'] == 'g-i2v')
        start_t2v = next(x for x in self.sent if x['event'] == 'generation_start' and x['properties']['generation_id'] == 'g-t2v')
        self.assertEqual(start_i2v['properties']['input_mode'], 'i2v')
        self.assertEqual(start_t2v['properties']['input_mode'], 't2v')

    def test_rejected_invented_events(self):
        self.assertIsNone(analytics.sanitize_properties('consent_denied', {}))
        self.assertIsNone(analytics.sanitize_properties('session_heartbeat', {}))
        self.assertIsNone(analytics.sanitize_properties('share_published', {}))


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

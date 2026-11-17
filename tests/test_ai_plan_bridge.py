"""Headless coverage of the Generate → Chat → Generate bridge."""
import inspect
import unittest
import tkinter as tk
from unittest.mock import Mock, patch

import analytics
import i18n
from app import App
from chat_ui import ChatTab


class Text:
    def __init__(self, value=''):
        self.value = value
        self.modified = False
    def focus_set(self): pass
    def configure(self, **kwargs): pass
    def get(self, *args): return self.value
    def set(self, value): self.value = value
    def delete(self, *args): self.value = ''
    def insert(self, index, value): self.value = value
    def edit_modified(self, value=None):
        if value is None: return self.modified
        self.modified = value


class BridgeTests(unittest.TestCase):
    def chat(self, messages=()):
        chat = ChatTab.__new__(ChatTab)
        chat.app = Mock()
        chat.input = Mock(wraps=Text())
        chat.messages = list(messages)
        chat._busy = False
        chat.send = Mock()
        return chat

    def test_button_and_translations(self):
        self.assertIn("command=self.start_ai_plan", inspect.getsource(App.build_create))
        for language in (i18n.ZH, i18n.EN, i18n.ES, i18n.PT):
            self.assertTrue(language['ai_plan_help'])
            self.assertTrue(language['ai_plan_ready'])

    def test_empty_and_existing_threads(self):
        for prompt, messages, sends in [('idea', [], 1), ('', [], 0), ('idea', [{'role': 'user', 'content': 'earlier'}], 0)]:
            chat = self.chat(messages)
            chat.start_from_generate(prompt)
            chat.app.tabs.select.assert_called_once_with(1)
            self.assertEqual(chat.input.get('1.0', 'end'), prompt)
            chat.input.focus_set.assert_called_once()
            self.assertEqual(chat.send.call_count, sends)
            self.assertEqual(chat.messages, messages)

    def test_login_gate_retains_carried_draft(self):
        chat = self.chat()
        chat.send = ChatTab.send.__get__(chat)
        chat.app.keys = []
        chat._oauth_logged_in = lambda: False
        chat._prompt_chat_login = Mock()
        chat.start_from_generate('keep this idea')
        self.assertEqual(chat.input.get('1.0', 'end'), 'keep this idea')
        chat._prompt_chat_login.assert_called_once()

    def form(self):
        app = App.__new__(App)
        for field, value in dict(prompt='old idea', model_id='wan3.0-video', duration='5', resolution='720p', ratio='16:9').items():
            setattr(app, field, Text(value))
        app.prompt_timer = None
        app.auto_prompt = Text(False)
        app._sync_model_widgets = Mock()
        app.update_cost = Mock()
        app.tabs = Mock()
        app._ai_plan_visit = True
        return app

    def test_apply_and_edit_highlights(self):
        app = self.form()
        with patch.object(analytics, 'notify_ai_plan_applied') as notify:
            app.apply_chat_plan({'prompt': 'new idea', 'duration': 10, 'resolution': '720p'})
        self.assertEqual(app._ai_highlights, {'prompt', 'duration'})
        app.tabs.select.assert_called_once_with(0)
        notify.assert_called_once_with('ai_plan')
        app._clear_ai_field('duration')
        self.assertEqual(app._ai_highlights, {'prompt'})
        app._clear_ai_review()
        self.assertEqual(app._ai_highlights, set())
        self.assertTrue(app._ai_plan_source)

    def test_source_reaches_submit(self):
        app = self.form()
        app._ai_plan_source = True
        app.busy = False
        app.keys = ['key']
        app.ensure_submit_credentials = lambda: True
        app.apply_prompt = lambda: {'warnings': []}
        app._collect_generate_media = lambda: []
        app.seed = Text('')
        app.new_output = lambda: 'out.mp4'
        client = Mock()
        app.launch = lambda operation: operation(client)
        app.generate()
        self.assertEqual(client.submit.call_args.args[1]['_vs_source'], 'ai_plan')

    def test_analytics_allowlist(self):
        for event in ('ai_plan_click', 'ai_plan_applied', 'generation_start', 'generation_success', 'generation_failed'):
            self.assertIn(event, analytics.EVENTS)
            clean = analytics.sanitize_properties(event, {'source': 'ai_plan', 'prompt': 'private', 'key': 'private'})
            self.assertEqual(clean.get('source'), 'ai_plan')
            self.assertNotIn('prompt', clean)
            self.assertNotIn('key', clean)


class UnrelatedFlowRegressionTests(unittest.TestCase):
    def test_onboarding_only_packs_card(self):
        for has_siblings, pack_error in ((False, False), (True, False), (False, True)):
            with self.subTest(siblings=has_siblings, pack_error=pack_error):
                app = App.__new__(App)
                app._create_frame = Mock()
                app.onboard_card = Mock()
                sibling = Mock()
                app._create_frame.winfo_children.return_value = (
                    [app.onboard_card, sibling] if has_siblings else [app.onboard_card])
                app._build_onboarding = Mock()
                app.tabs = Mock()
                app._ai_snapshot = Mock(side_effect=AssertionError('unexpected AI snapshot'))
                if pack_error:
                    app.onboard_card.pack.side_effect = tk.TclError('destroyed')
                with patch.object(analytics, 'notify_ai_plan_applied') as notify:
                    app.show_onboarding()
                app._build_onboarding.assert_called_once_with(app._create_frame, force=True)
                expected = dict(fill='x', pady=(0, 12))
                if has_siblings:
                    expected['before'] = sibling
                app.onboard_card.pack.assert_called_once_with(**expected)
                app.tabs.select.assert_not_called()
                notify.assert_not_called()
                self.assertFalse(hasattr(app, '_ai_highlights'))

    def test_oauth_preserves_post_login_flow(self):
        for key in ('test-key', None):
            with self.subTest(key_applied=bool(key)):
                app = App.__new__(App)
                app.keys = []
                app.tabs = Mock()
                app.status = Mock()
                app.sync_keys = Mock()
                app.save_keys = Mock()
                app._refresh_oauth_header = Mock()
                app._ai_snapshot = Mock(side_effect=AssertionError('unexpected AI snapshot'))
                calls = Mock()
                calls.attach_mock(app.tabs.select, 'select')
                with patch('app.oauth_desktop.save_session'), patch('app.oauth_desktop.load_session', return_value={}), patch('app.secret_store.storage_kind', return_value=None), patch('app.messagebox.showinfo') as info, patch.object(analytics, 'notify_login_success') as login, patch.object(analytics, 'notify_ai_plan_applied') as notify:
                    calls.attach_mock(login, 'login')
                    calls.attach_mock(info, 'info')
                    app._apply_oauth_result({'key': key, 'userinfo': {}})
                self.assertFalse(app.oauth_busy)
                app._refresh_oauth_header.assert_called_once_with()
                login.assert_called_once_with(key_applied=bool(key))
                info.assert_called_once()
                self.assertEqual([call[0] for call in calls.mock_calls],
                                 ['select', 'login', 'info'] + ([] if key else ['select']))
                self.assertEqual(app.tabs.select.call_args_list[0].args, (0,))
                if key:
                    self.assertEqual(app.keys, [key])
                    app.sync_keys.assert_called_once_with()
                    app.save_keys.assert_called_once_with()
                else:
                    self.assertEqual(app.tabs.select.call_args.args, (5,))
                notify.assert_not_called()
                self.assertFalse(hasattr(app, '_ai_highlights'))

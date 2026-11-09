"""Empty key pool on Generate (and the same create gate) opens OAuth, not paste-a-key."""
import i18n
i18n.set_language('zh')

import time
import unittest
from unittest.mock import Mock, patch

import app as appmod
from batch_ui import BatchTab
from compare_ui import CompareTab
from tests.test_app_flow import FlowTests


class _Status:
    def __init__(self):
        self.value = ''

    def set(self, value):
        self.value = value


def bare(*, keys, session):
    host = appmod.App.__new__(appmod.App)
    host.busy = False
    host.keys = list(keys)
    host.oauth_session = session
    host.oauth_busy = False
    host.login_calls = 0
    host.status = _Status()
    host.tabs = Mock()
    host.pages = [object()] * 6
    host.root = None

    def start_oauth_login():
        host.login_calls += 1

    host.start_oauth_login = start_oauth_login
    host.launch = Mock()
    return host


class GenerateLoginTests(unittest.TestCase):
    def setUp(self):
        i18n.set_language('zh')
        self.addCleanup(i18n.set_language, 'zh')

    def test_login_copy_does_not_lead_with_paste(self):
        expected_login = {
            'zh': '请先登录 PowerTokens，再生成视频。登录后 Key 会自动填入。',
            'en': 'Sign in with PowerTokens before generating. Your API key fills in automatically.',
            'es': 'Inicia sesión con PowerTokens antes de generar. La clave se rellena sola.',
            'pt': 'Entre com PowerTokens antes de gerar. A chave preenche-se sozinha.',
        }
        for lang, text in expected_login.items():
            i18n.set_language(lang)
            self.assertEqual(i18n.t('need_key'), text)
            lowered = i18n.t('need_key').lower()
            self.assertNotIn('paste', lowered)
            self.assertNotIn('粘贴', i18n.t('need_key'))
            self.assertNotIn('pega', lowered)
            self.assertNotIn('cole', lowered)
            self.assertNotIn('paste', i18n.t('status_ready').lower())
            self.assertIn('advanced', i18n.t('need_key_advanced').lower()) if lang == 'en' else None
        i18n.set_language('en')
        self.assertEqual(i18n.t('onboard_goto_keys'), 'Already have a key?')
        i18n.set_language('zh')
        self.assertEqual(i18n.t('onboard_goto_keys'), '已有 Key？')

    def test_logged_out_generate_starts_oauth_and_does_not_open_keys(self):
        host = bare(keys=[], session=None)
        with patch('app.messagebox.showinfo') as info, patch('app.messagebox.showerror') as error:
            host.generate()
        host.launch.assert_not_called()
        error.assert_not_called()
        info.assert_called_once()
        self.assertEqual(info.call_args.args[0], i18n.t('oauth_login'))
        self.assertEqual(info.call_args.args[1], i18n.t('need_key'))
        self.assertEqual(host.login_calls, 1)
        host.tabs.select.assert_not_called()
        self.assertEqual(host.status.value, i18n.t('need_key'))

    def test_expired_session_counts_as_logged_out(self):
        host = bare(keys=[], session={'access_token': 'tok', 'expires_at': time.time() - 30})
        self.assertFalse(host._is_oauth_logged_in())
        with patch('app.messagebox.showinfo') as info:
            self.assertFalse(host.ensure_submit_credentials())
        self.assertEqual(host.login_calls, 1)
        self.assertEqual(info.call_args.args[1], i18n.t('need_key'))

    def test_logged_in_without_key_opens_advanced_not_oauth(self):
        host = bare(
            keys=[],
            session={'access_token': 'tok', 'expires_at': time.time() + 3600})
        self.assertTrue(host._is_oauth_logged_in())
        with patch('app.messagebox.showinfo') as info, patch('app.messagebox.showerror') as error:
            host.generate()
        host.launch.assert_not_called()
        error.assert_not_called()
        self.assertEqual(host.login_calls, 0)
        self.assertEqual(info.call_args.args[1], i18n.t('need_key_advanced'))
        host.tabs.select.assert_called_once_with(5)

    def test_saved_key_without_oauth_still_submits(self):
        host, client = FlowTests().app('一只猫在奔跑，五秒')
        host.oauth_session = None
        host.generate()
        client.submit.assert_called_once()

    def test_compare_and_batch_share_the_logged_out_gate(self):
        host = bare(keys=[], session=None)
        compare = CompareTab.__new__(CompareTab)
        compare.app = host
        batch = BatchTab.__new__(BatchTab)
        batch.app = host
        batch.store = object()
        with patch('app.messagebox.showinfo') as info:
            compare.start_compare()
            batch.start(None)
        self.assertEqual(host.login_calls, 2)
        self.assertEqual(info.call_count, 2)
        host.tabs.select.assert_not_called()

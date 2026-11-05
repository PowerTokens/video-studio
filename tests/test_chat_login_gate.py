"""Chat Send requires a PowerTokens OAuth session, not merely a stored API key."""
import i18n
i18n.set_language('zh')

import time
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

import app as appmod
from chat_ui import ChatTab
from wan_core import TaskError


class _InlineThread:
    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target

    def start(self):
        if self._target:
            self._target()


def _host(*, session, keys):
    """App instance without building the window; real OAuth session check."""
    host = appmod.App.__new__(appmod.App)
    host.oauth_session = session
    host.keys = list(keys)
    host.oauth_busy = False
    host.login_calls = 0
    host.status = tk.StringVar()
    host.tabs = Mock()
    host.pages = [object()] * 6
    host.key_reads = 0

    def selected_key():
        host.key_reads += 1
        if not host.keys:
            raise TaskError('need key')
        return host.keys[0]

    def start_oauth_login():
        host.login_calls += 1

    host.selected_key = selected_key
    host.start_oauth_login = start_oauth_login
    return host


class ChatLoginGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()
        i18n.set_language('zh')

    def setUp(self):
        i18n.set_language('zh')
        self.frame = tk.Frame(self.root)
        self.frame.pack()
        self.addCleanup(self.frame.destroy)
        self.addCleanup(i18n.set_language, 'zh')

    def _tab(self, host):
        tab = ChatTab(host, self.frame)
        self.root.update_idletasks()
        return tab

    def _draft(self, tab, text='一只猫在屋顶上看月亮'):
        tab.input.insert('1.0', text)
        return text

    def test_need_login_copy_matches_web(self):
        expected = {
            'zh': '请先登录 PowerTokens，再开始对话。你写好的内容会保留，登录后可直接发送。',
            'en': 'Sign in to PowerTokens before chatting. Your draft will stay here so you can send it after signing in.',
            'es': 'Inicia sesión en PowerTokens antes de chatear. Tu borrador se mantendrá aquí para que puedas enviarlo después de iniciar sesión.',
            'pt': 'Entre no PowerTokens antes de conversar. Seu rascunho ficará aqui para você enviar depois de entrar.',
        }
        for lang, text in expected.items():
            i18n.set_language(lang)
            self.assertEqual(i18n.t('chat_need_login'), text)

    def test_logged_out_with_api_key_does_not_send_or_mount_plan(self):
        host = _host(session=None, keys=['sk-local-only'])
        tab = self._tab(host)
        draft = self._draft(tab)
        existing = {'prompt': 'already here', 'title': 'old'}
        tab.last_plan = existing
        with patch('chat_ui.chat_completions') as chat, \
             patch('chat_ui.messagebox.showinfo') as info, \
             patch('chat_ui.messagebox.showerror') as error:
            tab.send()
        chat.assert_not_called()
        error.assert_not_called()
        info.assert_called_once()
        self.assertEqual(info.call_args.args[0], i18n.t('chat_title'))
        self.assertEqual(info.call_args.args[1], i18n.t('chat_need_login'))
        self.assertEqual(host.login_calls, 1)
        self.assertEqual(host.key_reads, 0)
        self.assertEqual(tab.input.get('1.0', 'end').strip(), draft)
        self.assertEqual(tab.messages, [])
        self.assertIs(tab.last_plan, existing)
        self.assertIn(i18n.t('chat_need_login'), tab.thread.get('1.0', 'end'))
        self.assertEqual(tab.status_var.get(), i18n.t('chat_need_login'))
        host.tabs.select.assert_not_called()

    def test_expired_session_with_api_key_is_logged_out(self):
        host = _host(
            session={'access_token': 'tok', 'expires_at': time.time() - 30},
            keys=['sk-local-only'])
        self.assertFalse(host._is_oauth_logged_in())
        tab = self._tab(host)
        self._draft(tab)
        with patch('chat_ui.chat_completions') as chat, \
             patch('chat_ui.messagebox.showinfo') as info:
            tab.send()
        chat.assert_not_called()
        info.assert_called_once()
        self.assertEqual(host.login_calls, 1)
        self.assertEqual(host.key_reads, 0)

    def test_logged_out_without_key_asks_for_login_not_a_key(self):
        host = _host(session=None, keys=[])
        tab = self._tab(host)
        draft = self._draft(tab)
        with patch('chat_ui.chat_completions') as chat, \
             patch('chat_ui.messagebox.showinfo') as info, \
             patch('chat_ui.messagebox.showerror') as error:
            tab.send()
        chat.assert_not_called()
        error.assert_not_called()
        self.assertEqual(info.call_args.args[1], i18n.t('chat_need_login'))
        self.assertNotIn(i18n.t('storyboard_need_key'), info.call_args.args[1])
        self.assertEqual(tab.input.get('1.0', 'end').strip(), draft)
        self.assertEqual(host.login_calls, 1)
        host.tabs.select.assert_not_called()

    def test_empty_or_busy_send_does_not_open_login(self):
        host = _host(session=None, keys=['sk-local-only'])
        tab = self._tab(host)
        with patch('chat_ui.messagebox.showinfo') as info:
            tab.send()
        info.assert_not_called()
        self.assertEqual(host.login_calls, 0)
        self._draft(tab)
        tab._busy = True
        with patch('chat_ui.messagebox.showinfo') as info:
            tab.send()
        info.assert_not_called()
        self.assertEqual(host.login_calls, 0)

    def test_logged_in_with_key_sends_as_before(self):
        host = _host(
            session={'access_token': 'tok', 'expires_at': time.time() + 3600},
            keys=['sk-from-oauth'])
        self.assertTrue(host._is_oauth_logged_in())
        tab = self._tab(host)
        self._draft(tab, '拍一只猫')
        tab.last_plan = {'prompt': 'previous'}
        with patch('chat_ui.chat_completions', return_value=('{"assistant_message":"好","ready":false,"plan":null}', 'm')) as chat, \
             patch('chat_ui.threading.Thread', _InlineThread), \
             patch('chat_ui.messagebox.showinfo') as info, \
             patch('chat_ui.messagebox.showerror') as error:
            with patch.object(tab, '_schedule_poll', lambda: None):
                tab.send()
        info.assert_not_called()
        error.assert_not_called()
        self.assertEqual(host.login_calls, 0)
        self.assertGreaterEqual(host.key_reads, 1)
        chat.assert_called_once()
        self.assertEqual(chat.call_args.args[0], 'sk-from-oauth')
        self.assertEqual(tab.input.get('1.0', 'end').strip(), '')
        self.assertEqual(tab.messages[0]['role'], 'user')
        self.assertIn('拍一只猫', tab.messages[0]['content'])
        self.assertIsNone(tab.last_plan)

    def test_logged_in_without_key_still_asks_for_a_key(self):
        host = _host(
            session={'access_token': 'tok', 'expires_at': time.time() + 3600},
            keys=[])
        tab = self._tab(host)
        draft = self._draft(tab)
        with patch('chat_ui.chat_completions') as chat, \
             patch('app.messagebox.showinfo') as info, \
             patch('chat_ui.messagebox.showerror') as error:
            tab.send()
        chat.assert_not_called()
        error.assert_not_called()
        info.assert_called_once()
        self.assertEqual(info.call_args.args[1], i18n.t('need_key_advanced'))
        self.assertNotIn('粘贴', i18n.t('need_key'))
        self.assertEqual(host.login_calls, 0)
        self.assertEqual(tab.input.get('1.0', 'end').strip(), draft)
        self.assertEqual(tab.messages, [])
        host.tabs.select.assert_called_once_with(5)

    def test_login_already_in_progress_is_not_started_again(self):
        host = _host(session=None, keys=['sk-local-only'])
        tab = self._tab(host)
        self._draft(tab)

        def start_oauth_login():
            # Same contract as App.start_oauth_login: a busy login stays put.
            if host.oauth_busy:
                return
            host.oauth_busy = True
            host.login_calls += 1

        host.start_oauth_login = start_oauth_login
        with patch('chat_ui.messagebox.showinfo'):
            tab.send()
            tab.send()
        self.assertEqual(host.login_calls, 1)
        self.assertTrue(host.oauth_busy)

import time
import tkinter as tk
import unittest
from unittest.mock import Mock, patch
import app as appmod
import payment
import payment_ui as ui
import i18n


class Widget:
    """Small Tk geometry/event stub; real macOS Tk 8.5 stalls during layout."""
    def __init__(self, parent=None, **kw):
        self.children = []; self.manager = ''; self.value = kw.get('value', '')
        self.options = kw; self.callbacks = {}; self.exists = True; self.timers = {}
        if isinstance(parent, Widget): parent.children.append(self)
    def set(self, value):
        self.value = value
        for cb in self.callbacks.values(): cb()
    def get(self): return self.value
    def trace_add(self, mode, cb): self.callbacks[mode] = cb
    def configure(self, **kw): self.options.update(kw)
    def pack(self, **kw): self.manager = 'pack'
    def pack_forget(self): self.manager = ''
    def grid(self, **kw): self.manager = 'grid'
    def winfo_manager(self): return self.manager
    def winfo_exists(self): return self.exists
    def winfo_children(self): return self.children
    def destroy(self): self.exists = False
    def after(self, delay, cb):
        key = object(); self.timers[key] = (time.monotonic()+delay/1000, cb); return key
    def after_cancel(self, key): self.timers.pop(key, None)
    def update(self):
        for key, (at, cb) in list(self.timers.items()):
            if at <= time.monotonic():
                self.timers.pop(key, None); cb()
    def __getattr__(self, name): return lambda *args, **kw: None


class TopupTests(unittest.TestCase):
    def host(self):
        h = appmod.App.__new__(appmod.App)
        h.oauth_session = {'access_token': 'old', 'expires_at': time.time()-1}
        h.oauth_busy = False
        h.start_oauth_login = Mock()
        h._refresh_key_status = Mock()
        return h

    def test_entry(self):
        for session in (None, {'access_token': 'old', 'expires_at': 1}):
            h = self.host(); h.oauth_session = session
            with patch.object(ui, 'show') as show, patch('webbrowser.open') as browser:
                h.open_billing()
                show.assert_called_once_with(h)
                browser.assert_not_called(); h.start_oauth_login.assert_not_called()

    def test_payload_and_validation(self):
        self.assertEqual(set(ui.checkout_payload()), {'amount', 'package_id', 'payment_channel'})
        self.assertEqual(ui.validated_preselection({'selection': 'bad', 'channel': 'bad', 'custom_amount': '-1'}),
                         dict(selection=ui.DEFAULT_SELECTION, channel='limeng', custom_amount='5'))

    def test_resume(self):
        h = self.host()
        intent = dict(selection='fixed_20', channel='paypal', custom_amount='37', at=time.monotonic())
        h._topup_intent = intent
        h.oauth_session = {'access_token': 'new', 'expires_at': time.time()+100}
        h.status = Mock(); h.tabs = Mock(); h.keys = []
        with patch('oauth_desktop.save_session'), patch('oauth_desktop.load_session', return_value=h.oauth_session), patch.object(ui, 'show') as show, patch('payment.create') as create:
            h._apply_oauth_result({})
            show.assert_called_once_with(h, preselect=intent)
            self.assertEqual(h.oauth_session['access_token'], 'new')
            self.assertIsNone(h._topup_intent); create.assert_not_called()
        h._topup_intent = dict(intent, at=time.monotonic()-901)
        with patch.object(ui, 'show') as show:
            self.assertFalse(ui.resume_topup(h)); show.assert_not_called()


class TkTests(TopupTests):
    def setUp(self):
        self.root = Widget()
        for name in ('Toplevel', 'Frame', 'Label', 'StringVar', 'Radiobutton', 'Canvas', 'Button'):
            patcher = patch.object(tk, name, Widget)
            patcher.start(); self.addCleanup(patcher.stop)
        for name in ('Button', 'Label', 'Entry'):
            patcher = patch.object(ui.ttk, name, Widget)
            patcher.start(); self.addCleanup(patcher.stop)
        self.addCleanup(i18n.set_language, 'zh')

    def test_buy_and_restore_all_languages(self):
        for language in ('zh', 'en', 'es', 'pt'):
            i18n.set_language(language)
            h = self.host(); h.root = self.root
            with patch('payment.create') as create, patch('webbrowser.open') as browser:
                ui.show(h, dict(selection='fixed_20', channel='paypal', custom_amount='37'))
                controls = h._payment_controls
                controls['buy'](); self.root.update()
                self.assertTrue(controls['notice'].winfo_manager())
                self.assertEqual(len(controls['notice'].winfo_children()), 2)
                create.assert_not_called(); browser.assert_not_called()
                controls['confirm']()
                h.start_oauth_login.assert_called_once_with('billing')
                self.assertEqual(set(h._topup_intent), {'selection', 'channel', 'custom_amount', 'at'})
                h.oauth_session = {'access_token': 'new', 'expires_at': time.time()+100}
                self.assertTrue(ui.resume_topup(h)); self.root.update()
                self.assertEqual(h._payment_controls['choice'].get(), 'fixed_20')
                self.assertEqual(h._payment_controls['channel'].get(), 'paypal')
                self.assertEqual(h._payment_controls['amount'].get(), '37')
                self.assertEqual(h._payment_state['token'], 'new')
                self.assertFalse(h._payment_controls['notice'].winfo_manager())
                h._payment_window.destroy()

    def test_missing_session_notice(self):
        h = self.host(); h.root = self.root; h.oauth_session = None
        i18n.set_language('en')
        with patch('payment.create') as create, patch('webbrowser.open') as browser:
            ui.show(h)
            h._payment_controls['buy']()
            notice = h._payment_controls['notice']
            self.assertEqual(notice.children[0].options['text'], 'Sign in to pay. Your selected package will be kept.')
            self.assertEqual(notice.children[1].options['text'], 'Continue to sign in')
            create.assert_not_called(); browser.assert_not_called()
            h.start_oauth_login.assert_not_called()

    def test_auth_failure(self):
        h = self.host(); h.root = self.root
        h.oauth_session['expires_at'] = time.time()+100
        error = ValueError(); error.error_code = 'AUTH'
        with patch('payment.create', side_effect=error), patch('webbrowser.open') as browser:
            ui.show(h); h._payment_controls['buy']()
            until = time.monotonic()+2
            while not h._payment_controls['notice'].winfo_manager() and time.monotonic()<until:
                self.root.update(); time.sleep(.02)
            self.assertTrue(h._payment_controls['notice'].winfo_manager())
            browser.assert_not_called()

    def test_header_live_expiry(self):
        h = self.host(); h.root = self.root
        for name in ('header_oauth_btn', 'header_logout_btn', 'oauth_account_label', 'oauth_credits_label', 'keys_oauth_logout_btn'):
            setattr(h, name, tk.Button(self.root))
        h.oauth_account_var = tk.StringVar(); h.oauth_credits_var = tk.StringVar()
        h.oauth_session = dict(access_token='fresh', expires_at=time.time()+.15, profile=dict(name='Test', credits=100))
        h._refresh_oauth_header()
        self.assertTrue(h.header_logout_btn.winfo_manager()); self.assertFalse(h.header_oauth_btn.winfo_manager())
        until = time.monotonic()+.3
        while time.monotonic()<until: self.root.update(); time.sleep(.01)
        self.assertTrue(h.header_oauth_btn.winfo_manager())
        self.assertFalse(h.header_logout_btn.winfo_manager()); self.assertFalse(h.oauth_account_label.winfo_manager())
        self.assertFalse(h.keys_oauth_logout_btn.winfo_manager()); self.assertEqual(h.oauth_account_var.get(), '')
        self.assertNotIn('100', h.oauth_credits_var.get())

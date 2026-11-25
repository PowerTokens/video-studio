"""Offline login-only key ownership and generate gate regressions."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import app as appmod
from tests.test_generate_login import bare


class LogoutKeyTests(unittest.TestCase):
    def host(self, keys):
        host = bare(keys=keys, session=None)
        host.sync_keys = Mock()
        host.save_keys = Mock()
        host._refresh_oauth_header = Mock()
        return host

    def apply(self, host):
        with patch('app.oauth_desktop.save_session'), patch('app.oauth_desktop.load_session', return_value={'profile': {}}), patch('app.secret_store.storage_kind', return_value=None), patch('app.messagebox.showinfo'):
            host._apply_oauth_result({'key': 'fake-login-key', 'userinfo': {}})

    def logout(self, host):
        with patch('app.oauth_desktop.logout'):
            host.logout_oauth()
        self.assertIsNone(host.oauth_session)

    def test_only_login_key_cannot_generate_after_logout(self):
        host = self.host([])
        self.apply(host)
        self.assertTrue(host.ensure_submit_credentials())
        self.logout(host)
        self.assertFalse(host.keys)
        with patch('app.messagebox.askokcancel', return_value=True), patch('app.messagebox.showinfo'):
            host.generate()
        host.launch.assert_not_called()
        self.assertEqual(host.login_calls, 1)

    def test_manual_before_and_other_key_survive(self):
        for key in ['fake-login-key', 'fake-pasted-key']:
            host = self.host([key])
            self.apply(host)
            self.logout(host)
            self.assertTrue(host.keys == [key])
            self.assertTrue(host.ensure_submit_credentials())

    def test_manual_duplicate_after_login_survives(self):
        host = self.host([])
        self.apply(host)
        host.key_input = Mock()
        host.key_input.get.return_value = 'fake-login-key'
        host.add_keys()
        self.logout(host)
        self.assertTrue(host.keys == ['fake-login-key'])

    def test_source_metadata_survives_restart(self):
        host = self.host([])
        self.apply(host)
        host.remember = Mock()
        host.remember.get.return_value = True
        with tempfile.TemporaryDirectory() as tmp, patch('app.DATA_DIR', Path(tmp)), patch('app.secret_store.write_remembered_keys'), patch('app.secret_store.read_remembered_keys', return_value=['fake-login-key']):
            appmod.App.save_keys(host)
            self.assertNotIn('fake-login-key', (Path(tmp) / 'login-only-keys.json').read_text())
            (Path(tmp) / 'keys.json').touch()
            restored = self.host([])
            restored.remember = Mock()
            restored.load_keys()
            self.logout(restored)
            self.assertFalse(restored.keys)

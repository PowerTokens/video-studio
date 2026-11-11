"""macOS path, reveal, and remembered-key behavior. The OS Keychain is mocked.

Windows DPAPI is not exercised here; tests/test_core.py still runs the real
CryptProtectData round-trip on Windows and is left unchanged.
"""
import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import i18n
import platform_paths
import secret_store
import studio_ui


class PathHelperTests(unittest.TestCase):
    def test_localappdata_wins_on_every_platform(self):
        override = r'C:\Users\qin\AppData\Local' if os.name == 'nt' else '/tmp/pt-local'
        with patch.dict(os.environ, {'LOCALAPPDATA': override}), \
                patch('platform_paths.sys.platform', 'darwin'):
            self.assertEqual(platform_paths.user_data_dir(), Path(override) / 'PowerTokensWan')

    def test_macos_application_support_when_localappdata_unset(self):
        env = {key: value for key, value in os.environ.items() if key != 'LOCALAPPDATA'}
        with patch.dict(os.environ, env, clear=True), \
                patch('platform_paths.sys.platform', 'darwin'), \
                patch('platform_paths.Path.home', return_value=Path('/Users/qin')):
            self.assertEqual(
                platform_paths.user_data_dir(),
                Path('/Users/qin/Library/Application Support/PowerTokensWan'),
            )

    def test_other_systems_keep_local_share(self):
        env = {key: value for key, value in os.environ.items() if key != 'LOCALAPPDATA'}
        with patch.dict(os.environ, env, clear=True), \
                patch('platform_paths.sys.platform', 'linux'), \
                patch('platform_paths.Path.home', return_value=Path('/home/qin')):
            self.assertEqual(
                platform_paths.user_data_dir(),
                Path('/home/qin/.local/share/PowerTokensWan'),
            )

    def test_default_output_folder(self):
        with patch('platform_paths.Path.home', return_value=Path('/Users/qin')):
            with patch('platform_paths.sys.platform', 'darwin'):
                self.assertEqual(
                    platform_paths.default_output_dir(),
                    Path('/Users/qin/Movies/PowerTokensVideoStudio'),
                )
            with patch('platform_paths.sys.platform', 'win32'):
                self.assertEqual(
                    platform_paths.default_output_dir(),
                    Path('/Users/qin/Videos/PowerTokensVideoStudio'),
                )
            with patch('platform_paths.sys.platform', 'linux'):
                self.assertEqual(
                    platform_paths.default_output_dir(),
                    Path('/Users/qin/Videos/PowerTokensVideoStudio'),
                )


class RevealTests(unittest.TestCase):
    def test_macos_uses_open(self):
        with patch('platform_paths.sys.platform', 'darwin'), \
                patch('platform_paths.subprocess.run') as run:
            platform_paths.reveal_path('/Users/qin/Movies/PowerTokensVideoStudio')
        run.assert_called_once_with(['open', '/Users/qin/Movies/PowerTokensVideoStudio'], check=True)

    def test_windows_uses_startfile(self):
        with patch('platform_paths.sys.platform', 'win32'), \
                patch('platform_paths.os.startfile', create=True) as start, \
                patch('platform_paths.subprocess.run') as run:
            platform_paths.reveal_path(r'C:\Users\qin\Videos\PowerTokensVideoStudio')
        start.assert_called_once_with(r'C:\Users\qin\Videos\PowerTokensVideoStudio')
        run.assert_not_called()

    def test_other_systems_use_xdg_open(self):
        with patch('platform_paths.sys.platform', 'linux'), \
                patch('platform_paths.subprocess.run') as run:
            platform_paths.reveal_path('/home/qin/Videos/PowerTokensVideoStudio')
        run.assert_called_once_with(
            ['xdg-open', '/home/qin/Videos/PowerTokensVideoStudio'], check=True)

    def test_failed_launcher_raises_oserror(self):
        import subprocess
        with patch('platform_paths.sys.platform', 'darwin'), \
                patch('platform_paths.subprocess.run', side_effect=subprocess.CalledProcessError(1, 'open')):
            with self.assertRaises(OSError):
                platform_paths.reveal_path('/missing')


class OpenFolderWiringTests(unittest.TestCase):
    def test_history_open_folder_reveals_the_output_directory(self):
        from app import App
        folder = Path(tempfile.mkdtemp())
        host = App.__new__(App)
        host.tree = Mock()
        host.tree.selection.return_value = ()
        host.output = Mock()
        host.output.get.return_value = str(folder)
        host.records = {}
        with patch('app.reveal_path') as reveal:
            host.open_folder()
        reveal.assert_called_once_with(folder)

    def test_history_open_folder_shows_the_path_when_reveal_fails(self):
        from app import App
        folder = Path(tempfile.mkdtemp())
        host = App.__new__(App)
        host.tree = Mock()
        host.tree.selection.return_value = ()
        host.output = Mock()
        host.output.get.return_value = str(folder)
        host.records = {}
        with patch('app.reveal_path', side_effect=OSError('no launcher')), \
                patch('app.messagebox.showinfo') as info:
            host.open_folder()
        info.assert_called_once()
        self.assertIn(str(folder), info.call_args.args[-1])

    def test_compare_open_uses_the_same_reveal_helper(self):
        from compare_ui import CompareTab
        tab = CompareTab.__new__(CompareTab)
        folder = Path(tempfile.mkdtemp())
        clip = folder / 'clip.mp4'
        clip.write_bytes(b'not a real movie')
        with patch('compare_ui.reveal_path') as reveal:
            tab._open_path(clip, file=True)
            tab._open_path(clip, file=False)
        self.assertEqual(reveal.call_args_list[0].args[0], clip)
        self.assertEqual(reveal.call_args_list[1].args[0], folder)


class _MemoryKeychain:
    def __init__(self):
        self.items = {}

    def set(self, service, account, secret):
        self.items[(service, account)] = bytes(secret)

    def get(self, service, account):
        try:
            return self.items[(service, account)]
        except KeyError:
            raise secret_store.SecretStoreError('not found')

    def delete(self, service, account):
        self.items.pop((service, account), None)


class RememberedKeyTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'keys.json'
        self.chain = _MemoryKeychain()

    def _keychain_patches(self):
        return [
            patch.object(secret_store, 'storage_kind', return_value='keychain'),
            patch.object(secret_store, 'keychain_set', side_effect=self.chain.set),
            patch.object(secret_store, 'keychain_get', side_effect=self.chain.get),
            patch.object(secret_store, 'keychain_delete', side_effect=self.chain.delete),
        ]

    def test_macos_remember_stores_the_secret_in_the_keychain_not_the_file(self):
        patches = self._keychain_patches()
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        secret_store.write_remembered_keys(self.path, ['sk-mac-secret', 'sk-other'])
        text = self.path.read_text(encoding='utf-8')
        self.assertNotIn('sk-mac-secret', text)
        record = json.loads(text)
        self.assertEqual(record['keychain'], 'login')
        self.assertEqual(record['service'], secret_store.KEYCHAIN_SERVICE)
        self.assertNotIn('dpapi', record)
        stored = self.chain.items[(secret_store.KEYCHAIN_SERVICE, secret_store.KEYCHAIN_ACCOUNT)]
        self.assertIn(b'sk-mac-secret', stored)
        self.assertEqual(secret_store.read_remembered_keys(self.path), ['sk-mac-secret', 'sk-other'])
        secret_store.clear_remembered_keys(self.path)
        self.assertFalse(self.path.exists())
        self.assertEqual(self.chain.items, {})

    def test_windows_remember_keeps_the_dpapi_file_format(self):
        def fake_dpapi(raw, decrypt=False):
            if decrypt:
                self.assertTrue(raw.startswith(b'DPAPI'))
                return raw[len(b'DPAPI'):]
            return b'DPAPI' + bytes(raw)

        with patch.object(secret_store, 'storage_kind', return_value='dpapi'), \
                patch.object(secret_store, 'dpapi_protect', side_effect=fake_dpapi) as dpapi, \
                patch.object(secret_store, 'keychain_set') as keychain_set:
            secret_store.write_remembered_keys(self.path, ['sk-pt-1'])
            record = json.loads(self.path.read_text(encoding='utf-8'))
            self.assertEqual(set(record), {'dpapi'})
            blob = base64.b64decode(record['dpapi'])
            self.assertTrue(blob.startswith(b'DPAPI'))
            self.assertNotIn('sk-pt-1', self.path.read_text(encoding='utf-8'))
            self.assertEqual(secret_store.read_remembered_keys(self.path), ['sk-pt-1'])
            secret_store.clear_remembered_keys(self.path)
        self.assertFalse(self.path.exists())
        self.assertGreaterEqual(dpapi.call_count, 2)
        keychain_set.assert_not_called()

    def test_other_platforms_refuse_to_persist(self):
        with patch.object(secret_store, 'storage_kind', return_value=None):
            with self.assertRaises(RuntimeError):
                secret_store.write_remembered_keys(self.path, ['sk-temp'])
        self.assertFalse(self.path.exists())

    def test_keychain_helpers_do_not_touch_the_framework_off_macos(self):
        if sys.platform == 'darwin':
            self.skipTest('this guard is for non-macOS test runs')
        with self.assertRaises(secret_store.SecretStoreError):
            secret_store.keychain_set('service', 'account', b'secret')
        with self.assertRaises(secret_store.SecretStoreError):
            secret_store.keychain_get('service', 'account')
        with self.assertRaises(secret_store.SecretStoreError):
            secret_store.keychain_delete('service', 'account')

    @unittest.skipUnless(os.name != 'nt', 'DPAPI refusal is the non-Windows contract')
    def test_app_protect_still_refuses_dpapi_off_windows(self):
        from app import protect
        with self.assertRaises(RuntimeError) as caught:
            protect(b'sk-pt-secret')
        self.assertIn('Windows', str(caught.exception))


class FontTests(unittest.TestCase):
    def test_windows_and_mac_fonts(self):
        self.assertEqual(studio_ui.ui_font('win32'), 'Microsoft YaHei UI')
        self.assertEqual(studio_ui.ui_font('darwin'), 'PingFang SC')
        self.assertEqual(studio_ui.ui_font('linux'), 'PingFang SC')


if __name__ == '__main__':
    unittest.main()

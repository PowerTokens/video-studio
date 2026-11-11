"""Real macOS login Keychain write/read/update/delete with isolated dummy items.

Never uses the production service/account, never reads existing secrets, and
always deletes the test item. Dummy values are not real API keys.
"""
import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import secret_store


@unittest.skipUnless(sys.platform == 'darwin', 'real Keychain tests run only on macOS')
class LiveKeychainTests(unittest.TestCase):
    def setUp(self):
        self.service = 'PowerTokens Video Studio CI Test ' + uuid.uuid4().hex
        self.account = 'ci-dummy-' + uuid.uuid4().hex[:16]
        self.env = {
            'PT_KEYCHAIN_SERVICE': self.service,
            'PT_KEYCHAIN_ACCOUNT': self.account,
        }
        from unittest.mock import patch
        self._patch = patch.dict(os.environ, self.env, clear=False)
        self._patch.start()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        try:
            secret_store.keychain_delete(self.service, self.account)
        except Exception:
            pass
        self._patch.stop()

    def test_set_get_update_delete_isolated_item(self):
        first = b'sk-ci-dummy-not-a-real-key-aaa'
        second = b'sk-ci-dummy-not-a-real-key-bbb'
        secret_store.keychain_set(self.service, self.account, first)
        self.assertEqual(secret_store.keychain_get(self.service, self.account), first)
        secret_store.keychain_set(self.service, self.account, second)
        self.assertEqual(secret_store.keychain_get(self.service, self.account), second)
        secret_store.keychain_delete(self.service, self.account)
        with self.assertRaises(secret_store.SecretStoreError):
            secret_store.keychain_get(self.service, self.account)
        # Deleting a missing item is not an error.
        secret_store.keychain_delete(self.service, self.account)

    def test_remembered_keys_persist_and_are_gone_after_clear(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / 'keys.json'
        dummy = ['sk-ci-dummy-remember-one', 'sk-ci-dummy-remember-two']
        secret_store.write_remembered_keys(path, dummy)
        record = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(record.get('keychain'), 'login')
        self.assertEqual(record.get('service'), self.service)
        self.assertEqual(record.get('account'), self.account)
        self.assertNotIn('sk-ci-dummy-remember-one', path.read_text(encoding='utf-8'))
        self.assertEqual(secret_store.read_remembered_keys(path), dummy)
        secret_store.clear_remembered_keys(path)
        self.assertFalse(path.exists())
        with self.assertRaises(secret_store.SecretStoreError):
            secret_store.keychain_get(self.service, self.account)


class KeychainIsolationHelpersTests(unittest.TestCase):
    def test_defaults_are_the_production_names(self):
        from unittest.mock import patch
        env = {k: v for k, v in os.environ.items()
               if k not in ('PT_KEYCHAIN_SERVICE', 'PT_KEYCHAIN_ACCOUNT')}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(secret_store.active_keychain_service(), secret_store.KEYCHAIN_SERVICE)
            self.assertEqual(secret_store.active_keychain_account(), secret_store.KEYCHAIN_ACCOUNT)

    def test_env_override_does_not_use_production_names(self):
        from unittest.mock import patch
        with patch.dict(os.environ, {
            'PT_KEYCHAIN_SERVICE': 'CI Isolated Service',
            'PT_KEYCHAIN_ACCOUNT': 'ci-account',
        }):
            self.assertEqual(secret_store.active_keychain_service(), 'CI Isolated Service')
            self.assertEqual(secret_store.active_keychain_account(), 'ci-account')
            self.assertNotEqual(secret_store.active_keychain_service(), secret_store.KEYCHAIN_SERVICE)

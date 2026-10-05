import i18n
i18n.set_language('en')

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import i18n as i18n_mod


class OnboardingSettingsTests(unittest.TestCase):
    def test_dismiss_persists(self):
        folder = Path(tempfile.mkdtemp())
        path = folder / 'settings.json'
        with patch.object(i18n_mod, 'SETTINGS_PATH', path):
            self.assertFalse(i18n_mod.load_settings().get('onboarding_dismissed'))
            i18n_mod.save_setting('onboarding_dismissed', True)
            data = json.loads(path.read_text(encoding='utf-8'))
            self.assertTrue(data['onboarding_dismissed'])
            # language save keeps the flag
            i18n_mod.save_language('en')
            data = json.loads(path.read_text(encoding='utf-8'))
            self.assertTrue(data['onboarding_dismissed'])
            self.assertEqual(data['language'], 'en')

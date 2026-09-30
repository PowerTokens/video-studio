import i18n
i18n.set_language('zh')  # Existing tests check the original Chinese wording.

import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import pt_wan as cli

class CliTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.config = Path(temp.name) / 'config.json'
        self.config.write_text(json.dumps({'api_keys': ['sk-pt-example1234']}), encoding='utf-8')
        for p in (patch.object(cli, 'CONFIG_PATH', self.config), patch.dict(os.environ, {'POWERTOKENS_API_KEYS': '', 'POWERTOKENS_API_KEY': ''})):
            p.start()
            self.addCleanup(p.stop)

    def run_cli(self, args):
        stream = io.StringIO()
        with patch('sys.argv', ['pt_wan.py'] + args), contextlib.redirect_stdout(stream):
            code = cli.main()
        return code, json.loads(stream.getvalue())

    def test_check_is_valid_single_json(self):
        code, body = self.run_cli(['check'])
        self.assertEqual(code, 0)
        self.assertEqual(body['key_pool_size'], 1)
        self.assertNotIn('sk-pt-', json.dumps(body))

    def run_estimate_on(self, day):
        import datetime
        import wan_core
        with patch.object(wan_core, 'local_today', return_value=datetime.date(*day)):
            return self.run_cli(['estimate', '-d', '20', '-r', '1080p'])

    def test_estimate_after_promo_uses_list_price(self):
        code, body = self.run_estimate_on((2026, 10, 8))
        self.assertEqual(code, 0)
        self.assertEqual(body['est_cost_usd'], 4.0)
        self.assertNotIn('list_cost_usd', body)
        self.assertNotIn('promo_end_date', body)

    def test_estimate_uses_pt_price_and_source(self):
        code, body = self.run_estimate_on((2026, 10, 7))
        self.assertEqual(code, 0)
        self.assertEqual(body['est_cost_usd'], 1.6)
        self.assertEqual(body['list_cost_usd'], 4.0)
        self.assertEqual(body['promo_end_date'], '2026-10-07')
        self.assertIn('powertokens.ai', body['price_source'])
        self.assertIn('utm_campaign=video-studio', body['price_source'])

    def test_prune_keeps_403_and_network_errors(self):
        for code in (403, None, 502):
            with patch.object(cli, 'api', return_value=(code, {})):
                self.run_cli(['prune'])
            self.assertEqual(len(json.loads(self.config.read_text(encoding='utf-8'))['api_keys']), 1)

    def test_prune_removes_only_confirmed_401(self):
        with patch.object(cli, 'api', return_value=(401, {})):
            self.run_cli(['prune'])
        self.assertEqual(json.loads(self.config.read_text(encoding='utf-8'))['api_keys'], [])

    def test_rejects_wrong_model_without_network(self):
        with patch.object(cli, 'api') as api:
            with self.assertRaises(cli.TaskError):
                self.run_cli(['generate', '-p', 'hello', '-m', 'another-model'])
            api.assert_not_called()

    def test_config_add_accepts_non_pt_key(self):
        code, body = self.run_cli(['config', '--add-key', 'Bearer sk-example5678'])
        self.assertEqual(code, 0)
        self.assertIn('sk-example5678', json.loads(self.config.read_text(encoding='utf-8'))['api_keys'])
        self.assertNotIn('sk-example5678', json.dumps(body))

    def test_cli_generation_requests_native_audio(self):
        with patch.dict(os.environ, {'PT_DRY_RUN': '1'}):
            code, body = self.run_cli(['generate', '-p', '雨落在屋顶上，伴随雨声'])
        self.assertEqual(code, 0)
        self.assertIs(body['payload']['generate_audio'], True)
        self.assertEqual(body['payload']['model'], 'wan3.0-video')

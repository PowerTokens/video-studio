import i18n
i18n.set_language('zh')

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import compare_core
import models
import wan_core as w


class PlanCompareTests(unittest.TestCase):
    def test_requires_two_to_three_models(self):
        with self.assertRaises(w.TaskError):
            compare_core.plan_compare('hi', 5, '720p', '16:9', ['wan3.0-video'])
        with self.assertRaises(w.TaskError):
            compare_core.plan_compare('hi', 5, '720p', '16:9',
                                     ['wan3.0-video', 'kling-v3', 'wan3.0-video-prime',
                                      'dreamina-seedance-2-5-260628'])

    def test_snaps_per_model_and_sums_cost(self):
        plan = compare_core.plan_compare(
            'a cat runs', 30, '1080p', '16:9',
            ['wan3.0-video', 'dreamina-seedance-2-0-fast-260128'],
            folder='/tmp/out')
        self.assertEqual(len(plan['items']), 2)
        wan = plan['items'][0]
        fast = plan['items'][1]
        self.assertEqual(wan['duration'], 30)
        self.assertEqual(wan['resolution'], '1080p')
        self.assertEqual(fast['duration'], 15)
        self.assertEqual(fast['resolution'], '720p')
        self.assertIn('最长 15 秒', fast['notice'])
        self.assertGreater(plan['total_cost'], 0)
        self.assertTrue(fast['output'].endswith('_seedance-fast.mp4'))
        self.assertIn('/compare/', fast['output'].replace('\\', '/'))

    def test_short_names_from_registry(self):
        self.assertEqual(compare_core.model_short_name('wan3.0-video-prime'), 'wan-prime')
        self.assertEqual(models.get_model('kling-v3').short_name, 'kling-v3')


class RunCompareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        (self.data / 'tasks').mkdir()
        self.out = self.data / 'videos'
        self.out.mkdir()

    def test_run_compare_saves_grouped_tasks(self):
        plan = compare_core.plan_compare(
            'rainy street', 5, '720p', '16:9',
            ['wan3.0-video', 'kling-v3'], folder=str(self.out), name='demo')
        calls = []

        def fake_api(method, url, key, body=None, timeout=30):
            calls.append((method, url))
            if method == 'POST':
                tid = 'task-' + str(len(calls))
                if '/kling/' in url:
                    return 200, {'code': 0, 'data': {'task_id': tid}}
                return 200, {'id': tid}
            # poll
            if '/kling/' in url:
                return 200, {'code': 0, 'data': {
                    'task_status': 'succeed',
                    'task_result': {'videos': [{'url': 'https://example.com/v.mp4'}]},
                }}
            return 200, {'id': 'task', 'status': 'completed',
                         'metadata': {'url': 'https://example.com/v.mp4'}}

        def fake_download(url, out_path, key, stop, report):
            Path(out_path).write_bytes(b'fake-mp4')
            return 8

        def factory():
            return w.Client(report=lambda *_: None, data_dir=self.data)

        with patch.object(w, 'api', side_effect=fake_api), \
             patch.object(w, 'download', side_effect=fake_download):
            results = compare_core.run_compare(factory, ['sk-pt-testkey1234'], plan)
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r['ok'] for r in results))
        records = list((self.data / 'tasks').glob('*.json'))
        self.assertEqual(len(records), 2)
        loaded = [json.loads(p.read_text(encoding='utf-8')) for p in records]
        self.assertEqual({r['compare_id'] for r in loaded}, {plan['compare_id']})
        self.assertEqual({r['model'] for r in loaded}, {'wan3.0-video', 'kling-v3'})


class CliCompareTests(unittest.TestCase):
    def test_dry_run_compare(self):
        import contextlib, io
        import pt_wan as cli
        config = Path(tempfile.mkdtemp()) / 'config.json'
        config.write_text(json.dumps({'api_keys': ['sk-pt-example1234']}), encoding='utf-8')
        stream = io.StringIO()
        env = {'POWERTOKENS_API_KEYS': '', 'POWERTOKENS_API_KEY': '', 'PT_DRY_RUN': '1'}
        with patch.object(cli, 'CONFIG_PATH', config), patch.dict(os.environ, env), \
             patch('sys.argv', ['pt_wan.py', 'compare',
                                '--models', 'wan3.0-video,Seedance 2.0 Fast',
                                '-p', 'hello', '-d', '30', '-r', '1080p']), \
             contextlib.redirect_stdout(stream):
            code = cli.main()
        body = json.loads(stream.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(body['dry_run'])
        self.assertEqual(len(body['items']), 2)
        fast = next(i for i in body['items'] if 'seedance' in i['model_id'])
        self.assertEqual(fast['duration'], 15)
        self.assertEqual(fast['resolution'], '720p')

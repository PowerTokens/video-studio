import i18n
i18n.set_language('en')

import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import frame_util as fu
import models
import wan_core as w
from batch_engine import BatchRunner, BatchStore


class FrameEncodeTests(unittest.TestCase):
    def test_data_url_and_kling_strip(self):
        folder = Path(tempfile.mkdtemp())
        img = folder / 'f.jpg'
        # Minimal JPEG-ish bytes (not a real JPEG; builders only need bytes for encoding tests)
        img.write_bytes(b'\xff\xd8\xff' + b'x' * 64 + b'\xff\xd9')
        url = fu.data_url_for_image(img)
        self.assertTrue(url.startswith('data:image/jpeg;base64,'))
        raw = fu.kling_image_value(url)
        self.assertFalse(raw.startswith('data:'))
        self.assertEqual(raw, base64.b64encode(img.read_bytes()).decode('ascii'))

    def test_validate_allows_data_url(self):
        media = [{'type': 'first_frame', 'url': 'data:image/jpeg;base64,abc'}]
        models.validate_params(models.get_model('dreamina-seedance-2-0-fast-260128'), 5, '720p', '16:9',
                               prompt='hi', media=media)

    def test_inject_seedance_and_kling(self):
        folder = Path(tempfile.mkdtemp())
        img = folder / 'f.jpg'
        img.write_bytes(b'\xff\xd8\xff' + b'y' * 80 + b'\xff\xd9')
        seed_body = w.payload('a cat', 5, '720p', '16:9', model='dreamina-seedance-2-0-fast-260128')
        patched = fu.inject_first_frame_into_payload('dreamina-seedance-2-0-fast-260128', seed_body, img)
        types = [m['type'] for m in patched['media']]
        self.assertIn('first_frame', types)
        self.assertTrue(any(m.get('url', '').startswith('data:image/') for m in patched['media'] if m['type'] == 'first_frame'))
        kling_body = w.payload('a cat', 5, '720p', '16:9', model='kling-v3')
        patched_k = fu.inject_first_frame_into_payload('kling-v3', kling_body, img)
        self.assertIn('image', patched_k)
        self.assertFalse(str(patched_k['image']).startswith('data:'))


class ChainRunnerTests(unittest.TestCase):
    def test_chain_runs_sequentially_and_injects_frame(self):
        folder = Path(tempfile.mkdtemp())
        # Tiny fake mp4 path — extract is mocked
        out1 = folder / '001_a.mp4'
        out1.write_bytes(b'fake')
        jobs = []
        for i, title in enumerate(('A', 'B'), 1):
            body = w.payload('clip %s' % title, 5, '720p', '16:9', model='dreamina-seedance-2-0-fast-260128')
            jobs.append(dict(
                id='job%d' % i, sheet='s', row=i, title=title, prompt='clip %s' % title,
                character_setting='', state='queued', note='', record=None,
                model='dreamina-seedance-2-0-fast-260128', payload=body, cost=0.1,
                output=str(folder / ('%03d_%s.mp4' % (i, title)))))
        store = BatchStore(jobs, folder, source='test', data_dir=folder)
        first = store.snapshot()[0]
        out_path = Path(first['output'])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b'fake')
        store.update(first['id'], state='completed', output=str(out_path))

        calls = []

        class FakeClient:
            def __init__(self, **kwargs):
                self.record = {}
                self.kwargs = kwargs
            def submit(self, keys, request, out_path):
                calls.append(dict(request=request, out=out_path))
                tid = 'tid-%d' % len(calls)
                self.record = {'task_id': tid, 'output': out_path}
                Path(out_path).parent.mkdir(parents=True, exist_ok=True)
                Path(out_path).write_bytes(b'v')
                return self.record
            def resume(self, *a, **k):
                raise AssertionError('should not resume')

        frame = folder / 'frame.jpg'
        frame.write_bytes(b'\xff\xd8\xff' + b'z' * 40 + b'\xff\xd9')

        with patch('frame_util.extract_last_frame', return_value=frame):
            runner = BatchRunner(store, ['sk-test-key-aaaa'], concurrency=3, chain=True,
                                 client_factory=FakeClient, download_limit=1)
            # Only second job eligible queued
            runner.run(selected=[store.snapshot()[1]['id']])

        self.assertEqual(len(calls), 1)
        media = calls[0]['request'].get('media') or []
        self.assertTrue(any(m.get('type') == 'first_frame' for m in media))
        # task id saved on job — no double submit
        second = store.snapshot()[1]
        self.assertEqual(second['state'], 'completed')
        self.assertTrue((second.get('record') or {}).get('task_id'))

    def test_resume_with_task_id_skips_reinject(self):
        folder = Path(tempfile.mkdtemp())
        key = 'sk-test-key-abcdefgh'
        body = w.payload('x', 5, '720p', '16:9', model='kling-v3')
        jobs = [dict(
            id='j1', sheet='s', row=1, title='A', prompt='x', character_setting='',
            state='paused', note='', record={'task_id': 'already', 'key_hash': w.fingerprint(key)},
            model='kling-v3', payload=body, cost=0.1, output=str(folder / 'a.mp4'))]
        store = BatchStore(jobs, folder, source='t', data_dir=folder)
        store.update(store.snapshot()[0]['id'], state='paused',
                     record={'task_id': 'already', 'key_hash': w.fingerprint(key)})

        class FakeClient:
            def __init__(self, **kwargs):
                self.record = {}
            def resume(self, key, task_id, out_path, record):
                self.record = {'task_id': task_id, 'output': out_path}
                Path(out_path).parent.mkdir(parents=True, exist_ok=True)
                Path(out_path).write_bytes(b'v')
                return self.record
            def submit(self, *a, **k):
                raise AssertionError('must not resubmit')

        runner = BatchRunner(store, [key], concurrency=1, chain=True, client_factory=FakeClient)
        with patch('frame_util.extract_last_frame') as ex:
            runner.run()
            ex.assert_not_called()
        self.assertEqual(store.snapshot()[0]['state'], 'completed')

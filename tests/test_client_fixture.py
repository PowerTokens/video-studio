"""SIMULATION: Client submit/poll/download against a local HTTPS fixture.

This does not call api.powertokens.ai and does not use a real API key.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))

import https_fixture
import wan_core


def _ffmpeg():
    return https_fixture.find_ffmpeg()


@unittest.skipUnless(_ffmpeg(), 'ffmpeg (imageio-ffmpeg or PATH) is required for the synthetic clip')
class SimulatedClientFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.folder.cleanup)
        ffmpeg = _ffmpeg()
        cls.video = https_fixture.synthetic_mp4_bytes(ffmpeg, Path(cls.folder.name))
        cls.fixture = https_fixture.LocalHttpsFixture(cls.video, workdir=Path(cls.folder.name) / 'tls')
        cls.fixture.start()
        cls.addClassCleanup(cls.fixture.stop)
        # Trust the fixture CA for this process (and rebuild the opener).
        cls._prev_ca = os.environ.get('PT_WAN_CA_FILE')
        cls._prev_opener = wan_core.OPENER
        os.environ['PT_WAN_CA_FILE'] = str(cls.fixture.ca)
        wan_core.OPENER = wan_core.build_opener()

        def _restore():
            if cls._prev_ca is None:
                os.environ.pop('PT_WAN_CA_FILE', None)
            else:
                os.environ['PT_WAN_CA_FILE'] = cls._prev_ca
            wan_core.OPENER = cls._prev_opener

        cls.addClassCleanup(_restore)

    def setUp(self):
        self.data = Path(tempfile.mkdtemp(dir=self.folder.name))
        self.out = self.data / 'out' / 'clip.mp4'

    def _client(self):
        class FastClient(wan_core.Client):
            def wait(self, key, task_id, out_path, wall_timeout=60, interval=0.2):
                return super().wait(key, task_id, out_path, wall_timeout=wall_timeout, interval=interval)

        return FastClient(data_dir=self.data, report=lambda *_: None)

    def test_submit_poll_and_download_synthetic_video(self):
        """Labeled simulation: create → poll twice → download a real MP4."""
        with patch.object(wan_core, 'API_BASE', self.fixture.base), \
                patch.object(wan_core, 'DATA_DIR', self.data):
            record = self._client().submit(
                [https_fixture.VALID_KEY],
                wan_core.payload('a simulated blue clip, 1 second', duration=5, resolution='720p'),
                self.out,
                )
        self.assertTrue(self.out.is_file(), 'synthetic video was not written')
        self.assertEqual(self.out.read_bytes()[4:8], b'ftyp')
        self.assertGreaterEqual(self.out.stat().st_size, 100)
        self.assertEqual(record.get('task_id'), https_fixture.TASK_ID)
        self.assertGreaterEqual(self.fixture.state.posts, 1)
        self.assertGreaterEqual(self.fixture.state.polls, 2)
        self.assertGreaterEqual(self.fixture.state.downloads, 1)

    def test_invalid_key_is_rejected_without_leaving_a_task(self):
        with patch.object(wan_core, 'API_BASE', self.fixture.base), \
                patch.object(wan_core, 'DATA_DIR', self.data):
            with self.assertRaises(wan_core.TaskError) as caught:
                self._client().submit(
                    [https_fixture.INVALID_KEY],
                    wan_core.payload('should fail', duration=5, resolution='720p'),
                    self.out,
                )
        self.assertIn(caught.exception.kind, ('AUTH', 'REJECTED', 'POOL_EXHAUSTED', 'UNCERTAIN'))
        self.assertFalse(self.out.exists())

    def test_verify_key_hits_the_fixture_models_endpoint(self):
        with patch.object(wan_core, 'API_BASE', self.fixture.base):
            ok = wan_core.verify_key(https_fixture.VALID_KEY, timeout=5)
            bad = wan_core.verify_key(https_fixture.INVALID_KEY, timeout=5)
        self.assertTrue(ok.get('ok'), ok)
        self.assertEqual(ok.get('http'), 200)
        self.assertFalse(bad.get('ok'), bad)
        self.assertEqual(bad.get('http'), 401)
        self.assertGreaterEqual(self.fixture.state.models_gets, 2)

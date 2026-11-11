"""Real frame extraction from a synthetic MP4 using the bundled or PATH ffmpeg."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))

import frame_util
import https_fixture


def _ffmpeg():
    return https_fixture.find_ffmpeg() or frame_util.find_ffmpeg()


@unittest.skipUnless(_ffmpeg(), 'ffmpeg is required for a live extract')
class LiveFfmpegExtractTests(unittest.TestCase):
    def test_extract_chain_frame_from_synthetic_clip(self):
        ffmpeg = _ffmpeg()
        self.assertTrue(Path(ffmpeg).is_file())
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            video = folder / 'clip.mp4'
            video.write_bytes(https_fixture.synthetic_mp4_bytes(ffmpeg, folder / 'build'))
            self.assertEqual(video.read_bytes()[4:8], b'ftyp')
            frame = frame_util.extract_chain_frame(video)
            self.assertTrue(frame.is_file())
            data = frame.read_bytes()
            self.assertTrue(data.startswith(b'\xff\xd8'), 'expected a JPEG frame')
            self.assertGreater(len(data), 200)
            # Sidecar name is part of the product contract.
            self.assertEqual(frame.name, 'clip_chain_frame.jpg')

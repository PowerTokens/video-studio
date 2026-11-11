"""Local HTTPS simulation of the PowerTokens video API.

THIS IS A SIMULATION. It never talks to api.powertokens.ai, never bills, and
never accepts or stores a real PowerTokens credential. Responses are marked
with "simulation": true. Use it only in tests and Mac acceptance.

The server speaks HTTPS on 127.0.0.1 with a temporary self-signed certificate
because wan_core.resolve_api_base and download() require HTTPS.
"""
from __future__ import annotations

import json
import os
import ssl
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

SIM_WARNING = 'local fixture simulation, not PowerTokens'
VALID_KEY = 'sk-sim-acceptance-not-a-real-key'
INVALID_KEY = 'sk-sim-invalid-not-a-real-key'
TASK_ID = 'task_sim_acceptance_001'


def generate_localhost_cert(folder: Path) -> tuple[Path, Path, Path]:
    """Return (ca_or_cert_pem, key_pem, cert_pem) for HTTPS on 127.0.0.1.

    The certificate is self-signed and is also used as the trust anchor
    (PT_WAN_CA_FILE / ssl.create_default_context(cafile=...)).
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    cert = folder / 'fixture-cert.pem'
    key = folder / 'fixture-key.pem'
    # -addext needs OpenSSL 1.1.1+ / LibreSSL equivalents on current macOS CI.
    cmd = [
        'openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-sha256', '-days', '2',
        '-nodes', '-keyout', str(key), '-out', str(cert),
        '-subj', '/CN=127.0.0.1',
        '-addext', 'subjectAltName=IP:127.0.0.1,DNS:localhost',
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    if not cert.is_file() or not key.is_file():
        raise RuntimeError('openssl did not write the fixture certificate')
    return cert, key, cert


class FixtureState:
    def __init__(self, video_bytes: bytes):
        self.video_bytes = video_bytes
        self.polls = 0
        self.posts = 0
        self.models_gets = 0
        self.downloads = 0
        self.lock = threading.Lock()
        # Succeed on the second poll so wait() really polls more than once.
        self.succeed_after = 2
        self.video_url = ''


def _bearer(handler: BaseHTTPRequestHandler) -> str:
    raw = handler.headers.get('Authorization') or ''
    if raw.lower().startswith('bearer '):
        return raw[7:].strip()
    return ''


def make_handler(state: FixtureState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def _json(self, code: int, body: dict):
            body = dict(body)
            body.setdefault('simulation', True)
            body.setdefault('warning', SIM_WARNING)
            raw = json.dumps(body).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(raw)

        def _auth_or_reject(self) -> Optional[str]:
            key = _bearer(self)
            if key == VALID_KEY:
                return key
            self._json(401, {'error': {'code': 'invalid_api_key', 'message': 'simulation rejected key'}})
            return None

        def do_GET(self):  # noqa: N802
            parsed = self.path.split('?')[0]
            if parsed == '/media/clip.mp4':
                with state.lock:
                    state.downloads += 1
                data = state.video_bytes
                self.send_response(200)
                self.send_header('Content-Type', 'video/mp4')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Accept-Ranges', 'bytes')
                self.end_headers()
                self.wfile.write(data)
                return
            if parsed == '/v1/models':
                with state.lock:
                    state.models_gets += 1
                if self._auth_or_reject() is None:
                    return
                self._json(200, {'data': [{'id': 'wan3.0-video', 'simulation': True}]})
                return
            if parsed.startswith('/v1/videos/') and parsed.endswith('/content'):
                # Same bytes as the media URL; wait() may try this fallback.
                if self._auth_or_reject() is None:
                    return
                data = state.video_bytes
                self.send_response(200)
                self.send_header('Content-Type', 'video/mp4')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if parsed.startswith('/v1/videos/'):
                if self._auth_or_reject() is None:
                    return
                with state.lock:
                    state.polls += 1
                    polls = state.polls
                if polls < state.succeed_after:
                    self._json(200, {'id': TASK_ID, 'status': 'in_progress', 'progress': 40})
                    return
                self._json(200, {
                    'id': TASK_ID,
                    'status': 'succeeded',
                    'progress': 100,
                    'metadata': {'url': state.video_url},
                })
                return
            self._json(404, {'error': 'not found'})

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get('Content-Length') or '0')
            self.rfile.read(max(0, length))
            parsed = self.path.split('?')[0]
            if parsed != '/v1/videos':
                self._json(404, {'error': 'not found'})
                return
            if self._auth_or_reject() is None:
                return
            with state.lock:
                state.posts += 1
            self._json(200, {'id': TASK_ID, 'status': 'queued'})

    return Handler


class LocalHttpsFixture:
    """Threading HTTPS server on 127.0.0.1. Call start()/stop() or use as a context manager."""

    def __init__(self, video_bytes: bytes, workdir: Optional[Path] = None):
        self._own_dir = workdir is None
        self.workdir = Path(workdir or tempfile.mkdtemp(prefix='pt-api-fixture-'))
        self.cert, self.key, self.ca = generate_localhost_cert(self.workdir)
        self.state = FixtureState(video_bytes)
        self.server: Optional[ThreadingHTTPServer] = None
        self.thread: Optional[threading.Thread] = None
        self.base = ''
        self.video_url = ''

    def start(self) -> 'LocalHttpsFixture':
        handler = make_handler(self.state)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        host, port = self.server.server_address
        self.base = 'https://127.0.0.1:%d' % port
        self.video_url = self.base + '/media/clip.mp4'
        self.state.video_url = self.video_url
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(self.cert), str(self.key))
        self.server.socket = ctx.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self) -> None:
        if self.server is not None:
            try:
                self.server.shutdown()
            except Exception:
                pass
            try:
                self.server.server_close()
            except Exception:
                pass
        if self.thread is not None:
            self.thread.join(timeout=5)

    def __enter__(self) -> 'LocalHttpsFixture':
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


def synthetic_mp4_bytes(ffmpeg: str, folder: Path) -> bytes:
    """A real short MP4 (ftyp-compatible) made with ffmpeg. No network."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'synthetic.mp4'
    cmd = [
        ffmpeg, '-hide_banner', '-loglevel', 'error', '-y',
        '-f', 'lavfi', '-i', 'color=c=blue:s=320x240:d=1:r=10',
        '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    data = path.read_bytes()
    if data[4:8] != b'ftyp':
        raise RuntimeError('synthetic video is not a recognizable MP4')
    return data


def find_ffmpeg() -> Optional[str]:
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.isfile(exe):
            return exe
    except Exception:
        pass
    import shutil
    return shutil.which('ffmpeg')

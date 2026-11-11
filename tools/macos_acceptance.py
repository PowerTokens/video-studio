#!/usr/bin/env python3
"""Mac acceptance runner for the unsigned Apple Silicon desktop app.

Runs on our macOS CI (or an Apple Silicon Mac). It does not use the user's
computer, does not call the live paid PowerTokens API, and does not perform
a real PowerTokens OAuth login. Generation and OAuth checks are clearly
labeled simulations against local fixtures.

Exit status is 0 only when every required phase passed.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))

REPORT: dict[str, Any] = {
    'title': 'PowerTokens Video Studio macOS acceptance',
    'simulation_disclaimer': (
        'Generation and OAuth checks use a local fixture or a fake provider. '
        'They are not a live PowerTokens login and not a paid generation.'
    ),
    'phases': [],
    'ok': False,
}


def log(msg: str) -> None:
    print(msg, flush=True)


def phase(name: str, required: bool = True):
    def decorator(fn):
        def wrapped(*args, **kwargs):
            item = {'name': name, 'required': required, 'ok': False, 'detail': '', 'skipped': False}
            log('=== %s ===' % name)
            try:
                detail = fn(*args, **kwargs) or 'ok'
                item['ok'] = True
                item['detail'] = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)
                log('PASS %s: %s' % (name, item['detail']))
            except Exception as exc:
                item['ok'] = False
                item['detail'] = '%s: %s' % (type(exc).__name__, exc)
                item['trace'] = traceback.format_exc()
                log('FAIL %s: %s' % (name, exc))
                if os.environ.get('PT_ACCEPTANCE_TRACE'):
                    log(item['trace'])
            REPORT['phases'].append(item)
            return item['ok']
        return wrapped
    return decorator


def run(cmd, **kwargs):
    return subprocess.run(cmd, check=kwargs.pop('check', True), text=True,
                          capture_output=kwargs.pop('capture_output', True), **kwargs)


def screencapture(path: Path, extra: Optional[list] = None) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ['screencapture', '-x']
    if extra:
        cmd.extend(extra)
    cmd.append(str(path))
    try:
        run(cmd)
        return path.is_file() and path.stat().st_size > 0
    except Exception as exc:
        log('screencapture failed: %s' % exc)
        return False


def osascript(source: str) -> str:
    result = subprocess.run(['osascript', '-e', source], text=True, capture_output=True)
    return (result.stdout or '').strip()


def list_windows() -> str:
    return osascript('''
        tell application "System Events"
            set out to ""
            repeat with p in (application processes whose background only is false)
                try
                    set out to out & name of p & " windows=" & ((count of windows of p) as text) & linefeed
                end try
            end repeat
            return out
        end tell
    ''')


def dismiss_leftover_tcc_dialogs() -> str:
    """Click Don't Allow on leftover Local Network prompts from earlier Python tests.

    Does not disable the permission system; it only clears a stale dialog so the
    packaged-app screenshot is about the app itself.
    """
    script = '''
        tell application "System Events"
            set dismissed to 0
            repeat with p in application processes
                try
                    repeat with w in windows of p
                        set t to name of w as text
                        if t contains "local network" or t contains "局域网" then
                            try
                                click button "Don't Allow" of w
                                set dismissed to dismissed + 1
                            end try
                        end if
                    end repeat
                end try
            end repeat
            return dismissed as text
        end tell
    '''
    return osascript(script) or '0'


def find_bundled_ffmpeg(app: Path) -> Optional[Path]:
    matches = list(app.rglob('ffmpeg*'))
    matches = [p for p in matches if p.is_file() and os.access(p, os.X_OK)]
    return matches[0] if matches else None


@phase('environment')
def phase_environment(out: Path) -> str:
    if sys.platform != 'darwin':
        raise RuntimeError('acceptance must run on macOS, not %s' % sys.platform)
    if platform.machine() != 'arm64':
        raise RuntimeError('acceptance is for Apple Silicon; got %s' % platform.machine())
    info = {
        'system': platform.platform(),
        'machine': platform.machine(),
        'python': sys.version.split()[0],
        'cwd': str(Path.cwd()),
    }
    (out / 'environment.json').write_text(json.dumps(info, indent=2), encoding='utf-8')
    log(json.dumps(info))
    return '%s %s Python %s' % (info['system'], info['machine'], info['python'])


@phase('tcc_baseline')
def phase_tcc_baseline(out: Path) -> str:
    before = out / 'desktop-before-packaged.png'
    screencapture(before)
    windows = list_windows()
    (out / 'windows-before.txt').write_text(windows, encoding='utf-8')
    dismissed = dismiss_leftover_tcc_dialogs()
    after_dismiss = out / 'desktop-after-dismiss.png'
    screencapture(after_dismiss)
    return 'dismissed=%s; windows logged' % dismissed


@phase('live_paths')
def phase_live_paths() -> str:
    import platform_paths
    home = Path.home()
    env = {k: v for k, v in os.environ.items() if k != 'LOCALAPPDATA'}
    from unittest.mock import patch
    with patch.dict(os.environ, env, clear=True):
        data = platform_paths.user_data_dir()
        videos = platform_paths.default_output_dir()
    if data != home / 'Library' / 'Application Support' / 'PowerTokensWan':
        raise RuntimeError('unexpected data dir %s' % data)
    if videos != home / 'Movies' / 'PowerTokensVideoStudio':
        raise RuntimeError('unexpected output dir %s' % videos)
    # Isolated override still wins — this is what acceptance uses for writes.
    override = Path(tempfile.mkdtemp(prefix='pt-path-override-'))
    with patch.dict(os.environ, {'LOCALAPPDATA': str(override)}):
        if platform_paths.user_data_dir() != override / 'PowerTokensWan':
            raise RuntimeError('LOCALAPPDATA override failed')
    shutil.rmtree(override, ignore_errors=True)
    return 'Application Support + Movies defaults; LOCALAPPDATA override works'


@phase('live_keychain')
def phase_live_keychain(out: Path) -> str:
    import secret_store
    service = 'PowerTokens Video Studio CI Accept ' + uuid.uuid4().hex
    account = 'accept-dummy-' + uuid.uuid4().hex[:16]
    os.environ['PT_KEYCHAIN_SERVICE'] = service
    os.environ['PT_KEYCHAIN_ACCOUNT'] = account
    folder = Path(tempfile.mkdtemp(prefix='pt-keychain-accept-'))
    path = folder / 'keys.json'
    dummy = ['sk-accept-dummy-not-a-real-key']
    try:
        secret_store.keychain_set(service, account, b'sk-accept-first')
        if secret_store.keychain_get(service, account) != b'sk-accept-first':
            raise RuntimeError('keychain get mismatch after set')
        secret_store.keychain_set(service, account, b'sk-accept-updated')
        if secret_store.keychain_get(service, account) != b'sk-accept-updated':
            raise RuntimeError('keychain get mismatch after update')
        secret_store.write_remembered_keys(path, dummy)
        text = path.read_text(encoding='utf-8')
        if 'sk-accept-dummy-not-a-real-key' in text:
            raise RuntimeError('key material leaked into keys.json')
        if secret_store.read_remembered_keys(path) != dummy:
            raise RuntimeError('remembered keys did not round-trip')
        # Process "restart": a fresh interpreter reads the same item.
        script = (
            'import json,secret_store,sys; '
            'print(json.dumps(secret_store.read_remembered_keys(sys.argv[1])))'
        )
        env = os.environ.copy()
        env['PYTHONPATH'] = str(ROOT)
        proc = subprocess.run(
            [sys.executable, '-c', script, str(path)],
            check=True, capture_output=True, text=True, env=env, cwd=str(ROOT),
        )
        if json.loads(proc.stdout) != dummy:
            raise RuntimeError('subprocess restart did not read remembered keys: %s' % proc.stdout)
        secret_store.clear_remembered_keys(path)
        try:
            secret_store.keychain_get(service, account)
            raise RuntimeError('keychain item still present after clear')
        except secret_store.SecretStoreError:
            pass
        (out / 'keychain-accept.txt').write_text(
            'isolated service=%s account=%s; write/read/update/delete and restart ok\n' % (service, account),
            encoding='utf-8',
        )
        return 'CRUD + remember across subprocess restart (isolated dummy item, cleaned up)'
    finally:
        try:
            secret_store.keychain_delete(service, account)
        except Exception:
            pass
        shutil.rmtree(folder, ignore_errors=True)


@phase('remember_across_app_restart')
def phase_remember_app(out: Path) -> str:
    import tkinter as tk
    from unittest.mock import patch
    import app as app_mod
    import batch_ui
    import i18n
    import secret_store
    import wan_core

    service = os.environ.get('PT_KEYCHAIN_SERVICE') or ('PowerTokens Video Studio CI Accept ' + uuid.uuid4().hex)
    account = os.environ.get('PT_KEYCHAIN_ACCOUNT') or ('accept-dummy-' + uuid.uuid4().hex[:16])
    os.environ['PT_KEYCHAIN_SERVICE'] = service
    os.environ['PT_KEYCHAIN_ACCOUNT'] = account
    folder = Path(tempfile.mkdtemp(prefix='pt-app-remember-'))
    dummy = ['sk-accept-dummy-app-remember']
    try:
        def start_app():
            patches = [
                patch.object(app_mod, 'DATA_DIR', folder),
                patch.object(batch_ui, 'CHARACTER_SETTING_PATH', folder / 'characters.json'),
                patch.object(i18n, 'SETTINGS_PATH', folder / 'settings.json'),
                patch.object(wan_core, 'DATA_DIR', folder),
                patch.object(app_mod.messagebox, 'showerror', lambda *a, **k: None),
                patch.object(app_mod.messagebox, 'showwarning', lambda *a, **k: None),
                patch.object(app_mod.messagebox, 'showinfo', lambda *a, **k: None),
            ]
            for item in patches:
                item.start()
            root = tk.Tk()
            gui = app_mod.App(root)
            root.update()
            return root, gui, patches

        def stop_app(root, patches):
            for pending in root.tk.splitlist(root.tk.call('after', 'info')):
                try:
                    root.after_cancel(pending)
                except Exception:
                    pass
            root.destroy()
            for item in patches:
                item.stop()

        root, gui, patches = start_app()
        gui.keys = list(dummy)
        gui.remember.set(True)
        gui.save_keys()
        gui.sync_keys()
        root.update()
        if not (folder / 'keys.json').is_file():
            raise RuntimeError('keys.json was not written')
        stop_app(root, patches)

        root, gui, patches = start_app()
        try:
            if gui.keys != dummy:
                raise RuntimeError('keys not restored after App restart: %r' % gui.keys)
            if not gui.remember.get():
                raise RuntimeError('remember checkbox was not restored')
            screencapture(out / 'app-remember-restart.png')
        finally:
            stop_app(root, patches)
        return 'App load_keys restored dummy keys from isolated Keychain after restart'
    finally:
        try:
            secret_store.keychain_delete(service, account)
        except Exception:
            pass
        shutil.rmtree(folder, ignore_errors=True)


@phase('packaged_ffmpeg_extract')
def phase_ffmpeg(app: Path, out: Path) -> str:
    import frame_util
    import https_fixture
    from unittest.mock import patch

    bundled = find_bundled_ffmpeg(app)
    if bundled is None:
        raise RuntimeError('bundled ffmpeg not found in %s' % app)
    work = out / 'ffmpeg-work'
    work.mkdir(parents=True, exist_ok=True)
    video = work / 'synthetic.mp4'
    video.write_bytes(https_fixture.synthetic_mp4_bytes(str(bundled), work / 'build'))
    with patch.object(frame_util, 'find_ffmpeg', return_value=str(bundled)):
        frame = frame_util.extract_chain_frame(video, work / 'frame.jpg')
    data = frame.read_bytes()
    if not data.startswith(b'\xff\xd8') or len(data) < 200:
        raise RuntimeError('extracted frame does not look like a JPEG')
    (out / 'ffmpeg-path.txt').write_text(str(bundled) + '\n', encoding='utf-8')
    shutil.copy2(frame, out / 'extracted-chain-frame.jpg')
    return 'bundled %s extracted a JPEG (%d bytes)' % (bundled.name, len(data))


@phase('simulated_generation')
def phase_generation(out: Path) -> str:
    """SIMULATION: wan_core.Client against a local HTTPS fixture. Not a paid API call."""
    import https_fixture
    import wan_core
    from unittest.mock import patch

    ffmpeg = https_fixture.find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError('no ffmpeg to build the synthetic clip')
    work = out / 'generation-sim'
    work.mkdir(parents=True, exist_ok=True)
    video = https_fixture.synthetic_mp4_bytes(ffmpeg, work)
    fixture = https_fixture.LocalHttpsFixture(video, workdir=work / 'tls')
    fixture.start()
    prev_ca = os.environ.get('PT_WAN_CA_FILE')
    prev_opener = wan_core.OPENER
    try:
        os.environ['PT_WAN_CA_FILE'] = str(fixture.ca)
        wan_core.OPENER = wan_core.build_opener()
        data = work / 'data'
        dest = work / 'downloaded.mp4'

        class FastClient(wan_core.Client):
            def wait(self, key, task_id, out_path, wall_timeout=60, interval=0.2):
                return super().wait(key, task_id, out_path, wall_timeout=wall_timeout, interval=interval)

        with patch.object(wan_core, 'API_BASE', fixture.base), patch.object(wan_core, 'DATA_DIR', data):
            record = FastClient(data_dir=data, report=lambda text: log('  [sim] ' + text)).submit(
                [https_fixture.VALID_KEY],
                wan_core.payload('SIMULATION: a one-second blue clip', duration=5, resolution='720p'),
                dest,
            )
        if not dest.is_file() or dest.read_bytes()[4:8] != b'ftyp':
            raise RuntimeError('simulation did not download a real MP4')
        shutil.copy2(dest, out / 'simulated-download.mp4')
        summary = {
            'simulation': True,
            'warning': https_fixture.SIM_WARNING,
            'task_id': record.get('task_id'),
            'bytes': dest.stat().st_size,
            'posts': fixture.state.posts,
            'polls': fixture.state.polls,
            'downloads': fixture.state.downloads,
            'not': 'live PowerTokens generation',
        }
        (out / 'generation-sim.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
        return 'SIMULATION downloaded %d-byte MP4 after %d polls' % (summary['bytes'], summary['polls'])
    finally:
        fixture.stop()
        if prev_ca is None:
            os.environ.pop('PT_WAN_CA_FILE', None)
        else:
            os.environ['PT_WAN_CA_FILE'] = prev_ca
        wan_core.OPENER = prev_opener


@phase('oauth_loopback_fake_provider')
def phase_oauth(out: Path) -> str:
    """Fake local provider + loopback callback. Not a real PowerTokens login."""
    import oauth_desktop
    from unittest.mock import patch
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import threading
    import urllib.parse
    import urllib.request

    state = {'token': 0, 'userinfo': 0}

    class ApiHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def _send(self, payload: dict):
            raw = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            length = int(self.headers.get('Content-Length') or '0')
            self.rfile.read(max(0, length))
            state['token'] += 1
            self._send({'access_token': 'fake-access-not-real', 'token_type': 'Bearer', 'expires_in': 1800})

        def do_GET(self):
            state['userinfo'] += 1
            self._send({'name': 'Fake Acceptance User', 'credits': 0, 'key': 'sk-fake-oauth-not-a-real-key', 'user_id': 'sim'})

    api = HTTPServer(('127.0.0.1', 0), ApiHandler)
    port = api.server_port
    thread = threading.Thread(target=lambda: (api.handle_request(), api.handle_request()), daemon=True)
    thread.start()
    opened = {}

    def fake_open(url):
        opened['url'] = url
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))

        def hit():
            time.sleep(0.05)
            urllib.request.urlopen('%s?code=fake-code&state=%s' % (query['redirect_uri'], query['state']), timeout=5)

        threading.Thread(target=hit, daemon=True).start()
        return True

    try:
        with patch.object(oauth_desktop, 'TOKEN_URL', 'http://127.0.0.1:%d/oauth/token' % port), \
                patch.object(oauth_desktop, 'USERINFO_URL', 'http://127.0.0.1:%d/oauth/userinfo' % port):
            result = oauth_desktop.login(
                open_browser=True, timeout=8, preferred_port=0, browser_opener=fake_open)
    finally:
        thread.join(timeout=3)
        api.server_close()

    if result.get('access_token') != 'fake-access-not-real':
        raise RuntimeError('unexpected token %r' % result.get('access_token'))
    if 'code_challenge' not in opened.get('url', ''):
        raise RuntimeError('authorize URL missing PKCE challenge')
    if not opened['url'].startswith(oauth_desktop.AUTHORIZE_URL):
        # The authorize URL still points at the real host in the string we "open",
        # but we never navigate a browser there — fake_open intercepts it.
        raise RuntimeError('unexpected authorize url')
    summary = {
        'simulation': True,
        'real_powertokens_login': False,
        'callback': 'loopback 127.0.0.1',
        'token_hits': state['token'],
        'userinfo_hits': state['userinfo'],
        'key_hint': 'sk-fake-oauth-not-a-real-key',
        'note': 'browser_opener was intercepted; the real authorize page was not opened',
    }
    (out / 'oauth-sim.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    return 'FAKE provider: loopback callback, token + userinfo (not real PowerTokens login)'


@phase('gui_tabs_and_errors')
def phase_gui(out: Path) -> str:
    import tkinter as tk
    from unittest.mock import patch
    import app as app_mod
    import batch_ui
    import i18n
    import wan_core

    shots = out / 'gui'
    shots.mkdir(parents=True, exist_ok=True)
    tabs = ('generate', 'chat', 'compare', 'batch', 'history', 'apikey')
    problems = []
    folder = Path(tempfile.mkdtemp(prefix='pt-gui-accept-'))

    def start(lang):
        i18n.set_language(lang)
        patches = [
            patch.object(app_mod, 'DATA_DIR', folder),
            patch.object(batch_ui, 'CHARACTER_SETTING_PATH', folder / 'characters.json'),
            patch.object(i18n, 'SETTINGS_PATH', folder / 'settings.json'),
            patch.object(wan_core, 'DATA_DIR', folder),
            patch.object(app_mod.App, 'start_oauth_login', lambda *a, **k: None),
        ]
        for item in patches:
            item.start()
        root = tk.Tk()
        gui = app_mod.App(root)
        root.geometry('1280x900+0+0')
        root.lift()
        root.update()
        return root, gui, patches

    def stop(root, patches):
        for pending in root.tk.splitlist(root.tk.call('after', 'info')):
            try:
                root.after_cancel(pending)
            except Exception:
                pass
        root.destroy()
        for item in patches:
            item.stop()

    def grab(root, path: Path):
        root.update()
        time.sleep(0.3)
        if screencapture(path):
            return
        try:
            from PIL import ImageGrab
            ImageGrab.grab(all_screens=True).save(path)
        except Exception as exc:
            log('grab failed: %s' % exc)

    try:
        for lang in ('en', 'zh'):
            root, gui, patches = start(lang)
            try:
                started = time.monotonic()
                for index, name in enumerate(tabs):
                    gui.tabs.select(index)
                    root.update()
                    time.sleep(0.4)
                    grab(root, shots / ('%s-%s.png' % (lang, name)))
                # Error path: Generate with no key must not open a real OAuth browser.
                calls = {'login': 0, 'info': []}

                def fake_login(*_a, **_k):
                    calls['login'] += 1

                def fake_info(*a, **k):
                    calls['info'].append(a)

                with patch.object(app_mod.App, 'start_oauth_login', fake_login), \
                        patch.object(app_mod.messagebox, 'showinfo', fake_info), \
                        patch.object(app_mod.messagebox, 'showerror', lambda *a, **k: None):
                    gui.keys = []
                    gui.generate()
                    root.update()
                    grab(root, shots / ('%s-generate-no-key.png' % lang))
                if calls['login'] < 1:
                    problems.append('%s: generate with no key did not request login' % lang)
                # Invalid prompt/settings path
                with patch.object(app_mod.messagebox, 'showerror', lambda *a, **k: calls.setdefault('errors', []).append(a)):
                    gui.keys = ['sk-dummy-not-used-because-prompt-invalid']
                    gui.prompt.delete('1.0', 'end')
                    gui.duration.set('0')
                    gui.generate()
                    root.update()
                    grab(root, shots / ('%s-generate-bad-input.png' % lang))
                elapsed = time.monotonic() - started
                if elapsed < 3:
                    time.sleep(3 - elapsed)
            finally:
                stop(root, patches)
        (shots / 'layout-notes.txt').write_text('\n'.join(problems) or 'no functional problems recorded', encoding='utf-8')
        if problems:
            raise RuntimeError('; '.join(problems))
        return 'EN/ZH all six tabs + no-key and bad-input error paths (OAuth login patched out)'
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _launch_app_binary(binary: Path, data: Path, log_path: Path) -> int:
    env = os.environ.copy()
    env['LOCALAPPDATA'] = str(data)
    env.pop('POWERTOKENS_API_KEY', None)
    env.pop('POWERTOKENS_API_KEYS', None)
    env.pop('POWERTOKENS_API_TOKEN', None)
    env.pop('POWERTOKENS_API_BASE', None)
    with open(log_path, 'ab') as fh:
        proc = subprocess.Popen([str(binary)], env=env, stdout=fh, stderr=fh, cwd=str(data))
    return proc.pid


def _wait_window(name: str, timeout: int = 25) -> bool:
    for _ in range(timeout):
        time.sleep(1)
        count = osascript('''
            tell application "System Events"
                if exists process "%s" then
                    count of windows of process "%s"
                else
                    0
                end if
            end tell
        ''' % (name, name))
        digits = ''.join(ch for ch in count if ch.isdigit()) or '0'
        if int(digits) > 0:
            return True
    return False


def _quit_pid(pid: int) -> None:
    try:
        os.kill(pid, 15)
    except OSError:
        return
    for _ in range(10):
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.5)
    try:
        os.kill(pid, 9)
    except OSError:
        pass


def _crash_logs_since(stamp: float, dest: Path) -> int:
    roots = [
        Path.home() / 'Library' / 'Logs' / 'DiagnosticReports',
        Path('/Library/Logs/DiagnosticReports'),
    ]
    dest.mkdir(parents=True, exist_ok=True)
    copied = 0
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.glob('*'):
            try:
                recent = path.stat().st_mtime >= stamp - 5
                name = path.name
                related = ('PowerTokens' in name) or ('python' in name.lower())
                if recent and related:
                    shutil.copy2(path, dest / path.name)
                    copied += 1
            except OSError:
                continue
    return copied


@phase('packaged_app_from_dmg')
def phase_packaged(app: Path, dmg: Path, out: Path) -> str:
    if not app.is_dir():
        raise RuntimeError('missing app bundle %s' % app)
    binary = app / 'Contents' / 'MacOS' / 'PowerTokensVideoStudio'
    if not binary.is_file():
        raise RuntimeError('missing binary')
    mount = Path(tempfile.mkdtemp(prefix='pt-dmg-accept-'))
    data = Path(tempfile.mkdtemp(prefix='pt-dmg-data-'))
    log_path = out / 'packaged-launch.log'
    stamp = time.time()
    pid = None
    try:
        run(['hdiutil', 'attach', str(dmg), '-readonly', '-nobrowse', '-mountpoint', str(mount)])
        mounted_app = mount / 'PowerTokensVideoStudio.app'
        if not mounted_app.is_dir():
            raise RuntimeError('DMG does not contain the app')
        mounted_bin = mounted_app / 'Contents' / 'MacOS' / 'PowerTokensVideoStudio'
        pid = _launch_app_binary(mounted_bin, data, log_path)
        shown = _wait_window('PowerTokensVideoStudio', timeout=20)
        # Keep the window up long enough to be a real launch, not a 1-second flash.
        time.sleep(8)
        screencapture(out / 'packaged-from-dmg.png')
        windows = list_windows()
        (out / 'windows-packaged.txt').write_text(windows, encoding='utf-8')
        alive = True
        try:
            os.kill(pid, 0)
        except OSError:
            alive = False
        if not alive:
            raise RuntimeError('packaged app exited early; log: %s' % log_path.read_text(errors='replace')[:2000])
        if not shown:
            # Still a pass of "process stayed up", but record the limitation.
            (out / 'packaged-window-limitation.txt').write_text(
                'process stayed up but System Events listed no window\n' + windows, encoding='utf-8')
        crashes = _crash_logs_since(stamp, out / 'crash-reports')
        note = 'window=%s crashes_copied=%d data_entries=%d' % (
            shown, crashes, sum(1 for _ in data.rglob('*')))
        if not shown:
            raise RuntimeError('packaged app from DMG stayed running but no window was listed (%s)' % note)
        return 'launched from mounted DMG, window visible, ~8s lifetime, %s' % note
    finally:
        if pid:
            _quit_pid(pid)
        subprocess.run(['hdiutil', 'detach', str(mount), '-quiet', '-force'], capture_output=True)
        shutil.rmtree(mount, ignore_errors=True)
        # Keep data dir contents as evidence, then remove the temp parent copy into out.
        evidence = out / 'packaged-data-dir'
        if data.exists():
            if evidence.exists():
                shutil.rmtree(evidence, ignore_errors=True)
            shutil.copytree(data, evidence, dirs_exist_ok=True)
        shutil.rmtree(data, ignore_errors=True)


def write_report(out: Path) -> bool:
    required_failed = [p for p in REPORT['phases'] if p['required'] and not p['ok'] and not p.get('skipped')]
    skipped = [p['name'] for p in REPORT['phases'] if p.get('skipped')]
    REPORT['ok'] = not required_failed
    REPORT['failed'] = [p['name'] for p in required_failed]
    REPORT['skipped'] = skipped
    (out / 'report.json').write_text(json.dumps(REPORT, indent=2, ensure_ascii=False), encoding='utf-8')
    lines = ['# macOS acceptance report', '', REPORT['simulation_disclaimer'], '']
    for item in REPORT['phases']:
        flag = 'PASS' if item['ok'] else ('SKIP' if item.get('skipped') else 'FAIL')
        lines.append('- **%s** %s — %s' % (flag, item['name'], item['detail']))
    lines.append('')
    lines.append('Overall: %s' % ('PASS' if REPORT['ok'] else 'FAIL'))
    (out / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    log('\n'.join(lines))
    return REPORT['ok']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--app', default='dist/PowerTokensVideoStudio.app')
    parser.add_argument('--dmg', default='dist/PowerTokensVideoStudio-macOS-arm64.dmg')
    parser.add_argument('--out', default='dist/acceptance')
    args = parser.parse_args()
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.mkdir(parents=True, exist_ok=True)
    app = Path(args.app)
    if not app.is_absolute():
        app = ROOT / app
    dmg = Path(args.dmg)
    if not dmg.is_absolute():
        dmg = ROOT / dmg

    os.chdir(ROOT)
    phase_environment(out)
    phase_tcc_baseline(out)
    phase_live_paths()
    phase_live_keychain(out)
    phase_remember_app(out)
    if app.is_dir():
        phase_ffmpeg(app, out)
    else:
        REPORT['phases'].append({
            'name': 'packaged_ffmpeg_extract', 'required': True, 'ok': False,
            'detail': 'app bundle missing at %s' % app, 'skipped': False,
        })
    phase_generation(out)
    phase_oauth(out)
    phase_gui(out)
    if app.is_dir() and dmg.is_file():
        phase_packaged(app, dmg, out)
    else:
        REPORT['phases'].append({
            'name': 'packaged_app_from_dmg', 'required': True, 'ok': False,
            'detail': 'missing app or dmg', 'skipped': False,
        })
    ok = write_report(out)
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()

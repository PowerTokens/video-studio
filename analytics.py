"""Minimal PostHog capture client for Video Studio desktop.

This is not the official PostHog SDK: autocapture, session replay, heatmaps,
and person-profile enrichment are intentionally absent. Only allowlisted
critical events are queued and sent. Payloads are stripped to a property
allowlist; prompts, keys, tokens, emails, user assets, raw error text, OAuth
codes, and URL query strings are never accepted.

Network failures fail open (the UI is never blocked). The outbound queue is
bounded. Telemetry is off until the user grants consent, and stays off when
the project key is missing.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

EVENTS = frozenset({
    'app_open',
    'login_success',
    'generation_start',
    'generation_success',
    'generation_failed',
    'video_download',
})

ALLOWED_PROPS = frozenset({
    'platform',
    'app_version',
    'session_id',
    'install_id',
    'surface',
    'model',
    'duration_ms',
    'error_code',
    'generation_id',
    'requested_duration_s',
    'key_applied',
    'account_linked',
})

# Rejected even if someone later adds them to ALLOWED_PROPS by mistake.
FORBIDDEN_PROP_KEYS = frozenset({
    'prompt', 'key', 'api_key', 'token', 'access_token', 'refresh_token',
    'authorization', 'authorization_code', 'code', 'oauth_code', 'verifier',
    'code_verifier', 'email', 'name', 'display_name', 'avatar', 'picture',
    'messages', 'error', 'error_detail', 'error_text', 'error_message',
    'message', 'detail', 'exception', 'stack', 'traceback', 'url', 'href',
    'search', 'query', 'current_url', '$current_url', 'pathname', '$pathname',
    'referrer', '$referrer', 'video_url', 'media', 'image', 'assets',
    'credits', 'balance', 'password', 'secret', 'raw', 'userinfo', 'profile',
    'sub', 'user_id', 'distinct_email',
})

SAFE_ERROR_CODES = frozenset({
    'AUTH', 'QUOTA', 'NOT_ALLOWED', 'RATE_LIMIT', 'PARAM', 'NETWORK',
    'DOWNLOAD', 'PAUSED', 'TIMEOUT', 'UNKNOWN', 'SERVER_FAILED', 'QUERY_DOWN',
    'QUERY_AUTH', 'READY_NO_URL', 'UNCERTAIN', 'STOPPED', 'CANCELLED',
    'VALIDATION', 'POOL_EXHAUSTED', 'REJECTED', 'STORAGE', 'RANGE_UNSUPPORTED',
    'NO_RESUME', 'QUERY_FORBIDDEN',
})

SAFE_SURFACES = frozenset({'generate', 'compare', 'batch'})

PLATFORM = 'desktop'
QUEUE_MAX = 50
SESSION_IDLE_S = 30 * 60
FLUSH_TIMEOUT_S = 3
LIB_NAME = 'video-studio-analytics'
LIB_VERSION = '1.0.0'
STATE_NAME = 'analytics.json'
CONSENT_UNSET = 'unset'
CONSENT_GRANTED = 'granted'
CONSENT_DENIED = 'denied'

Transport = Callable[[dict], None]


def _now() -> float:
    return time.time()


def opaque_account_id(user_id: Any) -> Optional[str]:
    """Hash an internal user id so the raw identifier never leaves the device.

    Returns None for empty values, emails, or whitespace-bearing strings.
    """
    raw = str(user_id or '').strip()
    if not raw or '@' in raw or any(ch.isspace() for ch in raw) or len(raw) < 2:
        return None
    digest = hashlib.sha256(('vs-analytics:' + raw).encode('utf-8')).hexdigest()[:16]
    return 'ptu_' + digest


def normalize_error_code(code: Any) -> str:
    text = str(code or '').strip().upper().replace('-', '_').replace(' ', '_')
    if text in SAFE_ERROR_CODES:
        return text
    aliases = {
        'SERVER_FAILED': 'SERVER_FAILED',
        'QUERY_AUTH': 'QUERY_AUTH',
        'QUERY_DOWN': 'QUERY_DOWN',
        'READY_NO_URL': 'READY_NO_URL',
        'UNCERTAIN_RELOAD': 'UNCERTAIN',
        'CANCELED': 'CANCELLED',
    }
    return aliases.get(text, 'UNKNOWN')


def sanitize_properties(event: str, props: Optional[dict]) -> Optional[dict]:
    """Return an allowlisted payload, or None if the event itself is not allowed."""
    if event not in EVENTS:
        return None
    clean: dict[str, Any] = {}
    for key, value in (props or {}).items():
        if key in FORBIDDEN_PROP_KEYS:
            continue
        if key not in ALLOWED_PROPS:
            continue
        if value is None or value is False or value is True:
            # booleans and None are fine (None will be dropped below except key_applied)
            pass
        if key == 'error_code':
            value = normalize_error_code(value)
        elif key == 'surface':
            value = str(value or '').strip().lower()
            if value not in SAFE_SURFACES:
                continue
        elif key == 'duration_ms':
            try:
                value = int(max(0, int(value)))
            except (TypeError, ValueError):
                continue
        elif key == 'requested_duration_s':
            try:
                value = int(value)
            except (TypeError, ValueError):
                continue
            if value < 0 or value > 3600:
                continue
        elif key == 'key_applied':
            value = bool(value)
        elif key == 'account_linked':
            value = bool(value)
        elif key == 'model':
            text = str(value or '').strip()
            if not text or len(text) > 80 or any(ch.isspace() for ch in text) or '@' in text:
                continue
            value = text
        elif key in ('generation_id', 'session_id', 'install_id', 'app_version', 'platform'):
            text = str(value or '').strip()
            if not text or len(text) > 80:
                continue
            value = text
        clean[key] = value
    clean['platform'] = PLATFORM
    return clean


class Analytics:
    def __init__(self, data_dir: Path, transport: Optional[Transport] = None,
                 clock: Callable[[], float] = _now, config: Optional[dict] = None,
                 app_version: str = ''):
        self.data_dir = Path(data_dir)
        self.clock = clock
        self.app_version = str(app_version or '')
        self._lock = threading.RLock()
        self._transport = transport
        self._config = dict(config or {})
        self._state = self._load_state()
        self._ensure_install_id()
        self._flush_thread: Optional[threading.Thread] = None
        self._flush_lock = threading.Lock()

    # ----- persistence -----
    def _state_path(self) -> Path:
        return self.data_dir / STATE_NAME

    def _load_state(self) -> dict:
        try:
            data = json.loads(self._state_path().read_text(encoding='utf-8'))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_state(self) -> None:
        path = self._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(self._state)
        # Never persist more than QUEUE_MAX events.
        queue = list(payload.get('queue') or [])
        payload['queue'] = queue[-QUEUE_MAX:]
        seen = dict(payload.get('generation_seen') or {})
        if len(seen) > 200:
            # Drop oldest insertions by keeping an arbitrary tail of keys.
            payload['generation_seen'] = dict(list(seen.items())[-200:])
        tmp = path.with_name(path.name + '.tmp')
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(tmp, path)

    def _ensure_install_id(self) -> str:
        install_id = str(self._state.get('install_id') or '').strip()
        if not install_id:
            install_id = uuid.uuid4().hex
            self._state['install_id'] = install_id
            self._save_state()
        return install_id

    def install_id(self) -> str:
        with self._lock:
            return self._ensure_install_id()

    # ----- consent -----
    def consent_state(self) -> str:
        value = str(self._state.get('consent') or CONSENT_UNSET).strip()
        if value in (CONSENT_GRANTED, CONSENT_DENIED, CONSENT_UNSET):
            return value
        return CONSENT_UNSET

    def is_enabled(self) -> bool:
        return self.consent_state() == CONSENT_GRANTED

    def set_consent(self, granted: bool) -> None:
        with self._lock:
            self._state['consent'] = CONSENT_GRANTED if granted else CONSENT_DENIED
            if not granted:
                self._state['queue'] = []
                self._state['account_distinct_id'] = ''
            self._save_state()
            if granted:
                self.ensure_session()
                self.capture_app_open()
                self._schedule_flush()

    # ----- session -----
    def ensure_session(self) -> str:
        """Return the current session id. A desktop session is one process, or a
        30-minute idle gap if the process is left running. Tab switches do not
        apply here; callers must not invoke capture_app_open on focus/tab change.
        """
        with self._lock:
            now = self.clock()
            session_id = str(self._state.get('session_id') or '').strip()
            last = float(self._state.get('last_activity') or 0)
            if not session_id or (last and now - last > SESSION_IDLE_S):
                session_id = uuid.uuid4().hex
                self._state['session_id'] = session_id
                self._state['session_started_at'] = now
                self._state['app_open_session'] = ''
            self._state['last_activity'] = now
            self._save_state()
            return session_id

    def session_id(self) -> str:
        return self.ensure_session()

    # ----- account -----
    def identify_account(self, user_id: Any) -> Optional[str]:
        opaque = opaque_account_id(user_id)
        with self._lock:
            self._state['account_distinct_id'] = opaque or ''
            self._save_state()
        return opaque

    def clear_account(self) -> None:
        with self._lock:
            self._state['account_distinct_id'] = ''
            self._save_state()

    def distinct_id(self) -> str:
        with self._lock:
            account = str(self._state.get('account_distinct_id') or '').strip()
            return account or self._ensure_install_id()

    # ----- config / transport -----
    def _resolved_config(self) -> dict:
        if self._config.get('project_key') or self._config.get('host'):
            key = str(self._config.get('project_key') or '').strip()
            host = str(self._config.get('host') or '').strip().rstrip('/')
            return {'project_key': key, 'host': host or 'https://us.i.posthog.com'}
        try:
            import analytics_config
            return analytics_config.load()
        except Exception:
            return {'project_key': '', 'host': 'https://us.i.posthog.com'}

    def has_destination(self) -> bool:
        return bool(self._resolved_config().get('project_key')) or self._transport is not None

    def project_key_configured(self) -> bool:
        return bool(self._resolved_config().get('project_key')) or self._transport is not None

    # ----- capture -----
    def track(self, event: str, props: Optional[dict] = None) -> bool:
        """Queue an allowlisted event. Returns True if it was accepted.

        Events are held while consent is unset, dropped when denied, and only
        flushed when consent is granted and a destination exists.
        """
        try:
            return self._track(event, props)
        except Exception:
            return False

    def _track(self, event: str, props: Optional[dict]) -> bool:
        clean = sanitize_properties(event, props)
        if clean is None:
            return False
        consent = self.consent_state()
        if consent == CONSENT_DENIED:
            return False
        with self._lock:
            session_id = self.ensure_session()
            install_id = self._ensure_install_id()
            if not self._accept_unique(event, clean):
                return False
            clean['session_id'] = session_id
            clean['install_id'] = install_id
            if self.app_version:
                clean['app_version'] = self.app_version
            account = str(self._state.get('account_distinct_id') or '').strip()
            if account:
                clean['account_linked'] = True
            body = {
                'event': event,
                'distinct_id': account or install_id,
                'properties': clean,
                'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(self.clock())),
            }
            if consent == CONSENT_UNSET:
                # Hold until the user chooses. Still bounded.
                self._enqueue(body)
                self._save_state()
                return True
            self._enqueue(body)
            self._save_state()
            self._schedule_flush()
            return True

    def _accept_unique(self, event: str, clean: dict) -> bool:
        if event == 'app_open':
            session_id = str(self._state.get('session_id') or '')
            if self._state.get('app_open_session') == session_id and session_id:
                return False
            self._state['app_open_session'] = session_id
            return True
        if event in ('generation_start', 'generation_success', 'generation_failed'):
            gen_id = str(clean.get('generation_id') or '').strip()
            if not gen_id:
                return False
            seen = dict(self._state.get('generation_seen') or {})
            already = set(seen.get(gen_id) or [])
            if event == 'generation_start':
                if 'start' in already:
                    return False
                already.add('start')
            else:
                if 'terminal' in already:
                    return False
                already.add('terminal')
            seen[gen_id] = sorted(already)
            self._state['generation_seen'] = seen
            return True
        if event == 'login_success':
            # One login_success per session + account (or anonymous session).
            token = '%s:%s' % (self._state.get('session_id') or '', self._state.get('account_distinct_id') or 'anon')
            if self._state.get('login_success_token') == token:
                return False
            self._state['login_success_token'] = token
            return True
        return True

    def _enqueue(self, body: dict) -> None:
        queue = list(self._state.get('queue') or [])
        queue.append(body)
        if len(queue) > QUEUE_MAX:
            queue = queue[-QUEUE_MAX:]
        self._state['queue'] = queue

    def capture_app_open(self) -> bool:
        return self.track('app_open', {})

    def notify_login_success(self, user_id: Any = None, key_applied: Optional[bool] = None) -> bool:
        if user_id:
            self.identify_account(user_id)
        props: dict[str, Any] = {}
        if key_applied is not None:
            props['key_applied'] = bool(key_applied)
        return self.track('login_success', props)

    def notify_generation_start(self, generation_id: str, model: str = '',
                                surface: str = 'generate',
                                requested_duration_s: Any = None) -> bool:
        props: dict[str, Any] = {
            'generation_id': generation_id,
            'surface': surface or 'generate',
        }
        if model:
            props['model'] = model
        if requested_duration_s is not None:
            props['requested_duration_s'] = requested_duration_s
        return self.track('generation_start', props)

    def notify_generation_finish(self, generation_id: str, ok: bool, model: str = '',
                                 surface: str = 'generate', duration_ms: Any = None,
                                 error_code: Any = None) -> bool:
        props: dict[str, Any] = {
            'generation_id': generation_id,
            'surface': surface or 'generate',
        }
        if model:
            props['model'] = model
        if duration_ms is not None:
            props['duration_ms'] = duration_ms
        event = 'generation_success' if ok else 'generation_failed'
        if not ok:
            props['error_code'] = normalize_error_code(error_code)
        return self.track(event, props)

    def notify_video_download(self, model: str = '', surface: str = 'generate') -> bool:
        props: dict[str, Any] = {'surface': surface or 'generate'}
        if model:
            props['model'] = model
        return self.track('video_download', props)

    # ----- flush -----
    def pending(self) -> list:
        with self._lock:
            return list(self._state.get('queue') or [])

    def flush(self) -> None:
        try:
            thread = None
            with self._lock:
                thread = self._flush_thread
            if thread is not None and thread.is_alive() and thread is not threading.current_thread():
                thread.join(timeout=FLUSH_TIMEOUT_S)
            self._flush()
        except Exception:
            return

    def _schedule_flush(self) -> None:
        if not self.is_enabled() or not self.has_destination():
            return
        with self._lock:
            if self._flush_thread and self._flush_thread.is_alive():
                return
            thread = threading.Thread(target=self.flush, name='vs-analytics-flush', daemon=True)
            self._flush_thread = thread
            thread.start()

    def _flush(self) -> None:
        if not self._flush_lock.acquire(blocking=False):
            return
        try:
            self._flush_locked()
        finally:
            self._flush_lock.release()

    def _flush_locked(self) -> None:
        if not self.is_enabled():
            return
        if not self.has_destination():
            return
        while True:
            with self._lock:
                queue = list(self._state.get('queue') or [])
                if not queue:
                    return
                item = queue[0]
            try:
                self._deliver(item)
            except Exception:
                # Leave the item queued; try again later. Fail open.
                return
            with self._lock:
                current = list(self._state.get('queue') or [])
                if current and current[0] is item or (current and current[0] == item):
                    current = current[1:]
                elif item in current:
                    current.remove(item)
                self._state['queue'] = current
                self._save_state()

    def _deliver(self, item: dict) -> None:
        if self._transport is not None:
            self._transport(item)
            return
        config = self._resolved_config()
        key = config.get('project_key') or ''
        host = config.get('host') or ''
        if not key or not host:
            raise RuntimeError('no destination')
        props = dict(item.get('properties') or {})
        props['$lib'] = LIB_NAME
        props['$lib_version'] = LIB_VERSION
        props['$geoip_disable'] = True
        props['$ip'] = None
        props['$process_person_profile'] = False
        body = {
            'api_key': key,
            'event': item['event'],
            'distinct_id': item['distinct_id'],
            'properties': props,
            'timestamp': item.get('timestamp'),
        }
        payload = json.dumps(body, ensure_ascii=False).encode('utf-8')
        url = host.rstrip('/') + '/i/v0/e/'
        request = urllib.request.Request(
            url, data=payload, method='POST',
            headers={'Content-Type': 'application/json', 'User-Agent': LIB_NAME + '/' + LIB_VERSION},
        )
        with urllib.request.urlopen(request, timeout=FLUSH_TIMEOUT_S) as response:
            response.read(1024)


# ----- process singleton used by the app -----
_client: Optional[Analytics] = None
_client_lock = threading.Lock()


def _default_data_dir() -> Path:
    try:
        from wan_core import DATA_DIR
        return Path(DATA_DIR)
    except Exception:
        return Path(os.environ.get('LOCALAPPDATA', str(Path.home() / '.local' / 'share'))) / 'PowerTokensWan'


def get_client() -> Optional[Analytics]:
    return _client


def reset_client() -> None:
    global _client
    with _client_lock:
        _client = None


def boot(data_dir: Optional[Path] = None, app_version: str = '',
         transport: Optional[Transport] = None, config: Optional[dict] = None,
         clock: Callable[[], float] = _now) -> Analytics:
    global _client
    with _client_lock:
        _client = Analytics(
            data_dir=Path(data_dir) if data_dir else _default_data_dir(),
            transport=transport,
            clock=clock,
            config=config,
            app_version=app_version,
        )
        _client.ensure_session()
        _client.capture_app_open()
        return _client


def _call(method: str, *args, **kwargs) -> Any:
    client = _client
    if client is None:
        return False
    try:
        return getattr(client, method)(*args, **kwargs)
    except Exception:
        return False


def consent_state() -> str:
    client = _client
    return client.consent_state() if client is not None else CONSENT_UNSET


def is_enabled() -> bool:
    client = _client
    return bool(client and client.is_enabled())


def set_consent(granted: bool) -> None:
    _call('set_consent', granted)


def notify_login_success(user_id: Any = None, key_applied: Optional[bool] = None) -> bool:
    return bool(_call('notify_login_success', user_id, key_applied=key_applied))


def notify_generation_start(generation_id: str, model: str = '', surface: str = 'generate',
                            requested_duration_s: Any = None) -> bool:
    return bool(_call('notify_generation_start', generation_id, model=model, surface=surface,
                      requested_duration_s=requested_duration_s))


def notify_generation_finish(generation_id: str, ok: bool, model: str = '', surface: str = 'generate',
                             duration_ms: Any = None, error_code: Any = None) -> bool:
    return bool(_call('notify_generation_finish', generation_id, ok, model=model, surface=surface,
                      duration_ms=duration_ms, error_code=error_code))


def notify_video_download(model: str = '', surface: str = 'generate') -> bool:
    return bool(_call('notify_video_download', model=model, surface=surface))


def identify_account(user_id: Any) -> Optional[str]:
    result = _call('identify_account', user_id)
    return result or None


def clear_account() -> None:
    _call('clear_account')

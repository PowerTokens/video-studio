"""Minimal PostHog capture client for Video Studio desktop.

This is not the official PostHog SDK: autocapture, session replay, heatmaps,
and person-profile enrichment are intentionally absent. Only allowlisted
critical events are queued and sent. Payloads are stripped to a property
allowlist; prompts, keys, tokens, emails, user assets, raw error text, OAuth
codes, and URL query strings are never accepted.

Network failures fail open (the UI is never blocked). The outbound queue is
bounded and only used when a destination exists. Analytics is default on. Account linkage is not used in this MVP:
distinct_id is a random install UUID. The ingest host may still see the client
IP; this client sets $geoip_disable and $process_person_profile=false, and
does not claim IP anonymity.
"""
from __future__ import annotations

import json
import re
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

import analytics_config

EVENTS = frozenset({
    'app_open',
    'login_success',
    'generation_start',
    'generation_success',
    'generation_failed',
    'video_download',
    'chat_submit',
    'chat_success',
    'chat_failed',
    'session_end',
    'login_start',
    'login_failed',
    'logout',
    'billing_click',
    'surface_open',
    'key_verified',
    'key_verify_failed',
    'language_set',
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
    'environment',
    'test_run_id',
    'chat_id',
    'active_ms',
    'source',
    'language',
    'input_mode',
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
    'NO_RESUME', 'QUERY_FORBIDDEN', 'FAILED', 'QUERY_UNAVAILABLE',
})

SAFE_SURFACES = frozenset({
    'generate', 'compare', 'batch', 'chat', 'tasks', 'account', 'history', 'keys',
})
SAFE_INPUT_MODE = frozenset({'t2v', 'i2v'})
SAFE_LANGUAGES = frozenset({'zh', 'en', 'es', 'pt', 'ja', 'ko'})
SAFE_BILLING_SOURCE = frozenset({'header', 'account', 'quota', 'credits_chip', 'chat'})
SESSION_MAX_MS = 12 * 60 * 60 * 1000

# Official ingest hosts from https://posthog.com/docs/api/capture
ALLOWED_INGEST_HOSTS = frozenset({
    'https://us.i.posthog.com',
    'https://eu.i.posthog.com',
})

# Model ids are catalog-like tokens only (no spaces, no URLs, no sentences).
MODEL_ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')

# Failures that close a generation. Timeout / paused / query-down can be resumed
# and must not consume the terminal slot, or later success would be dropped.
FINAL_FAILURE_CODES = frozenset({
    'AUTH', 'QUOTA', 'NOT_ALLOWED', 'PARAM', 'VALIDATION', 'REJECTED',
    'POOL_EXHAUSTED', 'SERVER_FAILED', 'FAILED', 'CANCELLED', 'STORAGE',
    'UNCERTAIN', 'UNKNOWN',
})

PLATFORM = 'desktop'
QUEUE_MAX = 50
SESSION_IDLE_S = 30 * 60
FLUSH_TIMEOUT_S = 8
LIB_NAME = 'video-studio-analytics'
LIB_VERSION = '1.0.0'
STATE_NAME = 'analytics.json'
CONSENT_UNSET = 'unset'
CONSENT_GRANTED = 'granted'
CONSENT_DENIED = 'denied'

Transport = Callable[[dict], None]


def _now() -> float:
    return time.time()


def sanitize_model(value: Any) -> Optional[str]:
    """Return a catalog-like model token, or None if it could carry content."""
    text = str(value or '').strip()
    if not text or not MODEL_ID_RE.match(text) or '@' in text:
        return None
    return text



def normalize_error_code(code: Any) -> str:
    text = str(code or '').strip().upper().replace('-', '_').replace(' ', '_')
    if text in SAFE_ERROR_CODES:
        return text
    aliases = {
        'SERVER_FAILED': 'SERVER_FAILED',
        'FAILED': 'SERVER_FAILED',
        'QUERY_AUTH': 'QUERY_AUTH',
        'QUERY_DOWN': 'QUERY_DOWN',
        'QUERY_UNAVAILABLE': 'QUERY_DOWN',
        'READY_NO_URL': 'READY_NO_URL',
        'UNCERTAIN_RELOAD': 'UNCERTAIN',
        'CANCELED': 'CANCELLED',
    }
    return aliases.get(text, 'UNKNOWN')


def is_final_generation_failure(code: Any) -> bool:
    """True when a generation cannot be resumed and should close the funnel."""
    return normalize_error_code(code) in FINAL_FAILURE_CODES | {'SERVER_FAILED'}



SAFE_ENVIRONMENTS = frozenset({'synthetic', 'test', 'production'})
TEST_RUN_ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')


def sanitize_environment(value: Any) -> Optional[str]:
    text = str(value or '').strip().lower()
    return text if text in SAFE_ENVIRONMENTS else None


def sanitize_test_run_id(value: Any) -> Optional[str]:
    text = str(value or '').strip()
    if not text or not TEST_RUN_ID_RE.match(text):
        return None
    return text


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
        elif key in ('duration_ms', 'active_ms'):
            try:
                value = int(value)
            except (TypeError, ValueError):
                continue
            if value < 0:
                continue
            if value > SESSION_MAX_MS:
                value = SESSION_MAX_MS
        elif key == 'language':
            value = str(value or '').strip().lower()
            if value not in SAFE_LANGUAGES:
                continue
        elif key == 'input_mode':
            value = str(value or '').strip().lower()
            if value not in SAFE_INPUT_MODE:
                continue
        elif key == 'source':
            value = str(value or '').strip().lower()
            if value not in SAFE_BILLING_SOURCE:
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
        elif key == 'environment':
            value = sanitize_environment(value)
            if not value:
                continue
        elif key == 'test_run_id':
            value = sanitize_test_run_id(value)
            if not value:
                continue
        elif key == 'model':
            value = sanitize_model(value)
            if not value:
                continue
        elif key in ('generation_id', 'chat_id', 'session_id', 'install_id', 'app_version', 'platform'):
            text = str(value or '').strip()
            if not text or len(text) > 80:
                continue
            value = text
        clean[key] = value
    clean['platform'] = PLATFORM
    return clean



def build_capture_payload(item: dict, api_key: str) -> dict:
    """Shape documented at https://posthog.com/docs/api/capture (single event).

    Required: api_key, event, distinct_id. Optional: properties, timestamp.
    Anonymous events must set $process_person_profile=false. $geoip_disable
    skips GeoIP enrichment. $ip is intentionally omitted: the official docs
    say PostHog still sees the request IP, and discarding stored IPs is a
    project setting, not a client field.
    """
    props = dict(item.get('properties') or {})
    props.pop('$ip', None)
    props['$lib'] = LIB_NAME
    props['$lib_version'] = LIB_VERSION
    props['$geoip_disable'] = True
    props['$process_person_profile'] = False
    return {
        'api_key': api_key,
        'event': item['event'],
        'distinct_id': item['distinct_id'],
        'properties': props,
        'timestamp': item.get('timestamp'),
    }


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
        self._state['consent'] = CONSENT_GRANTED
        self._save_state()
        self._flush_thread: Optional[threading.Thread] = None
        self._flush_lock = threading.Lock()

    # ----- persistence -----
    def _state_path(self) -> Path:
        return self.data_dir / STATE_NAME

    def _load_state(self) -> dict:
        try:
            data = json.loads(self._state_path().read_text(encoding='utf-8'))
            if not isinstance(data, dict):
                return {}
        except (OSError, ValueError):
            return {}
        consent = str(data.get('consent') or CONSENT_UNSET).strip()
        # Never keep a pre-consent queue or identifiers from an older build.
        data['account_distinct_id'] = ''
        if consent != CONSENT_GRANTED:
            data['queue'] = []
            if consent != CONSENT_DENIED:
                data.pop('install_id', None)
                data.pop('session_id', None)
                data['app_open_session'] = ''
                data['login_success_token'] = ''
        return data

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
        if self.consent_state() != CONSENT_GRANTED:
            return ''
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
        return CONSENT_GRANTED

    def is_enabled(self) -> bool:
        return self.has_destination()

    def set_consent(self, granted: bool) -> None:
        """Compatibility entry point; analytics is always enabled with a destination."""
        self.ensure_session()
        self.capture_app_open()
        self._schedule_flush()

    # ----- session -----
    def _cap_ms(self, ms: Any) -> int:
        try:
            value = int(ms)
        except (TypeError, ValueError):
            return 0
        if value < 0:
            return 0
        return SESSION_MAX_MS if value > SESSION_MAX_MS else value

    def _is_focused(self) -> bool:
        hook = getattr(self, '_focused', None)
        if hook is None:
            return True
        if callable(hook):
            try:
                return bool(hook())
            except Exception:
                return True
        return bool(hook)

    def _pause_active(self, at: float) -> None:
        started = float(self._state.get('active_started_at') or 0)
        if not started:
            return
        slice_ms = max(0, int((at - started) * 1000))
        self._state['active_ms'] = self._cap_ms(int(self._state.get('active_ms') or 0) + slice_ms)
        self._state['active_started_at'] = 0

    def _resume_active(self, at: float) -> None:
        if self.consent_state() != CONSENT_GRANTED:
            return
        session_id = str(self._state.get('session_id') or '')
        if not session_id or self._state.get('session_end_id') == session_id:
            return
        if self._state.get('active_started_at'):
            return
        self._state['active_started_at'] = at

    def _session_times(self, end_at: float) -> dict:
        self._pause_active(end_at)
        started = float(self._state.get('session_started_at') or end_at)
        duration = self._cap_ms(int(max(0, (end_at - started) * 1000)))
        active = self._cap_ms(int(self._state.get('active_ms') or 0))
        if active > duration:
            active = duration
        return {'duration_ms': duration, 'active_ms': active}

    def _enqueue_session_end(self, end_at: float, session_id: str = '') -> bool:
        session_id = str(session_id or self._state.get('session_id') or '').strip()
        if not session_id:
            return False
        if self._state.get('session_end_id') == session_id:
            return False
        if self.consent_state() != CONSENT_GRANTED:
            return False
        install_id = str(self._state.get('install_id') or '').strip()
        if not install_id:
            return False
        times = self._session_times(end_at)
        self._state['session_end_id'] = session_id
        props = {
            'duration_ms': times['duration_ms'],
            'active_ms': times['active_ms'],
            'session_id': session_id,
            'install_id': install_id,
            'platform': PLATFORM,
        }
        if self.app_version:
            props['app_version'] = self.app_version
        env_name = sanitize_environment(self._marker_environment())
        if env_name:
            props['environment'] = env_name
        run_id = sanitize_test_run_id(self._marker_test_run_id())
        if run_id:
            props['test_run_id'] = run_id
        body = {
            'event': 'session_end',
            'distinct_id': install_id,
            'properties': props,
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(end_at)),
        }
        if self.has_destination():
            self._enqueue(body)
        self._save_state()
        return True

    def _begin_new_session(self, now: float) -> str:
        session_id = uuid.uuid4().hex
        self._state['session_id'] = session_id
        self._state['session_started_at'] = now
        self._state['last_activity'] = now
        self._state['app_open_session'] = ''
        self._state['login_success_token'] = ''
        self._state['surfaces_seen'] = {}
        self._state['active_ms'] = 0
        self._state['active_started_at'] = now if self._is_focused() else 0
        self._state['session_end_id'] = ''
        self._save_state()
        return session_id

    def _is_idle(self, now: float) -> bool:
        last = float(self._state.get('last_activity') or 0)
        return bool(last and now - last > SESSION_IDLE_S)

    def recover_closed_session(self) -> bool:
        """End an unterminated previous session (crash / last close failed)."""
        with self._lock:
            if self.consent_state() != CONSENT_GRANTED:
                return False
            session_id = str(self._state.get('session_id') or '').strip()
            if not session_id or self._state.get('session_end_id') == session_id:
                return False
            end_at = float(self._state.get('last_activity') or self._state.get('session_started_at') or self.clock())
            return self._enqueue_session_end(end_at, session_id)

    def end_session(self) -> bool:
        """Final session_end for window close. Idempotent per session_id."""
        with self._lock:
            if self.consent_state() != CONSENT_GRANTED:
                return False
            session_id = str(self._state.get('session_id') or '').strip()
            if not session_id:
                return False
            ok = self._enqueue_session_end(self.clock(), session_id)
        if ok:
            try:
                self.flush()
            except Exception:
                pass
        return ok

    def handle_focus(self, focused: bool) -> None:
        """Pause or resume active time. After 30 minutes idle, end and start a new session."""
        if self.consent_state() != CONSENT_GRANTED:
            return
        with self._lock:
            now = self.clock()
            session_id = str(self._state.get('session_id') or '').strip()
            if not focused:
                self._pause_active(now)
                if session_id:
                    self._state['last_activity'] = now
                    self._save_state()
                return
            if session_id and self._state.get('session_end_id') != session_id and self._is_idle(now):
                self._enqueue_session_end(float(self._state.get('last_activity') or now), session_id)
                self._begin_new_session(now)
                should_open = True
            else:
                should_open = False
                if session_id and self._state.get('session_end_id') != session_id:
                    self._resume_active(now)
                    self._state['last_activity'] = now
                    self._save_state()
        if should_open:
            self.capture_app_open()
            if self.has_destination():
                self._schedule_flush()

    def set_focused_hook(self, hook: Any) -> None:
        self._focused = hook

    def ensure_session(self) -> str:
        """Return the current session id. A desktop session is one process, or a
        30-minute idle gap if the process is left running. Tab switches do not
        fire app_open; they may emit surface_open once per tab per session.
        Idle after a focus loss ends the previous session first.
        """
        with self._lock:
            if self.consent_state() != CONSENT_GRANTED:
                return ''
            now = self.clock()
            session_id = str(self._state.get('session_id') or '').strip()
            idle = self._is_idle(now)
            if session_id and idle and self._state.get('session_end_id') != session_id:
                self._enqueue_session_end(float(self._state.get('last_activity') or now), session_id)
                if self.has_destination():
                    # Schedule after releasing? RLock allows it; keep it lazy.
                    pass
            if (not session_id or idle
                    or self._state.get('session_end_id') == session_id):
                session_id = self._begin_new_session(now)
                if self.has_destination():
                    self._schedule_flush()
                return session_id
            self._state['last_activity'] = now
            if self._is_focused():
                self._resume_active(now)
            self._save_state()
            return session_id

    def session_id(self) -> str:
        return self.ensure_session()

    # ----- identity (anonymous install UUID only; no account linkage) -----
    def identify_account(self, user_id: Any = None) -> Optional[str]:
        """MVP: no account linkage. Kept as a no-op so older call sites stay safe."""
        return None


    def clear_account(self) -> None:
        return

    def distinct_id(self) -> str:
        with self._lock:
            return self._ensure_install_id()

    # ----- config / transport -----
    def _resolved_config(self) -> dict:
        if self._config.get('project_key') or self._config.get('host'):
            key = str(self._config.get('project_key') or '').strip()
            host = str(self._config.get('host') or '').strip().rstrip('/')
        else:
            try:
                loaded = analytics_config.load()
            except Exception:
                loaded = {'project_key': '', 'host': 'https://us.i.posthog.com'}
            key = str(loaded.get('project_key') or '').strip()
            host = str(loaded.get('host') or '').strip().rstrip('/')
        try:
            key = analytics_config.public_project_key(key)
            host = analytics_config.official_host(host)
        except Exception:
            if key and not str(key).startswith('phc_'):
                key = ''
        if host and host not in ALLOWED_INGEST_HOSTS:
            host = ''
        if not host:
            host = 'https://us.i.posthog.com'
        return {
            'project_key': key,
            'host': host,
            'environment': self._config.get('environment') or '',
            'test_run_id': self._config.get('test_run_id') or '',
        }

    def _marker_environment(self) -> str:
        explicit = sanitize_environment(self._config.get('environment'))
        if explicit:
            return explicit
        try:
            loaded = analytics_config.load()
            return sanitize_environment(loaded.get('environment')) or ''
        except Exception:
            return ''

    def _marker_test_run_id(self) -> str:
        explicit = sanitize_test_run_id(self._config.get('test_run_id'))
        if explicit:
            return explicit
        try:
            loaded = analytics_config.load()
            return sanitize_test_run_id(loaded.get('test_run_id')) or ''
        except Exception:
            return ''

    def has_destination(self) -> bool:
        if self._transport is not None:
            return True
        cfg = self._resolved_config()
        return bool(cfg.get('project_key') and cfg.get('host') in ALLOWED_INGEST_HOSTS)

    def project_key_configured(self) -> bool:
        return bool(self._resolved_config().get('project_key')) or self._transport is not None

    # ----- capture -----
    def track(self, event: str, props: Optional[dict] = None) -> bool:
        """Queue an allowlisted event. Returns True if it was accepted.

        Events are only
        queued when a destination (project key or test transport) exists.
        """
        try:
            return self._track(event, props)
        except Exception:
            return False

    def _track(self, event: str, props: Optional[dict]) -> bool:
        clean = sanitize_properties(event, props)
        if clean is None:
            return False
        if self.consent_state() != CONSENT_GRANTED:
            return False
        if event == 'session_end':
            with self._lock:
                sid = str((clean or {}).get('session_id') or self._state.get('session_id') or '')
                return self._enqueue_session_end(self.clock(), sid)
        with self._lock:
            session_id = self.ensure_session()
            install_id = self._ensure_install_id()
            if not session_id or not install_id:
                return False
            if (event not in ('app_open', 'session_end')
                    and not getattr(self, '_opening_app_open', False)
                    and self._state.get('app_open_session') != session_id):
                # New session created by idle rotation: record app_open first.
                self._opening_app_open = True
                try:
                    self.capture_app_open()
                finally:
                    self._opening_app_open = False
            if not self._accept_unique(event, clean):
                return False
            clean['session_id'] = session_id
            clean['install_id'] = install_id
            if self.app_version:
                clean['app_version'] = self.app_version
            env_name = sanitize_environment(clean.get('environment') or self._marker_environment())
            if env_name:
                clean['environment'] = env_name
            run_id = sanitize_test_run_id(clean.get('test_run_id') or self._marker_test_run_id())
            if run_id:
                clean['test_run_id'] = run_id
            body = {
                'event': event,
                'distinct_id': install_id,
                'properties': clean,
                'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(self.clock())),
            }
            if not self.has_destination():
                # Consented but unconfigured: accept in memory for tests only if
                # a transport exists. Otherwise do not persist a send backlog.
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
        if event in ('chat_submit', 'chat_success', 'chat_failed'):
            chat_id = str(clean.get('chat_id') or '').strip()
            if not chat_id:
                return False
            seen = dict(self._state.get('chat_seen') or {})
            already = set(seen.get(chat_id) or [])
            if event == 'chat_submit':
                if 'submit' in already:
                    return False
                already.add('submit')
            else:
                if 'terminal' in already:
                    return False
                already.add('terminal')
            seen[chat_id] = sorted(already)
            if len(seen) > 200:
                keep_ids = list(seen.keys())[-200:]
                seen = {k: seen[k] for k in keep_ids}
            self._state['chat_seen'] = seen
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
            token = str(self._state.get('session_id') or '')
            if self._state.get('login_success_token') == token:
                return False
            self._state['login_success_token'] = token
            return True
        if event == 'session_end':
            sid = str(clean.get('session_id') or self._state.get('session_id') or '').strip()
            if not sid:
                return False
            if self._state.get('session_end_id') == sid:
                return False
            self._state['session_end_id'] = sid
            return True
        if event == 'surface_open':
            surface = str(clean.get('surface') or '').strip().lower()
            if surface not in SAFE_SURFACES:
                return False
            session_id = str(self._state.get('session_id') or '')
            bag = dict(self._state.get('surfaces_seen') or {})
            seen = list(bag.get(session_id) or [])
            if surface in seen:
                return False
            seen.append(surface)
            bag[session_id] = seen
            self._state['surfaces_seen'] = bag
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

    def notify_login_start(self) -> bool:
        return self.track('login_start', {})

    def notify_login_failed(self, error_code: Any = None) -> bool:
        return self.track('login_failed', {'error_code': normalize_error_code(error_code)})

    def notify_logout(self) -> bool:
        return self.track('logout', {})

    def notify_login_success(self, user_id: Any = None, key_applied: Optional[bool] = None) -> bool:
        # user_id is ignored in this MVP (no account linkage).
        props: dict[str, Any] = {}
        if key_applied is not None:
            props['key_applied'] = bool(key_applied)
        return self.track('login_success', props)

    def notify_billing_click(self, source: str = 'credits_chip') -> bool:
        return self.track('billing_click', {'source': source})

    def notify_surface_open(self, surface: str) -> bool:
        return self.track('surface_open', {'surface': surface})

    def notify_key_verified(self) -> bool:
        return self.track('key_verified', {})

    def notify_key_verify_failed(self, error_code: Any = None) -> bool:
        return self.track('key_verify_failed', {'error_code': normalize_error_code(error_code)})

    def notify_language_set(self, language: str) -> bool:
        return self.track('language_set', {'language': language})

    def notify_generation_start(self, generation_id: str, model: str = '',
                                surface: str = 'generate',
                                requested_duration_s: Any = None,
                                input_mode: Any = None) -> bool:
        props: dict[str, Any] = {
            'generation_id': generation_id,
            'surface': surface or 'generate',
        }
        if model:
            props['model'] = model
        if requested_duration_s is not None:
            props['requested_duration_s'] = requested_duration_s
        if input_mode:
            props['input_mode'] = input_mode
        return self.track('generation_start', props)

    def notify_generation_finish(self, generation_id: str, ok: bool, model: str = '',
                                 surface: str = 'generate', duration_ms: Any = None,
                                 error_code: Any = None, input_mode: Any = None) -> bool:
        props: dict[str, Any] = {
            'generation_id': generation_id,
            'surface': surface or 'generate',
        }
        if model:
            props['model'] = model
        if duration_ms is not None:
            props['duration_ms'] = duration_ms
        if input_mode:
            props['input_mode'] = input_mode
        event = 'generation_success' if ok else 'generation_failed'
        if not ok:
            props['error_code'] = normalize_error_code(error_code)
        return self.track(event, props)

    def notify_video_download(self, model: str = '', surface: str = 'generate') -> bool:
        props: dict[str, Any] = {'surface': surface or 'generate'}
        if model:
            props['model'] = model
        return self.track('video_download', props)

    def notify_chat_submit(self, chat_id: str, model: str = '') -> bool:
        """User confirmed Send. Does not mean a server reply completed."""
        props: dict[str, Any] = {'chat_id': chat_id, 'surface': 'chat'}
        if model:
            props['model'] = model
        return self.track('chat_submit', props)

    def notify_chat_finish(self, chat_id: str, ok: bool, model: str = '',
                           error_code: Any = None) -> bool:
        """Server/model reply completed or failed. Never pass message text."""
        props: dict[str, Any] = {'chat_id': chat_id, 'surface': 'chat'}
        if model:
            props['model'] = model
        event = 'chat_success' if ok else 'chat_failed'
        if not ok:
            props['error_code'] = normalize_error_code(error_code)
        return self.track(event, props)

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
        host = (config.get('host') or '').rstrip('/')
        if not key or host not in ALLOWED_INGEST_HOSTS:
            raise RuntimeError('no destination')
        body = build_capture_payload(item, key)
        payload = json.dumps(body, ensure_ascii=False).encode('utf-8')
        url = host + '/i/v0/e/'
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
        if _client.is_enabled():
            _client.recover_closed_session()
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


def notify_login_start() -> bool:
    return bool(_call('notify_login_start'))


def notify_login_failed(error_code: Any = None) -> bool:
    return bool(_call('notify_login_failed', error_code=error_code))


def notify_logout() -> bool:
    return bool(_call('notify_logout'))


def notify_login_success(user_id: Any = None, key_applied: Optional[bool] = None) -> bool:
    return bool(_call('notify_login_success', user_id, key_applied=key_applied))


def notify_billing_click(source: str = 'credits_chip') -> bool:
    return bool(_call('notify_billing_click', source))


def notify_surface_open(surface: str) -> bool:
    return bool(_call('notify_surface_open', surface))


def notify_key_verified() -> bool:
    return bool(_call('notify_key_verified'))


def notify_key_verify_failed(error_code: Any = None) -> bool:
    return bool(_call('notify_key_verify_failed', error_code=error_code))


def notify_language_set(language: str) -> bool:
    return bool(_call('notify_language_set', language))


def end_session() -> bool:
    return bool(_call('end_session'))


def handle_focus(focused: bool) -> None:
    _call('handle_focus', focused)


def flush() -> None:
    _call('flush')


def notify_generation_start(generation_id: str, model: str = '', surface: str = 'generate',
                            requested_duration_s: Any = None, input_mode: Any = None) -> bool:
    return bool(_call('notify_generation_start', generation_id, model=model, surface=surface,
                      requested_duration_s=requested_duration_s, input_mode=input_mode))


def notify_generation_finish(generation_id: str, ok: bool, model: str = '', surface: str = 'generate',
                             duration_ms: Any = None, error_code: Any = None,
                             input_mode: Any = None) -> bool:
    return bool(_call('notify_generation_finish', generation_id, ok, model=model, surface=surface,
                      duration_ms=duration_ms, error_code=error_code, input_mode=input_mode))


def notify_video_download(model: str = '', surface: str = 'generate') -> bool:
    return bool(_call('notify_video_download', model=model, surface=surface))


def notify_chat_submit(chat_id: str, model: str = '') -> bool:
    return bool(_call('notify_chat_submit', chat_id, model=model))


def notify_chat_finish(chat_id: str, ok: bool, model: str = '', error_code: Any = None) -> bool:
    return bool(_call('notify_chat_finish', chat_id, ok, model=model, error_code=error_code))


def identify_account(user_id: Any) -> Optional[str]:
    result = _call('identify_account', user_id)
    return result or None


def clear_account() -> None:
    _call('clear_account')

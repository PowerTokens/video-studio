"""PowerTokens OAuth 2.0 Authorization Code + PKCE (S256) for Video Studio desktop.

Opens the system browser, listens on http://127.0.0.1:<port>/callback, exchanges
the authorization code for an access_token, and fetches userinfo (credits + default
API key). Matches the web client_id powerbaze_desktop_v1.

Access tokens last ~1800s with no refresh_token. Session persistence stores the
token and display profile only — never the API key (that goes into the existing
key pool / DPAPI path in the app).
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any, Callable, Optional

from wan_core import DATA_DIR

CLIENT_ID = 'powerbaze_desktop_v1'
AUTHORIZE_URL = 'https://www.powertokens.ai/oauth/authorize'
TOKEN_URL = 'https://powertokens.ai/api/oauth/token'
USERINFO_URL = 'https://powertokens.ai/api/oauth/userinfo'
REVOKE_URL = 'https://powertokens.ai/api/oauth/revoke'
SCOPE = 'read'
PREFERRED_PORT = 53682
CALLBACK_PATH = '/callback'
DEFAULT_TIMEOUT = 300
SESSION_FILE = 'oauth_session.json'
USER_AGENT = 'PowerTokensVideoStudio-Desktop/oauth'


class OAuthError(Exception):
    """Raised for user-visible OAuth failures. ``code`` is a stable machine key."""

    def __init__(self, code: str, message: str = ''):
        self.code = code
        super().__init__(message or code)


def make_pkce() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for S256."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b'=').decode('ascii')
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode('ascii')).digest()
    ).rstrip(b'=').decode('ascii')
    return verifier, challenge


def make_state() -> str:
    return secrets.token_hex(16)


def build_authorize_url(redirect_uri: str, state: str, code_challenge: str,
                        client_id: str = CLIENT_ID, scope: str = SCOPE,
                        utm: Optional[dict] = None) -> str:
    """Authorize URL. Always appends last-touch UTM (required for backend attribution)."""
    params = {
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': scope,
        'state': state,
        'code_challenge': code_challenge,
        'code_challenge_method': 'S256',
    }
    # Required: last-touch UTM so backend can attribute desktop OAuth.
    try:
        import utm_attr
        utm_attr.capture_from_env()
        track = utm if utm is not None else utm_attr.params_for(utm_attr.CONTENT_OAUTH)
    except Exception:
        track = {
            'utm_source': 'videostudio',
            'utm_medium': 'app',
            'utm_campaign': 'video-studio',
            'utm_content': 'app.oauth_authorize',
        }
    for k, v in track.items():
        if v is not None and str(v) != '':
            params[k] = str(v)
    return AUTHORIZE_URL + '?' + urllib.parse.urlencode(params)


def _html_page(ok: bool) -> bytes:
    if ok:
        title = 'Signed in to PowerTokens'
        body = ('Signed in to PowerTokens. You can close this tab and return to '
                'Video Studio.<br>已登录 PowerTokens，可关闭此页并回到 Video Studio。')
    else:
        title = 'Sign-in failed'
        body = ('Sign-in failed. Return to Video Studio and try again.<br>'
                '登录失败，请回到 Video Studio 重试。')
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8"><title>%s</title></head>'
        '<body style="font-family:system-ui,sans-serif;padding:2rem;line-height:1.5">'
        '<h3>%s</h3><p>%s</p></body></html>'
        % (title, title, body)
    ).encode('utf-8')


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Filled by ``start_loopback_server`` before serve_forever."""

    expected_state: str = ''
    result: dict = {}
    done: threading.Event = threading.Event()

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path != CALLBACK_PATH:
            self.send_response(404)
            self.end_headers()
            return
        query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
        type(self).result = query
        ok = query.get('state') == self.expected_state and bool(query.get('code')) and not query.get('error')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(_html_page(ok))
        type(self).done.set()

    def log_message(self, *_args):
        return


def start_loopback_server(preferred_port: int = PREFERRED_PORT,
                          expected_state: str = '') -> tuple[http.server.HTTPServer, str]:
    """Bind 127.0.0.1 at preferred_port, or port 0 if that bind fails.

    Returns (server, redirect_uri). Caller must shut the server down.
    """
    handler = type(
        'BoundCallbackHandler',
        (_CallbackHandler,),
        {
            'expected_state': expected_state,
            'result': {},
            'done': threading.Event(),
        },
    )
    try:
        server = http.server.HTTPServer(('127.0.0.1', preferred_port), handler)
    except OSError:
        server = http.server.HTTPServer(('127.0.0.1', 0), handler)
    redirect_uri = 'http://127.0.0.1:%d%s' % (server.server_port, CALLBACK_PATH)
    return server, redirect_uri


def wait_for_callback(server: http.server.HTTPServer, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Serve until /callback is hit or timeout. Always shuts the server down."""
    handler_cls = server.RequestHandlerClass
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        finished = handler_cls.done.wait(timeout)
        if not finished:
            raise OAuthError('timeout', 'Timed out waiting for the browser sign-in.')
        return dict(handler_cls.result or {})
    finally:
        try:
            server.shutdown()
        except Exception:
            pass
        try:
            server.server_close()
        except Exception:
            pass
        thread.join(timeout=2)


def _form_post(url: str, fields: dict, headers: Optional[dict] = None) -> dict:
    body = urllib.parse.urlencode(fields).encode('utf-8')
    req_headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Accept': 'application/json',
        'User-Agent': USER_AGENT,
    }
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=body, method='POST', headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode('utf-8')
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = ''
        try:
            detail = exc.read().decode('utf-8', errors='replace')
            parsed = json.loads(detail)
            err = parsed.get('error') or 'http_error'
            desc = parsed.get('error_description') or detail
            raise OAuthError(str(err), str(desc)) from exc
        except OAuthError:
            raise
        except Exception:
            raise OAuthError('http_error', 'HTTP %s: %s' % (exc.code, detail or exc.reason)) from exc
    except urllib.error.URLError as exc:
        raise OAuthError('network', str(exc.reason or exc)) from exc


def _json_get(url: str, access_token: str) -> dict:
    req = urllib.request.Request(url, method='GET', headers={
        'Authorization': 'Bearer ' + access_token,
        'Accept': 'application/json',
        'User-Agent': USER_AGENT,
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode('utf-8')
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = ''
        try:
            detail = exc.read().decode('utf-8', errors='replace')
            parsed = json.loads(detail)
            err = parsed.get('error') or 'http_error'
            desc = parsed.get('error_description') or detail
            raise OAuthError(str(err), str(desc)) from exc
        except OAuthError:
            raise
        except Exception:
            raise OAuthError('http_error', 'HTTP %s: %s' % (exc.code, detail or exc.reason)) from exc
    except urllib.error.URLError as exc:
        raise OAuthError('network', str(exc.reason or exc)) from exc


def exchange_token(code: str, code_verifier: str, redirect_uri: str,
                   client_id: str = CLIENT_ID) -> dict:
    data = _form_post(TOKEN_URL, {
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': redirect_uri,
        'code_verifier': code_verifier,
        'client_id': client_id,
    })
    if not data.get('access_token'):
        raise OAuthError(
            data.get('error') or 'token_exchange_failed',
            data.get('error_description') or 'Token endpoint returned no access_token.',
        )
    return data


def fetch_userinfo(access_token: str) -> dict:
    data = _json_get(USERINFO_URL, access_token)
    if data.get('error') and not data.get('user_id') and not data.get('sub'):
        raise OAuthError(str(data.get('error')), str(data.get('error_description') or ''))
    return data


def revoke_token(access_token: str) -> bool:
    if not access_token:
        return True
    try:
        _form_post(REVOKE_URL, {'token': access_token})
        return True
    except OAuthError:
        return False


def normalize_api_key(raw: Any) -> str:
    """Strip accidental Bearer prefix; return empty string if missing."""
    if not isinstance(raw, str):
        return ''
    text = raw.strip()
    if text.lower().startswith('bearer '):
        text = text[7:].strip()
    return text


def login(open_browser: bool = True, timeout: float = DEFAULT_TIMEOUT,
          preferred_port: int = PREFERRED_PORT,
          browser_opener: Optional[Callable[[str], bool]] = None) -> dict:
    """Run the full interactive OAuth flow.

    Returns a dict::
        {
          'access_token': str,
          'token_type': str,
          'expires_in': int,
          'expires_at': float,   # unix seconds
          'scope': str,
          'userinfo': dict,      # may include plaintext 'key'
          'redirect_uri': str,
          'key': str,            # normalized default API key (may be '')
        }

    Does not persist anything; callers decide how to save the session and key.
    """
    verifier, challenge = make_pkce()
    state = make_state()
    server, redirect_uri = start_loopback_server(preferred_port, expected_state=state)
    url = build_authorize_url(redirect_uri, state, challenge)
    opener = browser_opener or webbrowser.open
    if open_browser:
        try:
            opener(url)
        except Exception:
            pass
    query = wait_for_callback(server, timeout=timeout)
    if query.get('error'):
        raise OAuthError(
            str(query.get('error')),
            str(query.get('error_description') or query.get('error')),
        )
    if query.get('state') != state:
        raise OAuthError('state_mismatch', 'State mismatch; sign-in aborted.')
    code = query.get('code')
    if not code:
        raise OAuthError('missing_code', 'Sign-in did not return an authorization code.')
    token = exchange_token(code, verifier, redirect_uri)
    userinfo = fetch_userinfo(token['access_token'])
    expires_in = int(token.get('expires_in') or 1800)
    expires_at = time.time() + max(60, expires_in - 30)
    key = normalize_api_key(userinfo.get('key'))
    return {
        'access_token': token['access_token'],
        'token_type': token.get('token_type') or 'Bearer',
        'expires_in': expires_in,
        'expires_at': expires_at,
        'scope': token.get('scope') or '',
        'userinfo': userinfo,
        'redirect_uri': redirect_uri,
        'key': key,
    }


def session_path(data_dir: Optional[Path] = None) -> Path:
    return Path(data_dir or DATA_DIR) / SESSION_FILE


def profile_from_userinfo(userinfo: dict) -> dict:
    """Display fields only — never include the API key."""
    credits = userinfo.get('credits')
    if credits is not None and not isinstance(credits, (int, float)):
        try:
            credits = int(credits)
        except (TypeError, ValueError):
            credits = None
    return {
        'sub': userinfo.get('sub') or '',
        'user_id': userinfo.get('user_id') or '',
        'email': userinfo.get('email') or '',
        'name': userinfo.get('name') or '',
        'avatar': userinfo.get('avatar') or '',
        'source': userinfo.get('source') or '',
        'credits': credits,
        'updated_at': time.time(),
    }


def save_session(token_result: dict, data_dir: Optional[Path] = None) -> Path:
    """Persist token + profile (no API key) under DATA_DIR."""
    path = session_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    userinfo = token_result.get('userinfo') or {}
    payload = {
        'access_token': token_result.get('access_token') or '',
        'token_type': token_result.get('token_type') or 'Bearer',
        'expires_at': token_result.get('expires_at'),
        'scope': token_result.get('scope') or '',
        'profile': profile_from_userinfo(userinfo),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def load_session(data_dir: Optional[Path] = None) -> Optional[dict]:
    """Return session dict if present and not expired; else None (and clear if expired)."""
    path = session_path(data_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get('access_token'):
        return None
    expires_at = data.get('expires_at')
    if expires_at is not None:
        try:
            if time.time() > float(expires_at):
                clear_session(data_dir)
                return None
        except (TypeError, ValueError):
            clear_session(data_dir)
            return None
    profile = data.get('profile') if isinstance(data.get('profile'), dict) else {}
    # Defensive: never keep a leaked key in the session file.
    profile.pop('key', None)
    data['profile'] = profile
    return data


def clear_session(data_dir: Optional[Path] = None) -> None:
    path = session_path(data_dir)
    try:
        path.unlink(missing_ok=True)
    except TypeError:
        # Python <3.8 compatibility (desktop targets 3.9+, but be safe)
        if path.exists():
            path.unlink()
    except OSError:
        pass


def logout(data_dir: Optional[Path] = None, revoke: bool = True) -> None:
    """Clear local session; optionally best-effort revoke the access_token."""
    session = None
    path = session_path(data_dir)
    if path.is_file():
        try:
            session = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            session = None
    token = (session or {}).get('access_token') or ''
    clear_session(data_dir)
    if revoke and token:
        revoke_token(token)

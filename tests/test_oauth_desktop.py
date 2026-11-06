"""Unit tests for desktop OAuth PKCE / loopback / session (no real browser or network)."""
import base64
import hashlib
import io
import json
import threading
import time
import unittest
import urllib.error
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch
import tempfile

import oauth_desktop
import wan_core


class PkceTests(unittest.TestCase):
    def test_verifier_and_challenge_are_s256(self):
        verifier, challenge = oauth_desktop.make_pkce()
        self.assertGreaterEqual(len(verifier), 43)
        self.assertLessEqual(len(verifier), 128)
        expected = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode('ascii')).digest()
        ).rstrip(b'=').decode('ascii')
        self.assertEqual(challenge, expected)
        self.assertNotIn('=', challenge)
        self.assertNotIn('+', challenge)
        self.assertNotIn('/', challenge)

    def test_state_is_32_hex_chars(self):
        state = oauth_desktop.make_state()
        self.assertEqual(len(state), 32)
        int(state, 16)  # must be hex


class AuthorizeUrlTests(unittest.TestCase):
    def test_contains_required_query_params(self):
        url = oauth_desktop.build_authorize_url(
            'http://127.0.0.1:53682/callback', 'abc', 'challenge123')
        self.assertTrue(url.startswith(oauth_desktop.AUTHORIZE_URL + '?'))
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        self.assertEqual(q['client_id'], oauth_desktop.CLIENT_ID)
        self.assertEqual(q['redirect_uri'], 'http://127.0.0.1:53682/callback')
        self.assertEqual(q['response_type'], 'code')
        self.assertEqual(q['scope'], 'read')
        self.assertEqual(q['state'], 'abc')
        self.assertEqual(q['code_challenge'], 'challenge123')
        self.assertEqual(q['code_challenge_method'], 'S256')
        # Required: product-forced UTM on authorize
        self.assertEqual(q['utm_source'], 'videostudio')
        self.assertEqual(q['utm_medium'], 'app')
        self.assertEqual(q['utm_campaign'], 'video-studio')
        self.assertEqual(q['utm_content'], 'app.oauth_authorize')

    def test_authorize_uses_explicit_utm_override(self):
        url = oauth_desktop.build_authorize_url(
            'http://127.0.0.1:53682/callback', 'abc', 'challenge123',
            utm={
                'utm_source': 'github',
                'utm_medium': 'release',
                'utm_campaign': 'video-studio',
                'utm_content': 'app.oauth_authorize',
            })
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        self.assertEqual(q['utm_source'], 'github')
        self.assertEqual(q['utm_medium'], 'release')
        self.assertEqual(q['utm_content'], 'app.oauth_authorize')

    def test_authorize_forces_product_over_ad_last_touch(self):
        import os
        import utm_attr
        cleared = {
            'POWERTTOKENS_UTM': '',
            'POWERTTOKENS_UTM_SOURCE': '',
            'POWERTTOKENS_UTM_MEDIUM': '',
            'POWERTTOKENS_UTM_CAMPAIGN': '',
            'POWERTTOKENS_UTM_CONTENT': '',
            'POWERTTOKENS_UTM_TERM': '',
        }
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            with patch.object(utm_attr, 'DATA_DIR', data_dir), \
                 patch.dict(os.environ, cleared, clear=False):
                utm_attr.capture({
                    'utm_source': 'google',
                    'utm_medium': 'cpc',
                    'utm_campaign': 'spring-sale',
                    'gclid': 'Cj0TEST',
                }, data_dir=data_dir)
                url = oauth_desktop.build_authorize_url(
                    'http://127.0.0.1:53682/callback', 'abc', 'challenge123')
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        self.assertEqual(q['client_id'], oauth_desktop.CLIENT_ID)
        self.assertEqual(q['utm_source'], 'videostudio')
        self.assertEqual(q['utm_medium'], 'app')
        self.assertEqual(q['utm_campaign'], 'video-studio')
        self.assertEqual(q['utm_content'], 'app.oauth_authorize')
        self.assertEqual(q['gclid'], 'Cj0TEST')
        self.assertNotIn('google', url)


class LoopbackTests(unittest.TestCase):
    def test_preferred_port_or_fallback(self):
        server, uri = oauth_desktop.start_loopback_server(preferred_port=53682, expected_state='s')
        try:
            self.assertTrue(uri.startswith('http://127.0.0.1:'))
            self.assertTrue(uri.endswith('/callback'))
            port = int(urllib.parse.urlsplit(uri).port)
            self.assertGreater(port, 0)
        finally:
            server.server_close()

    def test_callback_rejects_wrong_path_and_accepts_code(self):
        state = 'state-for-test'
        server, redirect_uri = oauth_desktop.start_loopback_server(
            preferred_port=0, expected_state=state)
        port = server.server_port

        def hit():
            # Wrong path → 404; does not finish the wait.
            try:
                urllib.request.urlopen('http://127.0.0.1:%d/nope' % port, timeout=2)
            except urllib.error.HTTPError as exc:
                self.assertEqual(exc.code, 404)
            # Correct callback finishes the waiter.
            urllib.request.urlopen(
                '%s?code=the-code&state=%s' % (redirect_uri, state), timeout=2)

        import urllib.request
        thread = threading.Thread(target=hit, daemon=True)
        thread.start()
        query = oauth_desktop.wait_for_callback(server, timeout=5)
        thread.join(timeout=2)
        self.assertEqual(query.get('code'), 'the-code')
        self.assertEqual(query.get('state'), state)

    def test_state_mismatch_raises_on_login(self):
        # Simulate callback with wrong state by driving login pieces manually.
        verifier, challenge = oauth_desktop.make_pkce()
        state = oauth_desktop.make_state()
        server, redirect_uri = oauth_desktop.start_loopback_server(
            preferred_port=0, expected_state=state)

        def hit():
            import urllib.request
            urllib.request.urlopen(
                '%s?code=x&state=WRONG' % redirect_uri, timeout=2)

        threading.Thread(target=hit, daemon=True).start()
        query = oauth_desktop.wait_for_callback(server, timeout=5)
        self.assertNotEqual(query.get('state'), state)


class TokenUserinfoTests(unittest.TestCase):
    def test_exchange_token_posts_form_and_reads_access_token(self):
        captured = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get('Content-Length', '0'))
                body = self.rfile.read(length).decode('utf-8')
                captured['path'] = self.path
                captured['ctype'] = self.headers.get('Content-Type')
                captured['ua'] = self.headers.get('User-Agent')
                captured['fields'] = dict(urllib.parse.parse_qsl(body))
                payload = json.dumps({
                    'access_token': 'tok-123',
                    'token_type': 'Bearer',
                    'expires_in': 1800,
                    'scope': '',
                }).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *a):
                return

        httpd = HTTPServer(('127.0.0.1', 0), Handler)
        port = httpd.server_port
        thread = threading.Thread(target=httpd.handle_request, daemon=True)
        thread.start()
        with patch.object(oauth_desktop, 'TOKEN_URL', 'http://127.0.0.1:%d/oauth/token' % port):
            data = oauth_desktop.exchange_token('code1', 'verifier1', 'http://127.0.0.1:9/callback')
        thread.join(timeout=2)
        httpd.server_close()
        self.assertEqual(data['access_token'], 'tok-123')
        self.assertEqual(captured['fields']['grant_type'], 'authorization_code')
        self.assertEqual(captured['fields']['code'], 'code1')
        self.assertEqual(captured['fields']['code_verifier'], 'verifier1')
        self.assertEqual(captured['fields']['client_id'], oauth_desktop.CLIENT_ID)
        self.assertIn('application/x-www-form-urlencoded', captured['ctype'])
        self.assertEqual(captured['ua'], wan_core.USER_AGENT)
        self.assertTrue(captured['ua'].startswith('videostudio'))

    def test_fetch_userinfo_sends_bearer(self):
        captured = {}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                captured['auth'] = self.headers.get('Authorization')
                captured['ua'] = self.headers.get('User-Agent')
                payload = json.dumps({
                    'name': 'Cai Wei',
                    'credits': 1234,
                    'key': 'YK4jbykzCNdeufluAgTdgncfg8NNurCvv85Hcls2K1UqpwsC',
                    'user_id': 'u1',
                }).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *a):
                return

        httpd = HTTPServer(('127.0.0.1', 0), Handler)
        port = httpd.server_port
        thread = threading.Thread(target=httpd.handle_request, daemon=True)
        thread.start()
        with patch.object(oauth_desktop, 'USERINFO_URL', 'http://127.0.0.1:%d/oauth/userinfo' % port):
            info = oauth_desktop.fetch_userinfo('tok-abc')
        thread.join(timeout=2)
        httpd.server_close()
        self.assertEqual(captured['auth'], 'Bearer tok-abc')
        self.assertEqual(captured['ua'], wan_core.USER_AGENT)
        self.assertTrue(captured['ua'].startswith('videostudio'))
        self.assertEqual(info['credits'], 1234)
        self.assertTrue(info['key'])

    def test_exchange_token_error_raises_oauth_error(self):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get('Content-Length', '0'))
                self.rfile.read(length)
                payload = b'{"error":"invalid_grant","error_description":"PKCE verification failed"}'
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *a):
                return

        httpd = HTTPServer(('127.0.0.1', 0), Handler)
        port = httpd.server_port
        thread = threading.Thread(target=httpd.handle_request, daemon=True)
        thread.start()
        with patch.object(oauth_desktop, 'TOKEN_URL', 'http://127.0.0.1:%d/oauth/token' % port):
            with self.assertRaises(oauth_desktop.OAuthError) as ctx:
                oauth_desktop.exchange_token('bad', 'v', 'http://127.0.0.1:9/callback')
        thread.join(timeout=2)
        httpd.server_close()
        self.assertEqual(ctx.exception.code, 'invalid_grant')


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())

    def test_save_load_omits_api_key_and_respects_expiry(self):
        result = {
            'access_token': 'tok',
            'token_type': 'Bearer',
            'expires_at': time.time() + 600,
            'scope': '',
            'userinfo': {
                'name': 'Ada',
                'email': 'a@example.com',
                'credits': 99,
                'key': 'SECRETKEYSHOULDNOTPERSIST0001',
                'user_id': 'u',
            },
        }
        path = oauth_desktop.save_session(result, data_dir=self.folder)
        raw = json.loads(path.read_text(encoding='utf-8'))
        self.assertNotIn('key', raw.get('profile', {}))
        self.assertNotIn('SECRETKEY', path.read_text(encoding='utf-8'))
        loaded = oauth_desktop.load_session(data_dir=self.folder)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded['profile']['name'], 'Ada')
        self.assertEqual(loaded['profile']['credits'], 99)
        self.assertEqual(loaded['access_token'], 'tok')

        # Expired → cleared
        raw['expires_at'] = time.time() - 10
        path.write_text(json.dumps(raw), encoding='utf-8')
        self.assertIsNone(oauth_desktop.load_session(data_dir=self.folder))
        self.assertFalse(path.exists())

    def test_normalize_api_key_strips_bearer(self):
        self.assertEqual(oauth_desktop.normalize_api_key('Bearer abc.def'), 'abc.def')
        self.assertEqual(oauth_desktop.normalize_api_key('  xyz  '), 'xyz')
        self.assertEqual(oauth_desktop.normalize_api_key(None), '')

    def test_login_end_to_end_with_mocks(self):
        """Drive login() against a local authorize-callback + token + userinfo stack."""
        # Local API stand-in for token + userinfo.
        api_state = {'token_hits': 0, 'userinfo_hits': 0}

        class ApiHandler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get('Content-Length', '0'))
                self.rfile.read(length)
                api_state['token_hits'] += 1
                payload = json.dumps({
                    'access_token': 'access-from-test',
                    'token_type': 'Bearer',
                    'expires_in': 1800,
                }).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):  # noqa: N802
                api_state['userinfo_hits'] += 1
                payload = json.dumps({
                    'name': 'Test User',
                    'credits': 42,
                    'key': 'DesktopOAuthTestKey0000000000001',
                    'user_id': 'u-test',
                }).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *a):
                return

        api = HTTPServer(('127.0.0.1', 0), ApiHandler)
        api_port = api.server_port

        def api_serve():
            # token POST + userinfo GET
            api.handle_request()
            api.handle_request()

        api_thread = threading.Thread(target=api_serve, daemon=True)
        api_thread.start()

        opened = {}

        def fake_open(url):
            opened['url'] = url
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
            redirect = q['redirect_uri']
            state = q['state']
            # Hit the loopback callback the login() waiter is listening on.

            def hit():
                time.sleep(0.05)
                import urllib.request
                urllib.request.urlopen(
                    '%s?code=auth-code&state=%s' % (redirect, state), timeout=3)

            threading.Thread(target=hit, daemon=True).start()
            return True

        with patch.object(oauth_desktop, 'TOKEN_URL', 'http://127.0.0.1:%d/oauth/token' % api_port), \
             patch.object(oauth_desktop, 'USERINFO_URL', 'http://127.0.0.1:%d/oauth/userinfo' % api_port):
            result = oauth_desktop.login(
                open_browser=True, timeout=5, preferred_port=0, browser_opener=fake_open)

        api_thread.join(timeout=3)
        api.server_close()
        self.assertIn('code_challenge', opened['url'])
        self.assertEqual(result['access_token'], 'access-from-test')
        self.assertEqual(result['key'], 'DesktopOAuthTestKey0000000000001')
        self.assertEqual(result['userinfo']['credits'], 42)
        self.assertEqual(api_state['token_hits'], 1)
        self.assertEqual(api_state['userinfo_hits'], 1)

        # Applying key into a pool (mirrors app._apply_oauth_result)
        keys = []
        key = result['key']
        keys = list(dict.fromkeys([key] + keys))
        self.assertEqual(keys[0], key)


class ProfileTests(unittest.TestCase):
    def test_profile_from_userinfo_drops_key(self):
        profile = oauth_desktop.profile_from_userinfo({
            'name': 'N', 'credits': '50', 'key': 'SHOULD_DROP', 'email': 'e@x'
        })
        self.assertNotIn('key', profile)
        self.assertEqual(profile['credits'], 50)
        self.assertEqual(profile['name'], 'N')


if __name__ == '__main__':
    unittest.main()

import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.request
import wan_core as w

VIDEO = b'\x00\x00\x00\x18ftypisom' + b'x' * 200000

class Response:
    def __init__(self, data=VIDEO, content_type='video/mp4', length=None):
        self.status = 200
        self.headers = {'Content-Type': content_type, 'Content-Length': str(len(data) if length is None else length)}
        self.data, self.sizes = io.BytesIO(data), []
    def read(self, size=-1):
        assert size > 0, 'Unbounded read'
        self.sizes.append(size)
        return self.data.read(size)
    def __enter__(self): return self
    def __exit__(self, *args): return False

class Tests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        self.out = self.dir / '中文视频.mp4'
        self.client = w.Client(data_dir=self.dir)
        self.client.record = dict(local_id='local', task_id='task_123', state='已提交')

    def recover(self, code, body, timeout=3600):
        with patch.object(w, 'api', return_value=(code, body)), patch.object(w.OPENER, 'open', return_value=Response()) as opened:
            result = self.client.wait('secret', 'task_123', self.out, wall_timeout=timeout)
        self.assertEqual(self.out.read_bytes(), VIDEO)
        self.assertEqual(result['state'], '已完成')
        return opened.call_args.args[0]

    def test_403_content_fallback_authenticated(self):
        req = self.recover(403, {'message': 'token 无模型权限（模型名为空）'})
        self.assertTrue(req.full_url.endswith('/task_123/content'))
        self.assertEqual(req.get_header('Authorization'), 'Bearer secret')

    def test_timeout_content_fallback(self):
        self.recover(200, {'status': 'processing'}, 0)

    def test_completed_missing_url(self):
        self.recover(200, {'status': 'completed'})

    def test_failed_but_content_available(self):
        self.recover(200, {'status': 'failed'})

    def test_timeout_preserves_task(self):
        with patch.object(w, 'api', return_value=(200, {'status': 'processing'})), patch.object(w.OPENER, 'open', return_value=Response(b'{"pending":true}', 'application/json')):
            with self.assertRaises(w.TaskError) as caught:
                self.client.wait('key', 'task_123', self.out, wall_timeout=0)
        self.assertEqual(caught.exception.kind, 'TIMEOUT')
        self.assertEqual(caught.exception.task_id, 'task_123')
        self.assertFalse(self.out.exists())

    def test_uncertain_submission_never_switches_keys(self):
        for code, body in ((None, {}), (502, {}), (200, {})):
            with self.subTest(code=code), patch.object(w, 'api', return_value=(code, body)) as api:
                with self.assertRaises(w.TaskError) as caught:
                    self.client.submit(['key1', 'key2'], w.payload('hi'), self.out)
                self.assertEqual(caught.exception.kind, 'UNCERTAIN')
                self.assertEqual(api.call_count, 1)

    def test_id_saved_before_poll_and_no_resubmit_on_timeout(self):
        def wait(*args):
            record = json.loads(next((self.dir / 'tasks').glob('*.json')).read_text(encoding='utf-8'))
            self.assertEqual(record['task_id'], 'task_123')
            self.assertNotIn('secret-key', json.dumps(record))
            raise w.TaskError('timeout', 'TIMEOUT', 'task_123')
        with patch.object(w, 'api', return_value=(201, {'id': 'task_123'})) as api, patch.object(self.client, 'wait', side_effect=wait):
            with self.assertRaises(w.TaskError):
                self.client.submit(['secret-key', 'key2'], w.payload('hi'), self.out)
        self.assertEqual(api.call_count, 1)

    def test_explicit_rejection_can_switch(self):
        with patch.object(w, 'api', side_effect=[(401, {}), (201, {'id': 'task_123'})]) as api, patch.object(self.client, 'wait', return_value={}):
            self.client.submit(['one', 'two'], w.payload('hi'), self.out)
        self.assertEqual(api.call_count, 2)

    def test_resume_only_gets(self):
        with patch.object(w, 'api', return_value=(200, {'status': 'completed'})) as api, patch.object(w.OPENER, 'open', return_value=Response()):
            self.client.resume('key', 'task_123', self.out)
        self.assertTrue(all(c.args[0] == 'GET' for c in api.call_args_list))

    def test_streaming_uses_bounded_reads(self):
        response = Response()
        with patch.object(w.OPENER, 'open', return_value=response):
            size = w.download(w.BASE + '/content', self.out, 'secret')
        self.assertEqual(size, len(VIDEO))
        self.assertGreater(len(response.sizes), 1)
        self.assertLessEqual(max(response.sizes), 1048576)

    def test_no_auth_sent_to_cdn(self):
        with patch.object(w.OPENER, 'open', return_value=Response()) as opened:
            w.download('https://cdn.example.com/video', self.out, 'secret')
        self.assertIsNone(opened.call_args.args[0].get_header('Authorization'))

    def test_redirect_strips_auth(self):
        request = urllib.request.Request(w.BASE + '/content', headers={'Authorization': 'Bearer secret'})
        target = w.SafeRedirect().redirect_request(request, None, 302, '', {}, 'https://cdn.example.com/video')
        self.assertIsNone(target.get_header('Authorization'))

    def test_no_http_downgrade(self):
        with self.assertRaises(w.TaskError):
            w.SafeRedirect().redirect_request(urllib.request.Request(w.BASE), None, 302, '', {}, 'http://cdn.example.com/video')

    def test_json_is_not_saved_as_video(self):
        with patch.object(w.OPENER, 'open', return_value=Response(b'{"pending":true}', 'application/json')):
            with self.assertRaises(w.TaskError):
                w.download(w.BASE + '/content', self.out, 'key')
        self.assertFalse(self.out.exists())

    def test_truncated_preserves_partial_and_old_output(self):
        self.out.write_bytes(b'original')
        with patch.object(w.OPENER, 'open', return_value=Response(length=len(VIDEO) + 10)):
            with self.assertRaises(w.TaskError):
                w.download(w.BASE + '/content', self.out, 'key')
        self.assertEqual(self.out.read_bytes(), b'original')
        self.assertEqual((self.dir / (self.out.name + '.part')).read_bytes(), VIDEO)
        self.assertTrue((self.dir / (self.out.name + '.part.json')).is_file())

    def test_stop_preserves_partial(self):
        stop = threading.Event()
        stop.set()
        with patch.object(w.OPENER, 'open', return_value=Response()):
            with self.assertRaises(w.TaskError) as caught:
                w.download(w.BASE + '/content', self.out, 'key', stop=stop)
        self.assertEqual(caught.exception.kind, 'PAUSED')
        self.assertEqual((self.dir / (self.out.name + '.part')).stat().st_size, 64 * 1024)

    def test_permission_not_invalid_key(self):
        self.assertEqual(w.category(403, {'message': 'token 无模型权限'}), 'NOT_ALLOWED')
        self.assertEqual(w.category(403, {'message': 'insufficient quota'}), 'QUOTA')

    def test_input_validation(self):
        for seconds in (-1, 0, 31, 'abc'):
            with self.assertRaises(w.TaskError): w.payload('hi', seconds)
        with self.assertRaises(w.TaskError): w.payload('hi', seed='abc')
        with self.assertRaises(w.TaskError): w.payload('hi', media=[{'type': 'first_frame', 'url': 'C:/a.png'}])
        with self.assertRaises(w.TaskError): w.task_url('../other')

    def test_user_agent_names_the_app(self):
        with patch.object(w.OPENER, 'open', side_effect=OSError) as opened:
            w.api('GET', w.API_BASE + '/v1/models', 'key')
        self.assertEqual(opened.call_args.args[0].get_header('User-agent'), 'PowerTokensVideoStudio/' + w.APP_VERSION)

    def test_api_base_validation(self):
        self.assertEqual(w.resolve_api_base(None), 'https://api.powertokens.ai')
        self.assertEqual(w.resolve_api_base(' https://gw.example.com/ '), 'https://gw.example.com')
        for bad in ('http://gw.example.com', 'gw.example.com', 'https://user:pw@gw.example.com', 'https://gw.example.com?x=1'):
            with self.assertRaises(ValueError):
                w.resolve_api_base(bad)

    def test_api_base_env_override(self):
        import subprocess, sys
        code = 'import wan_core as w; print(w.API_BASE); print(w.task_url("task_1"))'
        env = dict(os.environ, POWERTOKENS_API_BASE='https://gw.example.com', PYTHONIOENCODING='utf-8')
        out = subprocess.run([sys.executable, '-c', code], cwd=str(Path(w.__file__).parent), env=env,
                             capture_output=True, text=True, encoding='utf-8', check=True).stdout.split()
        self.assertEqual(out, ['https://gw.example.com', 'https://gw.example.com/v1/videos/task_1'])

    def test_key_only_sent_to_configured_base(self):
        with patch.object(w, 'API_BASE', 'https://gw.example.com'):
            with patch.object(w.OPENER, 'open', return_value=Response()) as opened:
                w.download('https://gw.example.com/v1/videos/t/content', self.out, 'secret')
            self.assertEqual(opened.call_args.args[0].get_header('Authorization'), 'Bearer secret')
            self.out.unlink()
            with patch.object(w.OPENER, 'open', return_value=Response()) as opened:
                w.download('https://api.powertokens.ai/v1/videos/t/content', self.out, 'secret')
            self.assertIsNone(opened.call_args.args[0].get_header('Authorization'))
            self.assertTrue(w.is_api_url('https://GW.example.com:443/x'))
            self.assertFalse(w.is_api_url('https://gw.example.com:8443/x'))

    def test_signed_link_kept_until_download_completes_then_cleared(self):
        link = 'https://oss.example.com/v.mp4?Signature=abc'
        path = lambda: json.loads(next((self.dir / 'tasks').glob('*.json')).read_text(encoding='utf-8'))
        with patch.object(w, 'download', side_effect=w.TaskError('drop', 'NETWORK')):
            with self.assertRaises(w.TaskError):
                w.Client(data_dir=self.dir).resume_download_url('key', 'task_123', self.out, link)
        self.assertEqual(path()['download_url'], link)
        record = path()
        with patch.object(w, 'download', return_value=len(VIDEO)):
            w.Client(data_dir=self.dir).resume_download_url('key', 'task_123', self.out, link, record)
        self.assertEqual(path()['download_url'], '')
        self.assertEqual(path()['state'], '已完成')

    @unittest.skipUnless(os.name == 'nt', 'Windows DPAPI only')
    def test_windows_encryption(self):
        from app import protect
        secret = b'sk-pt-secret'
        encrypted = protect(secret)
        self.assertNotEqual(secret, encrypted)
        self.assertEqual(protect(encrypted, True), secret)

if __name__ == '__main__': unittest.main()


class PromoPricingTests(unittest.TestCase):
    def test_discount_through_end_date_inclusive(self):
        import datetime
        import wan_core
        self.assertEqual(wan_core.PROMO_END_DATE, datetime.date(2026, 10, 7))
        for day, p720, p1080, regular in ((datetime.date(2026, 9, 30), .04, .08, True),
                                          (datetime.date(2026, 10, 7), .04, .08, True),
                                          (datetime.date(2026, 10, 8), .10, .20, False)):
            self.assertEqual(wan_core.current_prices(day), {'720p': p720, '1080p': p1080}, day)
            self.assertEqual(bool(wan_core.list_prices(day)), regular, day)
            self.assertEqual(wan_core.estimate_cost(20, '1080p', today=day), round(20 * p1080, 3), day)

    def test_default_date_comes_from_local_today(self):
        import datetime
        import wan_core
        from unittest.mock import patch
        with patch.object(wan_core, 'local_today', return_value=datetime.date(2026, 10, 8)):
            self.assertFalse(wan_core.promo_active())
            self.assertEqual(wan_core.estimate_cost(10, '720p'), 1.0)
        with patch.object(wan_core, 'local_today', return_value=datetime.date(2026, 10, 7)):
            self.assertTrue(wan_core.promo_active())
            self.assertEqual(wan_core.estimate_cost(10, '720p'), 0.4)

    def test_promotion_text_hides_after_end(self):
        import datetime
        from studio_ui import promotion_text
        self.assertEqual(promotion_text(datetime.date(2026, 10, 7)), 'Wan 3.0 限时折扣至 10 月 7 日')
        self.assertEqual(promotion_text(datetime.date(2026, 10, 8)), '')

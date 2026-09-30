"""Shared Windows UI / CLI engine. Standard library only; no automatic POST retries."""
import datetime
import hashlib
import http.client
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

APP_VERSION = '1.10'
USER_AGENT = 'PowerTokensVideoStudio/' + APP_VERSION
DEFAULT_API_BASE = 'https://api.powertokens.ai'
UTM = 'utm_source=github&utm_medium=oss&utm_campaign=video-studio'


def resolve_api_base(value=None):
    """Return the API origin. Only HTTPS is accepted because the API Key is sent to this host."""
    value = (value or '').strip().rstrip('/') or DEFAULT_API_BASE
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ValueError('POWERTOKENS_API_BASE 必须是 HTTPS 地址，例如 %s' % DEFAULT_API_BASE)
    return value


# Single source of truth for the API host; override with the POWERTOKENS_API_BASE environment variable.
API_BASE = resolve_api_base(os.environ.get('POWERTOKENS_API_BASE'))
BASE = API_BASE  # Backward-compatible alias.
MODEL = 'wan3.0-video'
# PT prices per second (USD), checked on PRICE_CHECKED_DATE at PRICE_SOURCE_URL.
LIST_PRICE_USD_PER_SECOND = {'720p': 0.10, '1080p': 0.20}
# Wan 3.0 limited-time discount, valid through PROMO_END_DATE (inclusive, local date).
PROMO_PRICE_USD_PER_SECOND = {'720p': 0.04, '1080p': 0.08}
PROMO_END_DATE = datetime.date(2026, 10, 7)


def local_today():
    return datetime.date.today()  # Local calendar date of this computer.


def promo_active(today=None):
    """True on or before PROMO_END_DATE (local date). `today` is injectable for tests."""
    return (today or local_today()) <= PROMO_END_DATE


def current_prices(today=None):
    return PROMO_PRICE_USD_PER_SECOND if promo_active(today) else LIST_PRICE_USD_PER_SECOND


def list_prices(today=None):
    """Regular prices to show as "原价" while the discount runs; {} afterwards."""
    return LIST_PRICE_USD_PER_SECOND if promo_active(today) else {}


PRICE_CHECKED_DATE = '2026-09-30'
PRICE_SOURCE_URL = 'https://powertokens.ai/zh-Hans/models/wan3.0-video?' + UTM
DATA_DIR = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / '.local' / 'share'))) / 'PowerTokensWan'


class TaskError(Exception):
    def __init__(self, message, kind='UNKNOWN', task_id=None):
        super().__init__(message)
        self.kind, self.task_id = kind, task_id


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def fingerprint(key):
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def task_url(task_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', task_id):
        raise TaskError('任务 ID 格式不正确', 'PARAM')
    return API_BASE + '/v1/videos/' + task_id


def origin(url):
    parsed = urllib.parse.urlsplit(url)
    scheme = (parsed.scheme or '').lower()
    port = parsed.port or {'https': 443, 'http': 80}.get(scheme)
    return scheme, (parsed.hostname or '').lower(), port


def is_api_url(url):
    """True only for the configured API origin; the API Key is never sent anywhere else."""
    return origin(url) == origin(API_BASE)


def estimate_cost(duration, resolution, prices=None, today=None):
    prices = current_prices(today) if prices is None else prices
    if resolution not in prices:
        raise ValueError('不支持的分辨率')
    return round(int(duration) * prices[resolution], 3)


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never send API credentials to a CDN or allow an HTTPS downgrade.
        old, new = urllib.parse.urlsplit(req.full_url), urllib.parse.urlsplit(newurl)
        if new.scheme != 'https':
            raise TaskError('下载重定向不是 HTTPS，已停止', 'DOWNLOAD')
        result = super().redirect_request(req, fp, code, msg, headers, newurl)
        if result is not None and (old.hostname, old.port) != (new.hostname, new.port):
            result.remove_header('Authorization')
        return result


OPENER = urllib.request.build_opener(SafeRedirect())


def api(method, url, key, payload=None, timeout=30):
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
        'User-Agent': USER_AGENT})
    try:
        with OPENER.open(req, timeout=timeout) as response:
            code, raw = response.status, response.read(2 * 1024 * 1024)
    except urllib.error.HTTPError as exc:
        code, raw = exc.code, exc.read(2 * 1024 * 1024)
    except (OSError, ValueError, TaskError, http.client.HTTPException):
        return None, {}
    try:
        body = json.loads(raw.decode('utf-8'))
        return code, body if isinstance(body, dict) else {}
    except (ValueError, UnicodeError):
        return code, {}


def category(code, body):
    text = json.dumps(body, ensure_ascii=False).lower()
    if code == 402 or any(x in text for x in ('quota', 'insufficient', '余额不足')):
        return 'QUOTA'
    if any(x in text for x in ('无模型权限', '无权', 'not permitted', 'not authorized to model')):
        return 'NOT_ALLOWED'
    if code == 401 or 'token_invalid' in text or 'invalid token' in text:
        return 'AUTH'
    if code == 403:
        return 'NOT_ALLOWED'
    return 'UNKNOWN'


def payload(prompt, duration=5, resolution='720p', ratio='16:9', media=None, seed=''):
    try:
        duration = int(duration)
    except (TypeError, ValueError):
        raise TaskError('时长必须是整数', 'PARAM')
    if not 2 <= duration <= 30:
        raise TaskError('此工具的时长范围为 2–30 秒', 'PARAM')
    if resolution not in ('720p', '1080p') or ratio not in ('16:9', '9:16', '1:1'):
        raise TaskError('请选择有效的分辨率和画面比例', 'PARAM')
    media = media or []
    if not prompt.strip() and not media:
        raise TaskError('请填写提示词或素材 URL', 'PARAM')
    for item in media:
        parsed = urllib.parse.urlsplit(item['url'])
        if parsed.scheme not in ('http', 'https') or not parsed.hostname:
            raise TaskError('素材必须是可公开访问的 HTTP/HTTPS URL', 'PARAM')
        if item['type'] not in ('first_frame', 'last_frame', 'reference_image', 'reference_video', 'reference_audio'):
            raise TaskError('素材类型无效', 'PARAM')
    result = dict(model=MODEL, prompt=prompt.strip(), seconds=str(duration), size=resolution.upper(), ratio=ratio,
                  generate_audio=True)
    if media:
        result['media'] = media
    if str(seed).strip():
        try:
            result['seed'] = int(seed)
        except (TypeError, ValueError):
            raise TaskError('种子必须是整数或留空', 'PARAM')
    return result


def download_source(url):
    parsed = urllib.parse.urlsplit(url)
    # Drop only common expiring-signature fields; retain parameters that may select a version.
    volatile = ('signature', 'sig', 'token', 'expires', 'x-amz-', 'x-goog-', 'key-pair-id', 'policy')
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
             if not any(k.lower().startswith(prefix) for prefix in volatile)]
    canonical = parsed.scheme.lower() + '://' + parsed.netloc.lower() + parsed.path
    if query:
        canonical += '?' + urllib.parse.urlencode(sorted(query))
    return hashlib.sha256(canonical.encode()).hexdigest()


def partial_download_source(out_path):
    out = Path(out_path)
    meta_path = out.with_name(out.name + '.part.json')
    try:
        meta = json.loads(meta_path.read_text(encoding='utf-8'))
        part = out.with_name(out.name + '.part')
        if part.is_file() and meta.get('source'):
            return meta['source']
    except (OSError, ValueError):
        pass
    return None


def download(url, out_path, key, stop=None, report=lambda text: None, timeout=180, _direct_retry=False):
    """Download with safe HTTP Range resume when the CDN supports it."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise TaskError('视频地址必须是有效的 HTTPS URL', 'DOWNLOAD')
    headers = {'User-Agent': USER_AGENT, 'Accept-Encoding': 'identity'}
    if is_api_url(url):
        headers['Authorization'] = 'Bearer ' + key
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(out.name + '.part')
    meta_path = out.with_name(out.name + '.part.json')
    source = download_source(url)
    done_key = hashlib.sha256(str(out.resolve()).encode()).hexdigest()
    done_path = DATA_DIR / 'downloads' / (done_key + '.json')
    new_etag = new_modified = None
    try:
        done = json.loads(done_path.read_text(encoding='utf-8'))
        if (out.is_file() and done.get('source') == source and done.get('bytes') == out.stat().st_size
                and out.open('rb').read(12)[4:8] == b'ftyp'):
            return out.stat().st_size
    except (OSError, ValueError):
        pass
    meta = {}
    try:
        meta = json.loads(meta_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        pass
    if not part.is_file():
        meta_path.unlink(missing_ok=True)
        meta = {}
    offset = part.stat().st_size if part.exists() else 0
    range_check_version = 3
    overlap = min(offset, 64 * 1024)
    range_start = offset - overlap
    if offset:
        headers['Range'] = 'bytes=%d-' % range_start
    requested_range = bool(headers.get('Range'))
    req = urllib.request.Request(url, headers=headers)
    try:
        try:
            response = OPENER.open(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and offset:
                raise TaskError('服务器不接受断点位置，已保留原下载进度。', 'DOWNLOAD')
            raise TaskError('下载端返回 HTTP %d；任务仍可稍后继续查询。' % exc.code, 'DOWNLOAD')
        with response:
            status = response.status
            final_url = response.geturl() if hasattr(response, 'geturl') else url
            final_host = urllib.parse.urlsplit(final_url).hostname or parsed.hostname
            if offset:
                report('续传诊断：请求从 %d 字节开始，%s 返回 HTTP %d。' % (range_start, final_host, status))
            if offset and status == 200:
                if final_url != url and not _direct_retry:
                    report('下载接口返回整文件，改用最终视频地址继续已有进度。')
                    response.close()
                    return download(final_url, out_path, key, stop, report, timeout, _direct_retry=True)
                raise TaskError('%s 对 Range 请求返回 HTTP 200；已保留 %.1f MB 临时文件。可粘贴 OSS 下载链接直接续传。' %
                                (final_host, offset / 1048576), 'NO_RESUME')
            content_type = response.headers.get('Content-Type', '').lower()
            if status not in (200, 206) or 'json' in content_type or 'html' in content_type:
                raise TaskError('下载端尚未返回视频内容，已保留现有下载进度。', 'DOWNLOAD')
            new_etag = response.headers.get('ETag')
            new_modified = response.headers.get('Last-Modified')
            if status == 206:
                content_range = response.headers.get('Content-Range', '')
                match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', content_range.strip(), re.I)
                if not offset or not match or int(match.group(1)) != range_start:
                    raise TaskError('服务器返回的续传范围不匹配，拒绝拼接损坏文件。', 'DOWNLOAD')
                same_source = meta.get('source') == source
                if same_source and meta.get('etag') and new_etag and meta['etag'] != new_etag:
                    raise TaskError('视频文件版本已变化，拒绝拼接旧下载进度。', 'DOWNLOAD')
                if same_source and not meta.get('etag') and meta.get('last_modified') and new_modified and meta['last_modified'] != new_modified:
                    raise TaskError('视频文件时间标识已变化，拒绝拼接旧下载进度。', 'DOWNLOAD')
                expected_total = int(match.group(3))
                if meta.get('total') is not None and int(meta['total']) != expected_total:
                    raise TaskError('视频总大小已变化，拒绝拼接旧下载进度。', 'DOWNLOAD')
                mode = 'ab'
                range_supported = True
            else:
                range_supported = None
                length = response.headers.get('Content-Length')
                expected_total = int(length) if length and length.isdigit() else None
                mode = 'wb'
            if overlap:
                saved_tail = b''
                with part.open('rb') as saved:
                    saved.seek(range_start)
                    saved_tail = saved.read(overlap)
                remote_tail = response.read(overlap)
                if remote_tail != saved_tail:
                    raise TaskError('续传处的视频内容与临时文件不同，已保留原进度，未拼接。', 'DOWNLOAD')
            first = response.read(64 * 1024)
            if offset == 0 and (len(first) < 12 or first[4:8] != b'ftyp'):
                raise TaskError('下载响应不是有效的 MP4 视频，已保留原文件。', 'DOWNLOAD')
            if not first and expected_total and offset < expected_total:
                raise TaskError('下载连接暂时没有返回数据，保留进度后稍后重试。', 'NETWORK')
            same_source = meta.get('source') == source
            meta = {'source': source, 'etag': new_etag or (meta.get('etag') if same_source else None),
                    'last_modified': new_modified or (meta.get('last_modified') if same_source else None), 'total': expected_total,
                    'range_check_version': range_check_version,
                    'range_supported': range_supported}
            atomic_json(meta_path, meta)
            received = offset + len(first)
            reported = time.monotonic()
            with part.open(mode) as f:
                f.write(first)
                f.flush()
                while True:
                    if stop and stop.is_set():
                        raise TaskError('已停止等待；下载进度已保留，可稍后继续。', 'PAUSED')
                    try:
                        chunk = response.read(1024 * 1024)
                    except http.client.IncompleteRead as exc:
                        if exc.partial:
                            f.write(exc.partial)
                            received += len(exc.partial)
                            f.flush()
                        raise TaskError('下载连接中断，已保留 %.1f MB；稍后会尝试断点续传。' % (received / 1048576), 'NETWORK')
                    if not chunk:
                        break
                    f.write(chunk)
                    received += len(chunk)
                    f.flush()
                    if time.monotonic() - reported >= 1:
                        report('下载中：%.1f MB%s' % (received / 1048576,
                               (' / %.1f MB' % (expected_total / 1048576)) if expected_total else ''))
                        reported = time.monotonic()
        actual = part.stat().st_size
        if expected_total is not None and actual != expected_total:
            if status == 206:
                # The resumed response ended before its declared byte range. Keep its
                # validator so a later request can safely continue from the new offset.
                meta['range_supported'] = True
                meta['range_check_version'] = range_check_version
                atomic_json(meta_path, meta)
            raise TaskError('下载连接中断，已保留 %.1f MB / %.1f MB；可继续断点续传。' %
                            (actual / 1048576, expected_total / 1048576), 'NETWORK')
        with part.open('rb') as f:
            if f.read(12)[4:8] != b'ftyp':
                raise TaskError('已下载文件的 MP4 文件头校验失败。', 'DOWNLOAD')
        os.replace(part, out)
        meta_path.unlink(missing_ok=True)
        try:
            atomic_json(done_path, {'source': source, 'bytes': actual})
        except OSError:
            pass
        return actual
    except TaskError:
        raise
    except (OSError, TimeoutError, http.client.HTTPException) as exc:
        actual = part.stat().st_size if part.exists() else offset
        raise TaskError('下载连接中断%s；稍后会尝试断点续传。' %
                        (('，已保留 %.1f MB' % (actual / 1048576)) if actual else ''), 'NETWORK') from exc



class Client:
    def __init__(self, report=lambda text: None, stop=None, data_dir=None, on_record=None, context=None, download_slots=None, defer_on_403=False):
        self.report = report
        self.stop = stop or threading.Event()
        self.data_dir = Path(data_dir) if data_dir else DATA_DIR
        self.record = None
        self.on_record = on_record
        self.context = context or {}
        self.download_slots = download_slots or threading.BoundedSemaphore(1)
        self.last_download_error = ''
        self.defer_on_403 = defer_on_403

    def save(self, **changes):
        self.record.update(changes)
        atomic_json(self.data_dir / 'tasks' / (self.record['local_id'] + '.json'), self.record)
        if self.on_record:
            self.on_record(dict(self.record))

    def submit(self, keys, request, out_path):
        if request.get('model') != MODEL:
            raise TaskError('仅支持 wan3.0-video', 'PARAM')
        if not keys:
            raise TaskError('请先添加 API Key', 'AUTH')
        output = Path(out_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        probe = output.parent / ('.write-test-' + uuid.uuid4().hex)
        try:
            probe.touch()
        finally:
            probe.unlink(missing_ok=True)
        self.record = dict(local_id=uuid.uuid4().hex, task_id='', created=time.strftime('%Y-%m-%d %H:%M:%S'),
                           output=str(Path(out_path).absolute()), state='准备提交', key_hash='', key_hint='')
        self.record.update(self.context)
        for index, key in enumerate(keys):
            if self.stop.is_set():
                raise TaskError('已停止提交', 'PAUSED')
            self.save(state='提交中', key_hash=fingerprint(key), key_hint='****' + key[-4:])
            self.report('提交任务：Key %d/%d（****%s）' % (index + 1, len(keys), key[-4:]))
            code, body = api('POST', API_BASE + '/v1/videos', key, request)
            task_id = body.get('id') or body.get('task_id')
            if isinstance(task_id, str) and task_id:
                task_url(task_id)
                # Persist before the first query so a restart can recover the same task.
                self.report('任务 ID：' + task_id)
                try:
                    self.save(task_id=task_id, state='已提交')
                except OSError:
                    raise TaskError('任务已提交，但记录保存失败。请保留任务 ID：' + task_id, 'STORAGE', task_id)
                return self.wait(key, task_id, out_path)
            kind = category(code, body)
            # Only explicit rejection before obtaining an ID can switch keys.
            if code in (400, 401, 402, 403, 429) and kind in ('AUTH', 'QUOTA', 'NOT_ALLOWED'):
                self.save(state='提交被拒绝：' + kind)
                self.report('该 Key 提交被拒绝：' + kind)
                continue
            self.save(state='提交结果未知，请查仪表盘')
            raise TaskError('提交结果不确定（HTTP %s）。请先查仪表盘，勿直接重新生成，以免重复扣费。' % code, 'UNCERTAIN')
        raise TaskError('所有 Key 均被拒绝，请检查余额和模型权限', 'REJECTED')

    def resume(self, key, task_id, out_path, record=None):
        task_url(task_id)
        self.record = dict(record) if record else dict(local_id=uuid.uuid4().hex,
            created=time.strftime('%Y-%m-%d %H:%M:%S'), task_id=task_id)
        self.save(state='继续查询', output=str(Path(out_path).absolute()), key_hash=fingerprint(key), key_hint='****' + key[-4:])
        return self.wait(key, task_id, out_path)

    def resume_download_url(self, key, task_id, out_path, video_url, record=None):
        task_url(task_id)
        parsed = urllib.parse.urlsplit(video_url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise TaskError('请粘贴有效的 HTTPS 视频下载链接。', 'PARAM', task_id)
        if is_api_url(video_url) and not key:
            raise TaskError('PT API 下载地址需要原 API Key；OSS 视频链接可直接下载。', 'AUTH', task_id)
        self.record = dict(record) if record else dict(local_id=uuid.uuid4().hex,
            created=time.strftime('%Y-%m-%d %H:%M:%S'), task_id=task_id)
        changes = dict(state='从视频链接续传', output=str(Path(out_path).absolute()), download_url=video_url)
        if key:
            changes.update(key_hash=fingerprint(key), key_hint='****' + key[-4:])
        self.save(**changes)
        result = self.try_download(key, video_url, out_path)
        if result == 'ok':
            return self.record
        self.save(state='下载待恢复；进度已保留')
        raise TaskError(self.last_download_error or '视频链接暂时不可用；临时文件已保留。',
                        'RANGE_UNSUPPORTED' if result == 'blocked' else 'DOWNLOAD', task_id)

    def try_download(self, key, url, out_path):
        acquired = False
        self.last_download_error = ''
        try:
            while not acquired:
                if self.stop.is_set():
                    raise TaskError('已停止等待；任务可稍后继续。', 'PAUSED')
                acquired = self.download_slots.acquire(timeout=.5)
            size = download(url, out_path, key, self.stop, self.report)
        except TaskError as exc:
            if exc.kind == 'PAUSED':
                raise
            self.last_download_error = str(exc)
            self.report(str(exc))
            if exc.kind == 'NO_RESUME':
                return 'blocked'
            return 'retry' if exc.kind == 'NETWORK' else 'unavailable'
        except (OSError, ValueError, http.client.HTTPException) as exc:
            self.last_download_error = '下载连接异常；保留已下载进度。'
            self.report('下载连接异常；保留已下载进度。')
            return 'retry'
        finally:
            if acquired:
                self.download_slots.release()
        self.save(state='已完成', bytes=size, download_source='', download_url='')
        self.report('已保存：' + str(out_path))
        return 'ok'

    def wait(self, key, task_id, out_path, wall_timeout=3600, interval=10):
        url = task_url(task_id)
        deadline = time.monotonic() + wall_timeout
        query_errors = 0
        cached_video_url = self.record.get('download_url', '')
        while True:
            if self.stop.is_set():
                self.save(state='已停止等待')
                raise TaskError('已停止等待；任务仍在云端继续并可能计费，可在任务记录里找回', 'PAUSED', task_id)
            code, body = api('GET', url, key)
            if code == 403 and self.defer_on_403:
                self.save(state='原 Key 查询返回 403；等待批次末尾重试')
                raise TaskError('原 Key 查询返回 HTTP 403；暂缓此任务，批次末尾再用原 Key 查询。',
                                'QUERY_FORBIDDEN', task_id)
            status = str(body.get('status') or '').strip().lower()
            expired = time.monotonic() >= deadline
            ready = status in ('succeeded', 'completed', 'success')
            terminal = status in ('failed', 'cancelled', 'canceled')
            query_failed = code != 200 or not status
            if query_failed:
                query_errors += 1
                self.report('查询失败：HTTP %s · %s · 连续 %d 次。' %
                            (code, status or '未返回有效状态', query_errors))
            else:
                query_errors = 0
                self.report('查询：HTTP %s · %s%s' % (code, status,
                            (' · %s%%' % body['progress']) if body.get('progress') is not None else ''))
            retrying_download = False
            response_meta = body.get('metadata') or {}
            direct = response_meta.get('url') if isinstance(response_meta, dict) else None
            if ready and isinstance(direct, str) and direct.startswith('https://'):
                if cached_video_url != direct:
                    cached_video_url = direct
                    self.save(download_url=direct)
            if ready or code == 403 or expired or terminal or (query_failed and (cached_video_url or query_errors >= 3)):
                candidates = []
                if cached_video_url:
                    candidates.append(cached_video_url)
                content_url = url + '/content'
                if (ready or code == 403 or expired or terminal or query_errors >= 3) and content_url not in candidates:
                    candidates.append(content_url)
                # Keep resuming the same endpoint after a dropped connection.
                saved_source = partial_download_source(out_path)
                if saved_source:
                    candidates.sort(key=lambda item: download_source(item) != saved_source)
                for download_url in candidates:
                    self.report('从已下载位置继续…' if partial_download_source(out_path) else '开始下载视频…')
                    outcome = self.try_download(key, download_url, out_path)
                    if outcome == 'ok':
                        return self.record
                    if outcome == 'blocked':
                        self.save(state='服务器不支持安全续传；进度已保留', download_source=download_source(download_url))
                        raise TaskError(self.last_download_error or '视频服务器未提供可用的断点续传条件，临时文件已保留。', 'RANGE_UNSUPPORTED', task_id)
                    if outcome == 'retry':
                        self.save(state='下载中断，已保留进度；稍后续传', download_source=download_source(download_url))
                        retrying_download = True
                        break
                    # A definitive refusal can use the API content fallback. Do not
                    # switch endpoints after a network interruption: keep the partial.
            if terminal:
                self.save(state='服务端返回失败；可再次查询')
                raise TaskError('服务端报告 %s，/content 暂无视频。已保留任务 ID，未重新提交。' % status, 'FAILED', task_id)
            if expired:
                self.save(state='下载中断，进度已保留；可继续查询' if retrying_download else '等待超时；可继续查询')
                raise TaskError('等待超时，已探测 /content，暂未取得视频。请继续查询原任务，勿直接重新生成。', 'TIMEOUT', task_id)
            if ready:
                self.save(state='下载中断，进度已保留；继续尝试' if retrying_download else '生成完成，下载待重试')
                if not retrying_download:
                    self.report('视频已生成，下载暂时不可用；稍后会重试。')
            if query_failed and query_errors >= 6:
                self.save(state='查询接口连续失败；可继续原任务')
                raise TaskError('任务状态接口连续 %d 次无有效结果（最近 HTTP %s）；已停止本地轮询。请稍后继续原任务，或粘贴该任务的视频链接下载。' %
                                (query_errors, code), 'QUERY_UNAVAILABLE', task_id)
            delay = min(60, interval * (2 ** min(query_errors - 1, 3))) if query_failed else interval
            if query_failed:
                self.report('下次查询约 %d 秒后。' % delay)
            self.stop.wait(min(delay, max(0, deadline - time.monotonic())))

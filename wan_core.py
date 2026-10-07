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

from i18n import t

APP_VERSION = '1.12'
USER_AGENT = 'PowerTokensVideoStudio/' + APP_VERSION
DEFAULT_API_BASE = 'https://api.powertokens.ai'
# In-app links (get-key button, price source) use medium=app; README links use medium=oss.
UTM = 'utm_source=github&utm_medium=app&utm_campaign=video-studio'


def resolve_api_base(value=None):
    """Return the API origin. Only HTTPS is accepted because the API Key is sent to this host."""
    value = (value or '').strip().rstrip('/') or DEFAULT_API_BASE
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ValueError(t('api_base_https', DEFAULT_API_BASE))
    return value


# Single source of truth for the API host; override with the POWERTOKENS_API_BASE environment variable.
API_BASE = resolve_api_base(os.environ.get('POWERTOKENS_API_BASE'))
BASE = API_BASE  # Backward-compatible alias.

import models as _models
from models import (DEFAULT_MODEL_ID, MODELS, PRICE_CHECKED_DATE, WAN_LIST_USD_PER_SECOND,
                    WAN_PROMO_END_DATE, WAN_PROMO_USD_PER_SECOND, get_model, list_models,
                    resolve_model_id, wan_promo_active)

MODEL = DEFAULT_MODEL_ID  # Backward-compatible default model id.
LIST_PRICE_USD_PER_SECOND = dict(WAN_LIST_USD_PER_SECOND)
PROMO_PRICE_USD_PER_SECOND = dict(WAN_PROMO_USD_PER_SECOND)
PROMO_END_DATE = WAN_PROMO_END_DATE


def local_today():
    return _models.local_today()


def promo_active(today=None):
    """True on or before PROMO_END_DATE (local date). `today` is injectable for tests."""
    return wan_promo_active(today if today is not None else local_today())


def current_prices(today=None, model_id=None):
    return get_model(model_id or DEFAULT_MODEL_ID).current_prices(
        today if today is not None else local_today())


def list_prices(today=None, model_id=None):
    """Regular prices to show as "原价" while the discount runs; {} afterwards."""
    return get_model(model_id or DEFAULT_MODEL_ID).list_prices_for_display(
        today if today is not None else local_today())


PRICE_SOURCE_URL = get_model(DEFAULT_MODEL_ID).model_page_url_zh
PRICE_SOURCE_URL_EN = get_model(DEFAULT_MODEL_ID).model_page_url
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


def task_url(task_id, poll_kind='unified'):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', task_id):
        raise TaskError(t('task_id_invalid'), 'PARAM')
    return _models.poll_url(API_BASE, task_id, poll_kind)


def origin(url):
    parsed = urllib.parse.urlsplit(url)
    scheme = (parsed.scheme or '').lower()
    port = parsed.port or {'https': 443, 'http': 80}.get(scheme)
    return scheme, (parsed.hostname or '').lower(), port


def is_api_url(url):
    """True only for the configured API origin; the API Key is never sent anywhere else."""
    return origin(url) == origin(API_BASE)


def estimate_cost(duration, resolution, prices=None, today=None, model_id=None):
    if prices is not None:
        resolution = str(resolution).strip().lower()
        if resolution in ('720', '1080', '480'):
            resolution += 'p'
        if resolution not in prices:
            raise ValueError(t('resolution_unsupported'))
        return round(int(duration) * prices[resolution], 3)
    return _models.estimate_cost(
        duration, resolution, model_id=model_id,
        today=today if today is not None else local_today())


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never send API credentials to a CDN or allow an HTTPS downgrade.
        old, new = urllib.parse.urlsplit(req.full_url), urllib.parse.urlsplit(newurl)
        if new.scheme != 'https':
            raise TaskError(t('redirect_not_https'), 'DOWNLOAD')
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
    """Classify API errors for key rotation. QUOTA/AUTH/NOT_ALLOWED exhaust; RATE_LIMIT is temporary."""
    text = json.dumps(body, ensure_ascii=False).lower()
    if code == 429 or any(x in text for x in ('rate limit', 'rate_limit', 'too many requests', '请求过于频繁')):
        return 'RATE_LIMIT'
    if code == 402 or any(x in text for x in (
            'quota', 'insufficient', '余额不足', 'usage limit', 'credit', '额度', '余额不够')):
        return 'QUOTA'
    if any(x in text for x in ('expired', 'expire', '已过期', 'token expired', 'key expired')):
        return 'AUTH'
    if any(x in text for x in ('无模型权限', '无权', 'not permitted', 'not authorized to model')):
        return 'NOT_ALLOWED'
    if code == 401 or 'token_invalid' in text or 'invalid token' in text:
        return 'AUTH'
    if code == 403:
        return 'NOT_ALLOWED'
    return 'UNKNOWN'


# Kinds that mean "this key cannot submit right now — try the next one".
KEY_EXHAUST_KINDS = ('AUTH', 'QUOTA', 'NOT_ALLOWED')  # not RATE_LIMIT — that uses cooldown + retry
RATE_LIMIT_MAX_RETRIES = 4
RATE_LIMIT_BASE_DELAY_S = 2


class KeyPool:
    """Shared key list with per-run exhaustion for real quota / expiry / not-allowed.

    429 rate limits use a short cooldown and retry the same key; they do not
    exhaust the key for the whole run. Thread-safe for batch workers. Never
    resubmit a request that already returned a task ID.
    """

    def __init__(self, keys):
        self.keys = list(keys)
        self._lock = threading.Lock()
        self.exhausted = {}  # fingerprint -> reason
        self.cooldowns = {}  # fingerprint -> monotonic deadline

    def fingerprint_of(self, key):
        return fingerprint(key)

    def is_exhausted(self, key):
        with self._lock:
            return fingerprint(key) in self.exhausted

    def mark_exhausted(self, key, reason):
        with self._lock:
            self.exhausted[fingerprint(key)] = reason
            self.cooldowns.pop(fingerprint(key), None)

    def mark_cooldown(self, key, seconds):
        with self._lock:
            self.cooldowns[fingerprint(key)] = time.monotonic() + max(0.1, float(seconds))

    def cooldown_remaining(self, key):
        with self._lock:
            deadline = self.cooldowns.get(fingerprint(key), 0)
        return max(0.0, deadline - time.monotonic())

    def available(self, start_offset=0):
        """Keys still usable this run, rotated from start_offset."""
        if not self.keys:
            return []
        offset = start_offset % len(self.keys)
        ordered = self.keys[offset:] + self.keys[:offset]
        with self._lock:
            return [k for k in ordered if fingerprint(k) not in self.exhausted]

    def all_exhausted(self):
        with self._lock:
            return bool(self.keys) and len(self.exhausted) >= len(self.keys)

    def exhausted_count(self):
        with self._lock:
            return len(self.exhausted)


def payload(prompt, duration=5, resolution='720p', ratio='16:9', media=None, seed='', model=None):
    """Build a provider request for the selected model. Defaults to Wan 3.0."""
    return _models.build_request(model or DEFAULT_MODEL_ID, prompt, duration, resolution, ratio, media, seed)


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
        raise TaskError(t('video_url_invalid'), 'DOWNLOAD')
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
                raise TaskError(t('range_rejected'), 'DOWNLOAD')
            raise TaskError(t('download_http', exc.code), 'DOWNLOAD')
        with response:
            status = response.status
            final_url = response.geturl() if hasattr(response, 'geturl') else url
            final_host = urllib.parse.urlsplit(final_url).hostname or parsed.hostname
            if offset:
                report(t('resume_diag', range_start, final_host, status))
            if offset and status == 200:
                if final_url != url and not _direct_retry:
                    report(t('whole_file_retry'))
                    response.close()
                    return download(final_url, out_path, key, stop, report, timeout, _direct_retry=True)
                raise TaskError(t('range_ignored', final_host, offset / 1048576), 'NO_RESUME')
            content_type = response.headers.get('Content-Type', '').lower()
            if status not in (200, 206) or 'json' in content_type or 'html' in content_type:
                raise TaskError(t('no_video_yet'), 'DOWNLOAD')
            new_etag = response.headers.get('ETag')
            new_modified = response.headers.get('Last-Modified')
            if status == 206:
                content_range = response.headers.get('Content-Range', '')
                match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', content_range.strip(), re.I)
                if not offset or not match or int(match.group(1)) != range_start:
                    raise TaskError(t('range_mismatch'), 'DOWNLOAD')
                same_source = meta.get('source') == source
                if same_source and meta.get('etag') and new_etag and meta['etag'] != new_etag:
                    raise TaskError(t('etag_changed'), 'DOWNLOAD')
                if same_source and not meta.get('etag') and meta.get('last_modified') and new_modified and meta['last_modified'] != new_modified:
                    raise TaskError(t('modified_changed'), 'DOWNLOAD')
                expected_total = int(match.group(3))
                if meta.get('total') is not None and int(meta['total']) != expected_total:
                    raise TaskError(t('size_changed'), 'DOWNLOAD')
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
                    raise TaskError(t('overlap_mismatch'), 'DOWNLOAD')
            first = response.read(64 * 1024)
            if offset == 0 and (len(first) < 12 or first[4:8] != b'ftyp'):
                raise TaskError(t('not_mp4'), 'DOWNLOAD')
            if not first and expected_total and offset < expected_total:
                raise TaskError(t('no_data'), 'NETWORK')
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
                        raise TaskError(t('download_paused'), 'PAUSED')
                    try:
                        chunk = response.read(1024 * 1024)
                    except http.client.IncompleteRead as exc:
                        if exc.partial:
                            f.write(exc.partial)
                            received += len(exc.partial)
                            f.flush()
                        raise TaskError(t('download_dropped', received / 1048576), 'NETWORK')
                    if not chunk:
                        break
                    f.write(chunk)
                    received += len(chunk)
                    f.flush()
                    if time.monotonic() - reported >= 1:
                        report(t('downloading', received / 1048576,
                                 t('download_total', expected_total / 1048576) if expected_total else ''))
                        reported = time.monotonic()
        actual = part.stat().st_size
        if expected_total is not None and actual != expected_total:
            if status == 206:
                # The resumed response ended before its declared byte range. Keep its
                # validator so a later request can safely continue from the new offset.
                meta['range_supported'] = True
                meta['range_check_version'] = range_check_version
                atomic_json(meta_path, meta)
            raise TaskError(t('download_short', actual / 1048576, expected_total / 1048576), 'NETWORK')
        with part.open('rb') as f:
            if f.read(12)[4:8] != b'ftyp':
                raise TaskError(t('mp4_header_failed'), 'DOWNLOAD')
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
        raise TaskError(t('download_interrupted', t('download_kept', actual / 1048576) if actual else ''),
                        'NETWORK') from exc



class Client:
    def __init__(self, report=lambda text: None, stop=None, data_dir=None, on_record=None, context=None, download_slots=None, defer_on_403=False, key_pool=None):
        self.report = report
        self.stop = stop or threading.Event()
        self.data_dir = Path(data_dir) if data_dir else DATA_DIR
        self.record = None
        self.on_record = on_record
        self.context = context or {}
        self.download_slots = download_slots or threading.BoundedSemaphore(1)
        self.last_download_error = ''
        self.defer_on_403 = defer_on_403
        self.key_pool = key_pool

    def save(self, **changes):
        self.record.update(changes)
        atomic_json(self.data_dir / 'tasks' / (self.record['local_id'] + '.json'), self.record)
        if self.on_record:
            self.on_record(dict(self.record))

    def submit(self, keys, request, out_path):
        request = dict(request)
        poll_kind = request.pop('_poll_kind', None)
        model_id = request.get('model') or request.get('model_name') or DEFAULT_MODEL_ID
        if model_id not in MODELS:
            raise TaskError(t('model_unknown', model_id), 'PARAM')
        spec = get_model(model_id)
        if poll_kind is None:
            poll_kind = spec.poll_kind
        if not keys:
            raise TaskError(t('add_api_key'), 'AUTH')
        output = Path(out_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        probe = output.parent / ('.write-test-' + uuid.uuid4().hex)
        try:
            probe.touch()
        finally:
            probe.unlink(missing_ok=True)
        self.record = dict(local_id=uuid.uuid4().hex, task_id='', created=time.strftime('%Y-%m-%d %H:%M:%S'),
                           output=str(Path(out_path).absolute()), state=t('state_preparing'), key_hash='', key_hint='',
                           model=model_id, poll_kind=poll_kind)
        self.record.update(self.context)
        submit_endpoint = _models.submit_url(API_BASE, request)
        pool = self.key_pool or KeyPool(keys)
        # Prefer the caller's order, but skip keys already exhausted in this run.
        candidates = [k for k in keys if not pool.is_exhausted(k)]
        if not candidates:
            raise TaskError(t('all_keys_exhausted'), 'POOL_EXHAUSTED')

        def sleep_interruptible(seconds):
            end = time.monotonic() + max(0.0, seconds)
            while time.monotonic() < end:
                if self.stop.is_set():
                    raise TaskError(t('submit_stopped'), 'PAUSED')
                time.sleep(min(0.25, max(0.0, end - time.monotonic())))

        for index, key in enumerate(candidates):
            rate_tries = 0
            while True:
                if self.stop.is_set():
                    raise TaskError(t('submit_stopped'), 'PAUSED')
                wait_s = pool.cooldown_remaining(key)
                if wait_s > 0:
                    self.report(t('key_rate_limited', key[-4:], int(max(1, round(wait_s)))))
                    sleep_interruptible(wait_s)
                self.save(state=t('state_submitting'), key_hash=fingerprint(key), key_hint='****' + key[-4:],
                          model=model_id, poll_kind=poll_kind)
                self.report(t('submitting_key', index + 1, len(candidates), key[-4:]))
                code, body = api('POST', submit_endpoint, key, request)
                task_id = _models.extract_task_id(body, spec.family)
                kind = category(code or 400, body) if (
                    spec.family == 'kling' and isinstance(body.get('code'), int)
                    and body.get('code') != 0 and not task_id) else None
                if isinstance(task_id, str) and task_id:
                    task_url(task_id, poll_kind)
                    # Persist BEFORE any further work so a restart resumes instead of resubmitting.
                    self.report(t('task_id_log') + task_id)
                    try:
                        self.save(task_id=task_id, state=t('state_submitted'), model=model_id, poll_kind=poll_kind)
                    except OSError:
                        raise TaskError(t('record_save_failed') + task_id, 'STORAGE', task_id)
                    return self.wait(key, task_id, out_path)
                if kind is None:
                    kind = category(code, body)
                # Temporary rate limit: backoff and retry the SAME key (do not exhaust).
                if kind == 'RATE_LIMIT':
                    rate_tries += 1
                    if rate_tries > RATE_LIMIT_MAX_RETRIES:
                        pool.mark_cooldown(key, RATE_LIMIT_BASE_DELAY_S)
                        self.report(t('key_rate_limited', key[-4:], RATE_LIMIT_BASE_DELAY_S))
                        break  # try another key this round; key stays available later
                    delay = min(20, RATE_LIMIT_BASE_DELAY_S ** rate_tries)
                    pool.mark_cooldown(key, delay)
                    self.report(t('key_rate_limited', key[-4:], int(delay)))
                    sleep_interruptible(delay)
                    continue
                # Real exhaustion (quota / expired / not allowed) — try the next key.
                if kind in KEY_EXHAUST_KINDS:
                    pool.mark_exhausted(key, kind)
                    self.save(state=t('state_rejected') + kind)
                    self.report(t('key_exhausted_log', key[-4:], kind))
                    break
                self.save(state=t('state_unknown'))
                raise TaskError(t('submit_uncertain', code), 'UNCERTAIN')
        if pool.all_exhausted():
            raise TaskError(t('all_keys_exhausted'), 'POOL_EXHAUSTED')
        raise TaskError(t('all_rejected'), 'REJECTED')

    def resume(self, key, task_id, out_path, record=None):
        task_url(task_id)
        self.record = dict(record) if record else dict(local_id=uuid.uuid4().hex,
            created=time.strftime('%Y-%m-%d %H:%M:%S'), task_id=task_id)
        self.save(state=t('state_resuming'), output=str(Path(out_path).absolute()), key_hash=fingerprint(key), key_hint='****' + key[-4:])
        return self.wait(key, task_id, out_path)

    def resume_download_url(self, key, task_id, out_path, video_url, record=None):
        task_url(task_id)
        parsed = urllib.parse.urlsplit(video_url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise TaskError(t('video_link_invalid'), 'PARAM', task_id)
        if is_api_url(video_url) and not key:
            raise TaskError(t('api_link_needs_key'), 'AUTH', task_id)
        self.record = dict(record) if record else dict(local_id=uuid.uuid4().hex,
            created=time.strftime('%Y-%m-%d %H:%M:%S'), task_id=task_id)
        changes = dict(state=t('state_from_link'), output=str(Path(out_path).absolute()), download_url=video_url)
        if key:
            changes.update(key_hash=fingerprint(key), key_hint='****' + key[-4:])
        self.save(**changes)
        result = self.try_download(key, video_url, out_path)
        if result == 'ok':
            return self.record
        self.save(state=t('state_download_pending'))
        raise TaskError(self.last_download_error or t('link_unavailable'),
                        'RANGE_UNSUPPORTED' if result == 'blocked' else 'DOWNLOAD', task_id)

    def try_download(self, key, url, out_path):
        acquired = False
        self.last_download_error = ''
        try:
            while not acquired:
                if self.stop.is_set():
                    raise TaskError(t('wait_paused'), 'PAUSED')
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
            self.last_download_error = t('download_error')
            self.report(t('download_error'))
            return 'retry'
        finally:
            if acquired:
                self.download_slots.release()
        self.save(state=t('state_done'), bytes=size, download_source='', download_url='')
        self.report(t('saved_to') + str(out_path))
        return 'ok'

    def wait(self, key, task_id, out_path, wall_timeout=3600, interval=10):
        poll_kind = _models.poll_kind_for_record(self.record)
        family = _models.family_for_record(self.record)
        # Remember model on resume of legacy records that pre-date the model field.
        if not self.record.get('model'):
            self.save(model=DEFAULT_MODEL_ID, poll_kind=poll_kind)
        url = task_url(task_id, poll_kind)
        deadline = time.monotonic() + wall_timeout
        query_errors = 0
        cached_video_url = self.record.get('download_url', '')
        while True:
            if self.stop.is_set():
                self.save(state=t('state_stopped'))
                raise TaskError(t('stopped_cloud'), 'PAUSED', task_id)
            code, body = api('GET', url, key)
            if code == 403 and self.defer_on_403:
                self.save(state=t('state_403_deferred'))
                raise TaskError(t('query_403_deferred'), 'QUERY_FORBIDDEN', task_id)
            status, progress, direct = _models.normalize_status(body, family)
            # Kling wraps errors as HTTP 200 + code != 0
            kling_err = family == 'kling' and isinstance(body.get('code'), int) and body.get('code') != 0 and not status
            expired = time.monotonic() >= deadline
            ready = status in ('succeeded', 'completed', 'success')
            terminal = status in ('failed', 'cancelled', 'canceled')
            query_failed = (code != 200 and not (family == 'kling' and code == 200)) or not status or kling_err
            if family == 'kling' and code == 200 and status:
                query_failed = False
            if query_failed:
                query_errors += 1
                self.report(t('query_failed', code, status or t('no_valid_status'), query_errors))
            else:
                query_errors = 0
                self.report(t('query_ok', code, status,
                              (' · %s%%' % progress) if progress is not None else ''))
            retrying_download = False
            if ready and isinstance(direct, str) and direct.startswith('https://'):
                if cached_video_url != direct:
                    cached_video_url = direct
                    self.save(download_url=direct)
            if ready or code == 403 or expired or terminal or (query_failed and (cached_video_url or query_errors >= 3)):
                candidates = []
                if cached_video_url:
                    candidates.append(cached_video_url)
                if family != 'kling':
                    content_url = url + '/content'
                    if (ready or code == 403 or expired or terminal or query_errors >= 3) and content_url not in candidates:
                        candidates.append(content_url)
                # Keep resuming the same endpoint after a dropped connection.
                saved_source = partial_download_source(out_path)
                if saved_source:
                    candidates.sort(key=lambda item: download_source(item) != saved_source)
                for download_url in candidates:
                    self.report(t('resume_download') if partial_download_source(out_path) else t('start_download'))
                    outcome = self.try_download(key, download_url, out_path)
                    if outcome == 'ok':
                        return self.record
                    if outcome == 'blocked':
                        self.save(state=t('state_no_resume'), download_source=download_source(download_url))
                        raise TaskError(self.last_download_error or t('no_resume'), 'RANGE_UNSUPPORTED', task_id)
                    if outcome == 'retry':
                        self.save(state=t('state_retry_later'), download_source=download_source(download_url))
                        retrying_download = True
                        break
                    # A definitive refusal can use the API content fallback. Do not
                    # switch endpoints after a network interruption: keep the partial.
            if terminal:
                self.save(state=t('state_failed_server'))
                raise TaskError(t('server_failed', status), 'FAILED', task_id)
            if expired:
                self.save(state=t('state_interrupted_resume') if retrying_download else t('state_timeout'))
                raise TaskError(t('timeout'), 'TIMEOUT', task_id)
            if ready:
                self.save(state=t('state_interrupted_retry') if retrying_download else t('state_ready_retry'))
                if not retrying_download:
                    self.report(t('ready_retry'))
            if query_failed and query_errors >= 6:
                self.save(state=t('state_query_down'))
                raise TaskError(t('query_down', query_errors, code), 'QUERY_UNAVAILABLE', task_id)
            delay = min(60, interval * (2 ** min(query_errors - 1, 3))) if query_failed else interval
            if query_failed:
                self.report(t('next_query', delay))
            self.stop.wait(min(delay, max(0, deadline - time.monotonic())))

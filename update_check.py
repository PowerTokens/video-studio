"""In-app update check against the official GitHub Releases page.

No Tk dependency: the desktop app calls these helpers from a background thread
and shows a non-blocking banner when a newer release is available.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

from wan_core import APP_VERSION, DATA_DIR, USER_AGENT, atomic_json

LATEST_URL = 'https://api.github.com/repos/PowerTokens/video-studio/releases/latest'
PUBLIC_DOWNLOAD_PAGE = 'https://powertokens.github.io/video-studio-web/download.html'
STATE_NAME = 'update_check.json'
CHECK_WINDOW = timedelta(hours=24)
DEFAULT_TIMEOUT = 8
MAX_ASSET_BYTES = 200 * 1024 * 1024
WIN_ASSET = 'PowerTokensVideoStudio.exe'
MAC_ASSET = 'PowerTokensVideoStudio-macOS-arm64.dmg'
ALLOWED_HOSTS = frozenset({
    'github.com',
    'objects.githubusercontent.com',
    'release-assets.githubusercontent.com',
})
_SHA256_HEX = re.compile(r'\b([a-fA-F0-9]{64})\b')
_LABELED_SHA = re.compile(
    r'SHA-?256(?:\s*\(\s*(\.[A-Za-z0-9]+)\s*\))?\s*:\s*`?([a-fA-F0-9]{64})`?',
    re.IGNORECASE,
)
_VERSION_LEAD = re.compile(r'^v?', re.IGNORECASE)
_VERSION_NUM = re.compile(r'(\d+(?:\.\d+)*)')


class UpdateError(Exception):
    """Raised when a user-started download cannot be completed safely."""

    def __init__(self, message, code='network'):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ReleaseInfo:
    tag: str
    version: str
    asset_name: str | None
    asset_url: str | None
    sha256: str | None
    html_url: str | None

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        if not data:
            return None
        tag = str(data.get('tag') or '').strip()
        if not tag:
            return None
        version = str(data.get('version') or tag).strip()
        return cls(
            tag=tag,
            version=version,
            asset_name=_blank_to_none(data.get('asset_name')),
            asset_url=_blank_to_none(data.get('asset_url')),
            sha256=_blank_to_none(data.get('sha256')),
            html_url=_blank_to_none(data.get('html_url')),
        )


def _blank_to_none(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_version(text):
    """Return a numeric version tuple, or None if `text` has no leading version."""
    raw = str(text or '').strip()
    if not raw:
        return None
    raw = _VERSION_LEAD.sub('', raw, count=1)
    match = _VERSION_NUM.match(raw)
    if not match:
        return None
    parts = [int(part) for part in match.group(1).split('.')]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer(latest, current):
    """True when `latest` is a higher numeric version than `current`.

    `1.17` equals `v1.17.0`. Prerelease suffixes after the numeric part are ignored.
    Unparseable values are not newer.
    """
    left = parse_version(latest)
    right = parse_version(current)
    if left is None or right is None:
        return False
    length = max(len(left), len(right))
    left = left + (0,) * (length - len(left))
    right = right + (0,) * (length - len(right))
    return left > right


def pick_asset(assets, platform):
    """Return the official asset dict for this platform, or None.

    Windows uses the EXE; macOS uses the arm64 DMG (not the zip). Linux has no official package.
    """
    if platform == 'win32':
        wanted = WIN_ASSET
    elif platform == 'darwin':
        wanted = MAC_ASSET
    else:
        return None
    for asset in assets or []:
        if not isinstance(asset, dict):
            continue
        if str(asset.get('name') or '').strip() == wanted:
            return asset
    return None


def parse_sha256(body, asset_name):
    """Parse a SHA-256 hex digest for `asset_name` from a GitHub release body."""
    text = body or ''
    if not text:
        return None
    name = (asset_name or '').strip()
    ext = Path(name).suffix.lower() if name else ''

    # Prefer a labeled hash whose extension matches the asset (e.g. SHA-256 (.dmg): `…`).
    if ext:
        for match in _LABELED_SHA.finditer(text):
            label_ext = (match.group(1) or '').lower()
            if label_ext == ext:
                return match.group(2).lower()

    if name:
        lines = text.splitlines()
        for index, line in enumerate(lines):
            if name not in line:
                continue
            window = '\n'.join(lines[index:index + 6])
            # If this window mentions another official asset later, stop before that hash
            # only when a labeled hash for our asset / unlabeled hash is found first.
            same = _LABELED_SHA.search(window)
            if same:
                label_ext = (same.group(1) or '').lower()
                if not label_ext or label_ext == ext:
                    return same.group(2).lower()
            digest = _SHA256_HEX.search(window)
            if digest:
                # Avoid picking a following file's hash when the next line names a different asset.
                return digest.group(1).lower()

    # A single unlabeled SHA-256 in the whole body can still be used when the asset is named nearby.
    labeled = list(_LABELED_SHA.finditer(text))
    if len(labeled) == 1 and not labeled[0].group(1) and name and name in text:
        return labeled[0].group(2).lower()
    return None


def is_allowed_download_url(url):
    """Official GitHub HTTPS download hosts only; no credentials in the URL."""
    try:
        parsed = urllib.parse.urlsplit(url)
    except Exception:
        return False
    if parsed.scheme != 'https':
        return False
    host = (parsed.hostname or '').lower().rstrip('.')
    if not host or parsed.username or parsed.password:
        return False
    if host in ALLOWED_HOSTS:
        return True
    return host.endswith('.githubusercontent.com') and host != 'githubusercontent.com'


def _now():
    return datetime.now(timezone.utc)


def parse_time(value):
    if value is None or value == '':
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value).strip().replace('Z', '+00:00')
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def state_path(data_dir=None):
    return Path(data_dir) / STATE_NAME if data_dir else DATA_DIR / STATE_NAME


def load_state(data_dir=None):
    path = state_path(data_dir)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(state, data_dir=None):
    path = state_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(path, state or {})


def should_check(state, now=None, window=None):
    """True when no successful/attempted check was recorded in the last 24 hours."""
    now = now or _now()
    window = CHECK_WINDOW if window is None else window
    last = parse_time((state or {}).get('last_check_at'))
    if last is None:
        return True
    return now - last >= window


def should_prompt(state, tag, now=None, window=None):
    """True unless this tag was dismissed and 24 hours have not yet passed.

    An even newer tag than the dismissed one may be shown immediately.
    """
    if not tag:
        return False
    now = now or _now()
    window = CHECK_WINDOW if window is None else window
    dismissed = str((state or {}).get('dismissed_tag') or '').strip()
    if not dismissed:
        return True
    if is_newer(tag, dismissed):
        return True
    if parse_version(tag) != parse_version(dismissed):
        # Different but not newer (shouldn't happen for latest); don't re-prompt.
        return False
    dismissed_at = parse_time((state or {}).get('dismissed_at')) or parse_time((state or {}).get('last_check_at'))
    if dismissed_at is None:
        return False
    return now - dismissed_at >= window


def mark_checked(state=None, data_dir=None, now=None, release=None, persist=True):
    current = dict(state or load_state(data_dir))
    current['last_check_at'] = (now or _now()).isoformat()
    if release is not None:
        current['last_release'] = release.to_dict() if isinstance(release, ReleaseInfo) else release
    if persist:
        save_state(current, data_dir)
    return current


def mark_dismissed(tag, state=None, data_dir=None, now=None, persist=True):
    current = dict(state or load_state(data_dir))
    current['dismissed_tag'] = str(tag or '').strip()
    current['dismissed_at'] = (now or _now()).isoformat()
    if persist:
        save_state(current, data_dir)
    return current


def cached_release(state=None, data_dir=None):
    current = state if state is not None else load_state(data_dir)
    return ReleaseInfo.from_dict((current or {}).get('last_release'))


def skip_update_check():
    return os.environ.get('VS_SKIP_UPDATE_CHECK') == '1'


def use_update_fixture():
    return os.environ.get('VS_UPDATE_FIXTURE') == '1'


def fixture_release(platform=None, version='1.18.0'):
    """A fake newer release used by `VS_UPDATE_FIXTURE=1` for previews and tests."""
    plat = sys.platform if platform is None else platform
    tag = version if str(version).lower().startswith('v') else 'v' + str(version)
    parsed = parse_version(tag)
    display = '.'.join(str(part) for part in (parsed or (1, 18, 0)))
    name, url = None, None
    if plat == 'win32':
        name = WIN_ASSET
    elif plat == 'darwin':
        name = MAC_ASSET
    if name:
        url = 'https://github.com/PowerTokens/video-studio/releases/download/%s/%s' % (tag, name)
    return ReleaseInfo(
        tag=tag,
        version=display,
        asset_name=name,
        asset_url=url,
        sha256=None,
        html_url='https://github.com/PowerTokens/video-studio/releases/tag/%s' % tag,
    )


def _request(url, timeout=DEFAULT_TIMEOUT, urlopen=None, headers=None):
    opener = urlopen or urllib.request.urlopen
    req = urllib.request.Request(url, headers=headers or {
        'User-Agent': USER_AGENT,
        'Accept': 'application/vnd.github+json',
    })
    return opener(req, timeout=timeout)


def fetch_latest(current_version, platform, timeout=DEFAULT_TIMEOUT, urlopen=None, url=None):
    """Return a ReleaseInfo when GitHub has a newer tag, else None.

    Failures (HTTP errors, timeouts, bad JSON) are silent: the function returns None.
    A newer tag with no official asset for this platform still returns info with
    `asset_url=None` so the UI can offer the public download page.
    """
    target = url or LATEST_URL
    try:
        with _request(target, timeout=timeout, urlopen=urlopen) as response:
            raw = response.read()
        payload = json.loads(raw.decode('utf-8'))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        return None
    except Exception:
        # socket.timeout on older Python, or other unexpected parse issues: stay silent.
        return None

    if not isinstance(payload, dict):
        return None
    tag = str(payload.get('tag_name') or payload.get('tag') or '').strip()
    if not tag or not is_newer(tag, current_version or APP_VERSION):
        return None
    parsed = parse_version(tag)
    version = '.'.join(str(part) for part in parsed) if parsed else tag.lstrip('vV')
    assets = payload.get('assets') or []
    asset = pick_asset(assets, platform)
    name = url_ = sha = None
    body = payload.get('body') or ''
    if asset:
        name = str(asset.get('name') or '').strip() or None
        url_ = str(asset.get('browser_download_url') or asset.get('url') or '').strip() or None
        if url_ and not is_allowed_download_url(url_):
            url_ = None
        sha = parse_sha256(body, name) if name else None
    html_url = str(payload.get('html_url') or '').strip() or None
    return ReleaseInfo(
        tag=tag,
        version=version,
        asset_name=name,
        asset_url=url_,
        sha256=sha,
        html_url=html_url,
    )


class _HostGuard(urllib.request.HTTPRedirectHandler):
    """Refuse redirects that leave the official GitHub download hosts."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not is_allowed_download_url(newurl):
            raise UpdateError('redirect left the official download hosts', 'url')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download_opener(urlopen=None):
    if urlopen is not None:
        return urlopen
    return urllib.request.build_opener(_HostGuard()).open


def download_asset(url, dest, sha256=None, timeout=DEFAULT_TIMEOUT, progress=None, stop=None, urlopen=None,
                   max_bytes=MAX_ASSET_BYTES):
    """Download an official asset to `dest`, optionally verifying SHA-256.

    Progress is reported as `progress(received, total_or_None)`. `stop` is a
    threading.Event (or any object with `is_set`). Returns the destination Path.
    """
    if not is_allowed_download_url(url):
        raise UpdateError('download URL is not an official GitHub asset', 'url')
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + '.part')
    opener = _download_opener(urlopen)
    req = urllib.request.Request(url, headers={
        'User-Agent': USER_AGENT,
        'Accept': 'application/octet-stream',
        'Accept-Encoding': 'identity',
    })
    digest = hashlib.sha256()
    received = 0
    expected = None
    try:
        try:
            response = opener(req, timeout=timeout)
        except UpdateError:
            raise
        except urllib.error.HTTPError as exc:
            raise UpdateError('download failed (HTTP %s)' % exc.code, 'http') from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise UpdateError('download failed', 'network') from exc
        with response:
            final_url = response.geturl() if hasattr(response, 'geturl') else url
            if final_url and not is_allowed_download_url(final_url):
                raise UpdateError('redirect left the official download hosts', 'url')
            length = None
            headers = getattr(response, 'headers', None)
            if headers is not None:
                length = headers.get('Content-Length')
            if length and str(length).isdigit():
                expected = int(length)
                if expected > max_bytes:
                    raise UpdateError('asset is larger than the size cap', 'size')
            if progress:
                progress(0, expected)
            with part.open('wb') as handle:
                while True:
                    if stop is not None and getattr(stop, 'is_set', lambda: False)():
                        raise UpdateError('download cancelled', 'cancelled')
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > max_bytes:
                        raise UpdateError('asset is larger than the size cap', 'size')
                    digest.update(chunk)
                    handle.write(chunk)
                    if progress:
                        progress(received, expected)
        if expected is not None and received != expected:
            raise UpdateError('download ended before the file was complete', 'network')
        if received <= 0:
            raise UpdateError('download returned no data', 'network')
        actual = digest.hexdigest()
        if sha256:
            expected_sha = str(sha256).strip().lower()
            if expected_sha and actual != expected_sha:
                raise UpdateError('sha256 mismatch', 'hash')
        part.replace(dest)
        return dest
    except Exception:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def downloads_dir(data_dir=None, home=None):
    """Prefer the user's Downloads folder; fall back to the app data `updates` dir."""
    base = Path.home() if home is None else Path(home)
    folder = base / 'Downloads'
    if folder.is_dir():
        return folder
    target = Path(data_dir) / 'updates' if data_dir else DATA_DIR / 'updates'
    target.mkdir(parents=True, exist_ok=True)
    return target


def destination_path(folder, asset_name, version):
    """Pick a path that will not overwrite a file already in the Downloads folder."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    name = asset_name or 'PowerTokensVideoStudio.bin'
    primary = folder / name
    if not primary.exists():
        return primary
    stem, suffix = Path(name).stem, Path(name).suffix
    numeric = parse_version(version)
    label = '.'.join(str(part) for part in numeric) if numeric else str(version or 'update').strip()
    versioned = folder / ('%s-%s%s' % (stem, label, suffix))
    if not versioned.exists():
        return versioned
    index = 2
    while True:
        candidate = folder / ('%s-%s-%d%s' % (stem, label, index, suffix))
        if not candidate.exists():
            return candidate
        index += 1


def launch_package(path, platform, runner=None):
    """Start the downloaded EXE (Windows) or open the DMG (macOS)."""
    target = Path(path)
    if not target.is_file():
        raise UpdateError('downloaded file is missing', 'launch')
    if runner is not None:
        return runner(target, platform)
    if platform == 'win32':
        os.startfile(str(target))  # noqa: S606 — launch a just-downloaded official installer
        return
    if platform == 'darwin':
        import subprocess
        subprocess.Popen(['open', str(target)])
        return
    raise UpdateError('no official package to launch on this platform', 'launch')


def format_bytes(count):
    """Human-readable size for the download progress line, e.g. '12 MB'."""
    if count is None:
        return None
    value = float(count)
    mb = value / (1024 * 1024)
    if mb < 0.1:
        kb = value / 1024
        if kb < 1:
            return '%d B' % int(value)
        return '%.0f KB' % kb
    if mb < 10:
        return '%.1f MB' % mb
    return '%d MB' % round(mb)


def format_progress(received, total=None):
    if total:
        return '%s / %s' % (format_bytes(received), format_bytes(total))
    return format_bytes(received) or '0 B'


def offer_page_url(info):
    """URL opened when there is no official asset to download in-app."""
    if info is not None and getattr(info, 'html_url', None):
        # Prefer the stable public download page; the release page is the fallback
        # only when the public page is inappropriate. Product rule: use the public page.
        pass
    return PUBLIC_DOWNLOAD_PAGE


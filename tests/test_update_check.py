"""Unit tests for the in-app GitHub update check. No display required."""
import i18n
i18n.set_language('zh')

from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

import update_check
from update_check import (
    PUBLIC_DOWNLOAD_PAGE, ReleaseInfo, UpdateError, cached_release, destination_path,
    download_asset, downloads_dir, fetch_latest, fixture_release, format_progress,
    is_allowed_download_url, is_newer, load_state, mark_checked, mark_dismissed,
    parse_sha256, parse_version, pick_asset, should_check, should_prompt,
)


WIN_EXE = 'PowerTokensVideoStudio.exe'
MAC_DMG = 'PowerTokensVideoStudio-macOS-arm64.dmg'
MAC_ZIP = 'PowerTokensVideoStudio-macOS-arm64.app.zip'
EXE_SHA = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
DMG_SHA = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
ZIP_SHA = 'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc'

# Mirrors the v1.17.0 GitHub release-notes layout (Windows SHA-256 on the
# following line; macOS hashes labeled by extension). Hashes here are fixtures.
V117_STYLE_BODY = """Windows desktop and unsigned Apple Silicon Mac Beta, v1.18.0.

**Windows:** [PowerTokensVideoStudio.exe](https://github.com/PowerTokens/video-studio/releases/download/v1.18.0/PowerTokensVideoStudio.exe)  
SHA-256: `{exe}`

**macOS (Apple Silicon, unsigned Beta):** [PowerTokensVideoStudio-macOS-arm64.dmg](https://github.com/PowerTokens/video-studio/releases/download/v1.18.0/PowerTokensVideoStudio-macOS-arm64.dmg) · [PowerTokensVideoStudio-macOS-arm64.app.zip](https://github.com/PowerTokens/video-studio/releases/download/v1.18.0/PowerTokensVideoStudio-macOS-arm64.app.zip)  
SHA-256 (.dmg): `{dmg}`  
SHA-256 (.zip): `{zip}`

The Mac package is not signed or notarized, and the window title includes **Beta**. The first time you open a downloaded copy, right-click `PowerTokensVideoStudio.app` and choose **Open**, or go to **System Settings → Privacy & Security** and click **Open Anyway**.
""".format(exe=EXE_SHA, dmg=DMG_SHA, zip=ZIP_SHA)

OFFICIAL_EXE_URL = 'https://github.com/PowerTokens/video-studio/releases/download/v1.18.0/' + WIN_EXE
OFFICIAL_DMG_URL = 'https://github.com/PowerTokens/video-studio/releases/download/v1.18.0/' + MAC_DMG


def _assets():
    return [
        {'name': WIN_EXE, 'browser_download_url': OFFICIAL_EXE_URL},
        {'name': MAC_DMG, 'browser_download_url': OFFICIAL_DMG_URL},
        {'name': MAC_ZIP, 'browser_download_url': OFFICIAL_DMG_URL.replace('.dmg', '.app.zip')},
    ]


def _payload(tag='v1.18.0', body=V117_STYLE_BODY, assets=None):
    return {
        'tag_name': tag,
        'html_url': 'https://github.com/PowerTokens/video-studio/releases/tag/' + tag,
        'body': body,
        'assets': _assets() if assets is None else assets,
    }


class FakeResponse:
    def __init__(self, data, url, headers=None, status=200):
        if isinstance(data, str):
            data = data.encode('utf-8')
        self._buf = io.BytesIO(data)
        self._url = url
        self.status = status
        self.headers = headers or {}

    def read(self, n=-1):
        return self._buf.read(n)

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class VersionTests(unittest.TestCase):
    def test_1_17_equals_v1_17_0(self):
        self.assertEqual(parse_version('1.17'), (1, 17, 0))
        self.assertEqual(parse_version('v1.17.0'), (1, 17, 0))
        self.assertFalse(is_newer('v1.17.0', '1.17'))
        self.assertFalse(is_newer('1.17', 'v1.17.0'))

    def test_newer_patch_and_minor(self):
        self.assertTrue(is_newer('v1.18.0', '1.17'))
        self.assertTrue(is_newer('v1.17.1', '1.17'))
        self.assertFalse(is_newer('1.17', 'v1.18.0'))

    def test_prerelease_suffix_ignored(self):
        self.assertFalse(is_newer('v1.17.0-beta', '1.17'))
        self.assertTrue(is_newer('v1.18.0-rc.1', '1.17'))

    def test_junk_is_not_newer(self):
        self.assertFalse(is_newer('latest', '1.17'))
        self.assertFalse(is_newer('', '1.17'))
        self.assertFalse(is_newer(None, '1.17'))
        self.assertFalse(is_newer('v1.18.0', 'not-a-version'))
        self.assertIsNone(parse_version('???'))


class AssetAndUrlTests(unittest.TestCase):
    def test_pick_asset_windows_and_macos(self):
        assets = _assets()
        self.assertEqual(pick_asset(assets, 'win32')['name'], WIN_EXE)
        self.assertEqual(pick_asset(assets, 'darwin')['name'], MAC_DMG)
        self.assertIsNone(pick_asset(assets, 'linux'))
        self.assertIsNone(pick_asset(assets, 'freebsd'))

    def test_linux_has_no_official_asset(self):
        self.assertIsNone(pick_asset(_assets(), 'linux'))

    def test_allowed_github_https_urls(self):
        self.assertTrue(is_allowed_download_url(OFFICIAL_EXE_URL))
        self.assertTrue(is_allowed_download_url(
            'https://objects.githubusercontent.com/github-production-release-asset-2e65be/123/abc'))
        self.assertTrue(is_allowed_download_url(
            'https://release-assets.githubusercontent.com/github-production-release-asset/1'))
        self.assertTrue(is_allowed_download_url(
            'https://github-releases.githubusercontent.com/1234/file.exe'))

    def test_rejects_http_and_off_host_urls(self):
        self.assertFalse(is_allowed_download_url(OFFICIAL_EXE_URL.replace('https://', 'http://')))
        self.assertFalse(is_allowed_download_url('https://evil.example/PowerTokensVideoStudio.exe'))
        self.assertFalse(is_allowed_download_url('https://github.com.evil.example/file.exe'))
        self.assertFalse(is_allowed_download_url('file:///tmp/PowerTokensVideoStudio.exe'))
        self.assertFalse(is_allowed_download_url(
            'https://user:pass@github.com/PowerTokens/video-studio/releases/download/v1.18.0/' + WIN_EXE))


class ShaParserTests(unittest.TestCase):
    def test_parses_v1_17_style_release_body(self):
        self.assertEqual(parse_sha256(V117_STYLE_BODY, WIN_EXE), EXE_SHA)
        self.assertEqual(parse_sha256(V117_STYLE_BODY, MAC_DMG), DMG_SHA)
        self.assertEqual(parse_sha256(V117_STYLE_BODY, MAC_ZIP), ZIP_SHA)

    def test_missing_hash_returns_none(self):
        self.assertIsNone(parse_sha256('No checksums here.', WIN_EXE))
        self.assertIsNone(parse_sha256('', WIN_EXE))


class FakeUrlOpen:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def __call__(self, req, timeout=None):
        self.calls.append((req, timeout))
        if self.error is not None:
            raise self.error
        return self.response


class FetchLatestTests(unittest.TestCase):
    def test_newer_release_returns_info_for_windows(self):
        payload = json.dumps(_payload())
        opener = FakeUrlOpen(FakeResponse(payload, update_check.LATEST_URL))
        info = fetch_latest('1.17', 'win32', urlopen=opener)
        self.assertIsInstance(info, ReleaseInfo)
        self.assertEqual(info.tag, 'v1.18.0')
        self.assertEqual(info.version, '1.18.0')
        self.assertEqual(info.asset_name, WIN_EXE)
        self.assertEqual(info.asset_url, OFFICIAL_EXE_URL)
        self.assertEqual(info.sha256, EXE_SHA)
        self.assertEqual(opener.calls[0][1], update_check.DEFAULT_TIMEOUT)
        ua = opener.calls[0][0].headers.get('User-agent') or opener.calls[0][0].get_header('User-agent')
        # urllib stores User-Agent on the Request.
        self.assertIn('PowerTokensVideoStudio', opener.calls[0][0].header_items() and
                      dict(opener.calls[0][0].header_items()).get('User-agent', '') or
                      opener.calls[0][0].get_header('User-agent') or '')

    def test_newer_release_picks_dmg_on_macos(self):
        opener = FakeUrlOpen(FakeResponse(json.dumps(_payload()), update_check.LATEST_URL))
        info = fetch_latest('1.17', 'darwin', urlopen=opener)
        self.assertEqual(info.asset_name, MAC_DMG)
        self.assertEqual(info.sha256, DMG_SHA)
        self.assertTrue(info.asset_url.endswith('.dmg'))

    def test_same_version_returns_none(self):
        opener = FakeUrlOpen(FakeResponse(json.dumps(_payload('v1.17.0')), update_check.LATEST_URL))
        self.assertIsNone(fetch_latest('1.17', 'win32', urlopen=opener))

    def test_http_error_is_silent(self):
        error = urllib.error.HTTPError(update_check.LATEST_URL, 500, 'boom', hdrs=None, fp=io.BytesIO())
        opener = FakeUrlOpen(error=error)
        self.assertIsNone(fetch_latest('1.17', 'win32', urlopen=opener))

    def test_timeout_is_silent(self):
        opener = FakeUrlOpen(error=TimeoutError('timed out'))
        self.assertIsNone(fetch_latest('1.17', 'win32', urlopen=opener))
        opener = FakeUrlOpen(error=urllib.error.URLError('timed out'))
        self.assertIsNone(fetch_latest('1.17', 'win32', urlopen=opener))

    def test_linux_newer_tag_without_asset_still_returns_offer(self):
        opener = FakeUrlOpen(FakeResponse(json.dumps(_payload()), update_check.LATEST_URL))
        info = fetch_latest('1.17', 'linux', urlopen=opener)
        self.assertIsInstance(info, ReleaseInfo)
        self.assertEqual(info.tag, 'v1.18.0')
        self.assertIsNone(info.asset_url)
        self.assertIsNone(info.asset_name)
        self.assertTrue(info.html_url)


class StateTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.data = Path(self.folder.name)

    def test_should_check_24h_window(self):
        now = datetime(2026, 11, 12, 4, 0, tzinfo=timezone.utc)
        self.assertTrue(should_check({}, now=now))
        state = mark_checked({}, data_dir=self.data, now=now, persist=True)
        self.assertFalse(should_check(state, now=now + timedelta(hours=23, minutes=59)))
        self.assertTrue(should_check(state, now=now + timedelta(hours=24)))
        self.assertTrue(Path(self.data, 'update_check.json').is_file())

    def test_later_dismiss_hides_same_tag_for_24h(self):
        now = datetime(2026, 11, 12, 4, 0, tzinfo=timezone.utc)
        state = mark_dismissed('v1.18.0', {}, data_dir=self.data, now=now)
        self.assertFalse(should_prompt(state, 'v1.18.0', now=now + timedelta(hours=1)))
        self.assertTrue(should_prompt(state, 'v1.18.0', now=now + timedelta(hours=24)))

    def test_newer_than_dismissed_tag_reappears(self):
        now = datetime(2026, 11, 12, 4, 0, tzinfo=timezone.utc)
        state = mark_dismissed('v1.18.0', {}, data_dir=self.data, now=now)
        self.assertTrue(should_prompt(state, 'v1.19.0', now=now + timedelta(minutes=5)))
        self.assertFalse(should_prompt(state, 'v1.18.0', now=now + timedelta(minutes=5)))

    def test_cached_release_roundtrip(self):
        info = fixture_release(platform='win32', version='1.18.0')
        state = mark_checked({}, data_dir=self.data, release=info)
        cached = cached_release(state)
        self.assertEqual(cached.tag, 'v1.18.0')
        self.assertEqual(cached.asset_name, WIN_EXE)


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.dest = Path(self.folder.name) / WIN_EXE

    def test_writes_bytes_and_verifies_sha256(self):
        payload = b'official-installer-bytes'
        digest = hashlib.sha256(payload).hexdigest()
        opener = FakeUrlOpen(FakeResponse(
            payload, OFFICIAL_EXE_URL, headers={'Content-Length': str(len(payload))},
        ))
        seen = []
        path = download_asset(OFFICIAL_EXE_URL, self.dest, sha256=digest, urlopen=opener,
                              progress=lambda got, total: seen.append((got, total)))
        self.assertEqual(path.read_bytes(), payload)
        self.assertTrue(seen)
        self.assertEqual(seen[-1], (len(payload), len(payload)))

    def test_rejects_bad_sha256(self):
        payload = b'official-installer-bytes'
        opener = FakeUrlOpen(FakeResponse(payload, OFFICIAL_EXE_URL))
        with self.assertRaises(UpdateError) as raised:
            download_asset(OFFICIAL_EXE_URL, self.dest, sha256='0' * 64, urlopen=opener)
        self.assertEqual(raised.exception.code, 'hash')
        self.assertFalse(self.dest.exists())

    def test_rejects_bad_url(self):
        with self.assertRaises(UpdateError) as raised:
            download_asset('https://evil.example/installer.exe', self.dest)
        self.assertEqual(raised.exception.code, 'url')
        with self.assertRaises(UpdateError):
            download_asset(OFFICIAL_EXE_URL.replace('https://', 'http://'), self.dest)

    def test_rejects_oversize_content_length(self):
        opener = FakeUrlOpen(FakeResponse(
            b'x', OFFICIAL_EXE_URL, headers={'Content-Length': str(201 * 1024 * 1024)},
        ))
        with self.assertRaises(UpdateError) as raised:
            download_asset(OFFICIAL_EXE_URL, self.dest, urlopen=opener, max_bytes=200 * 1024 * 1024)
        self.assertEqual(raised.exception.code, 'size')

    def test_rejects_stream_that_exceeds_cap(self):
        payload = b'x' * 64
        opener = FakeUrlOpen(FakeResponse(payload, OFFICIAL_EXE_URL))
        with self.assertRaises(UpdateError) as raised:
            download_asset(OFFICIAL_EXE_URL, self.dest, urlopen=opener, max_bytes=32)
        self.assertEqual(raised.exception.code, 'size')
        self.assertFalse(self.dest.exists())

    def test_destination_uses_versioned_name_when_taken(self):
        folder = Path(self.folder.name)
        taken = folder / WIN_EXE
        taken.write_bytes(b'old')
        path = destination_path(folder, WIN_EXE, '1.18.0')
        self.assertEqual(path.name, 'PowerTokensVideoStudio-1.18.0.exe')
        path.write_bytes(b'also-taken')
        again = destination_path(folder, WIN_EXE, '1.18.0')
        self.assertEqual(again.name, 'PowerTokensVideoStudio-1.18.0-2.exe')

    def test_downloads_dir_prefers_existing_downloads(self):
        home = Path(self.folder.name) / 'home'
        downloads = home / 'Downloads'
        downloads.mkdir(parents=True)
        self.assertEqual(downloads_dir(data_dir=self.folder.name, home=home), downloads)
        fallback_home = Path(self.folder.name) / 'no-downloads-home'
        fallback_home.mkdir()
        fallback = downloads_dir(data_dir=Path(self.folder.name) / 'data', home=fallback_home)
        self.assertEqual(fallback, Path(self.folder.name) / 'data' / 'updates')
        self.assertTrue(fallback.is_dir())


class I18nTests(unittest.TestCase):
    KEYS = (
        'update_available', 'update_download', 'update_later', 'update_downloading',
        'update_ready_win', 'update_quit_and_open', 'update_ready_mac', 'update_open_installer',
        'update_reveal', 'update_fail', 'update_retry', 'update_open_page', 'update_hash_fail',
    )

    def test_keys_exist_in_zh_and_en(self):
        for key in self.KEYS:
            self.assertIn(key, i18n.ZH)
            self.assertIn(key, i18n.EN)

    def test_chinese_copy_is_used_not_raw_english(self):
        import re
        han = re.compile(r'[\u4e00-\u9fff]')
        for key in self.KEYS:
            self.assertRegex(i18n.ZH[key], han, key)
            self.assertIsNone(han.search(i18n.EN[key]), key)
        i18n.set_language('zh')
        self.addCleanup(i18n.set_language, 'zh')
        self.assertIn('新版本', i18n.t('update_available', '1.18.0', '1.17'))
        self.assertEqual(i18n.t('update_later'), '稍后')
        self.assertEqual(i18n.t('update_download'), '下载更新')
        i18n.set_language('en')
        self.assertIn('Version 1.18.0 is available', i18n.t('update_available', '1.18.0', '1.17'))
        self.assertEqual(i18n.t('update_later'), 'Later')

    def test_spanish_and_portuguese_overlays_cover_banner_chrome(self):
        for key in ('update_download', 'update_later', 'update_retry'):
            self.assertIn(key, i18n.ES_OVERLAY)
            self.assertIn(key, i18n.PT_OVERLAY)


class AppHookTests(unittest.TestCase):
    def test_app_module_uses_i18n_keys_for_banner_copy(self):
        source = Path(__file__).resolve().parent.parent.joinpath('app.py').read_text(encoding='utf-8')
        self.assertIn("t('update_available'", source)
        self.assertIn("t('update_download')", source)
        self.assertIn("t('update_later')", source)
        self.assertIn('root.after(800, self._start_update_check)', source)
        self.assertIn("kind.startswith('update_')", source)
        # Must handle update events before the generate-log fallback in drain().
        drain = source[source.find('def drain(self):'):]
        available = drain.find("if kind == 'update_available':")
        unknown = drain.find("kind.startswith('update_')")
        fallback = drain.find("self.log.configure(state='normal')")
        self.assertGreater(available, 0)
        self.assertGreater(unknown, available)
        self.assertGreater(fallback, unknown)

    def test_unknown_update_events_are_dropped_before_the_log(self):
        source = Path(__file__).resolve().parent.parent.joinpath('app.py').read_text(encoding='utf-8')
        drain = source[source.find('def drain(self):'):source.find('def refresh_history(self):')]
        # The generate log is only reached after every update_* kind has been skipped.
        prefix, _, rest = drain.partition("self.log.configure(state='normal')")
        self.assertIn("kind.startswith('update_')", prefix)
        self.assertIn("continue", prefix)
        self.assertTrue(rest)

    def test_fixture_helper_skips_the_network(self):
        info = fixture_release(platform='win32')
        self.assertEqual(info.tag, 'v1.18.0')
        self.assertTrue(is_newer(info.tag, '1.17'))
        self.assertTrue(info.asset_url.startswith('https://github.com/'))


class ProgressFormatTests(unittest.TestCase):
    def test_progress_line(self):
        self.assertEqual(format_progress(12 * 1024 * 1024, 45 * 1024 * 1024), '12 MB / 45 MB')


if __name__ == '__main__':
    unittest.main()

"""First/last-touch UTM for Video Studio desktop.

Backend attribution uses UTM only. In-app defaults:
  utm_source=videostudio&utm_medium=app&utm_campaign=video-studio
GitHub README/Release copy keeps github + oss|readme (not stored here).

Persist file: DATA_DIR / utm.json  →  { "first": {...}, "last": {...} }
Optional env capture (updates last, and first if empty):
  POWERTTOKENS_UTM=utm_source=...&utm_medium=...
  or POWERTTOKENS_UTM_SOURCE / _MEDIUM / _CAMPAIGN / _CONTENT / _TERM
"""
from __future__ import annotations

import json
import os
import urllib.parse
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Optional

from wan_core import DATA_DIR

TRACK_KEYS = (
    'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
    'gclid', 'gbraid', 'wbraid', 'fbclid', 'msclkid', 'dclid', 'ttclid', 'twclid',
    'li_fat_id', 'yclid', 'epik', 'rdt_cid',
)

DEFAULTS = {
    'utm_source': 'videostudio',
    'utm_medium': 'app',
    'utm_campaign': 'video-studio',
}

CONTENT_OAUTH = 'app.oauth_authorize'
CONTENT_SIGNUP = 'app.signup'
UTM_FILE = 'utm.json'


def _path(data_dir: Optional[Path] = None) -> Path:
    return Path(data_dir or DATA_DIR) / UTM_FILE


def _pick(src: Mapping[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k in TRACK_KEYS:
        v = src.get(k)
        if v is None:
            continue
        s = str(v).strip()
        if s:
            out[k] = s
    return out


def _merge_defaults(params: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    out = dict(DEFAULTS)
    if params:
        out.update(_pick(params))
    return out


def load_store(data_dir: Optional[Path] = None) -> dict[str, dict[str, str]]:
    path = _path(data_dir)
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return {'first': {}, 'last': {}}
    if not isinstance(raw, dict):
        return {'first': {}, 'last': {}}
    first = _pick(raw.get('first') or {}) if isinstance(raw.get('first'), dict) else {}
    last = _pick(raw.get('last') or {}) if isinstance(raw.get('last'), dict) else {}
    return {'first': first, 'last': last}


def save_store(store: Mapping[str, Mapping[str, str]], data_dir: Optional[Path] = None) -> None:
    path = _path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'first': dict(store.get('first') or {}),
        'last': dict(store.get('last') or {}),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def capture(params: Mapping[str, Any], data_dir: Optional[Path] = None) -> dict[str, str]:
    """Merge inbound params into last-touch; set first if empty. Returns last-touch with defaults."""
    inbound = _pick(params)
    store = load_store(data_dir)
    if inbound:
        if not store['first']:
            store['first'] = dict(inbound)
        store['last'] = dict(inbound)
        save_store(store, data_dir)
    elif not store['first'] or not store['last']:
        if not store['first']:
            store['first'] = dict(DEFAULTS)
        if not store['last']:
            store['last'] = dict(DEFAULTS)
        save_store(store, data_dir)
    return _merge_defaults(store['last'] or store['first'])


def capture_from_env(data_dir: Optional[Path] = None) -> dict[str, str]:
    """Read POWERTTOKENS_UTM* env and persist. Always ensures defaults exist."""
    params: dict[str, str] = {}
    blob = (os.environ.get('POWERTTOKENS_UTM') or '').strip()
    if blob:
        params.update(_pick(dict(urllib.parse.parse_qsl(blob, keep_blank_values=False))))
    for key in TRACK_KEYS:
        env_key = 'POWERTTOKENS_' + key.upper()
        val = (os.environ.get(env_key) or '').strip()
        if val:
            params[key] = val
    return capture(params, data_dir)


def first_touch(data_dir: Optional[Path] = None) -> dict[str, str]:
    store = load_store(data_dir)
    return _merge_defaults(store.get('first') or {})


def last_touch(data_dir: Optional[Path] = None) -> dict[str, str]:
    store = load_store(data_dir)
    return _merge_defaults(store.get('last') or {})


def params_for(content: Optional[str] = None, data_dir: Optional[Path] = None) -> dict[str, str]:
    p = last_touch(data_dir)
    if content:
        p['utm_content'] = str(content)
    return p


def append_to_url(url: str, content: Optional[str] = None, data_dir: Optional[Path] = None) -> str:
    """Replace tracking query on url with last-touch (+ optional content)."""
    parts = urllib.parse.urlsplit(url)
    q = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
    for k in TRACK_KEYS:
        q.pop(k, None)
    q.update(params_for(content, data_dir))
    return urllib.parse.urlunsplit((
        parts.scheme, parts.netloc, parts.path,
        urllib.parse.urlencode(q), parts.fragment,
    ))


def query_string(content: Optional[str] = None, data_dir: Optional[Path] = None) -> str:
    return urllib.parse.urlencode(params_for(content, data_dir))

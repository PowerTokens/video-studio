"""Video model registry: IDs, limits, pricing, and request builders."""
import datetime
from dataclasses import dataclass, field
from typing import Callable, Optional

from i18n import t

UTM = 'utm_source=github&utm_medium=oss&utm_campaign=video-studio'
PRICE_CHECKED_DATE = '2026-10-05'

# Wan 3.0 limited-time discount (local calendar date, inclusive). Keep; do not treat as permanent copy.
WAN_PROMO_END_DATE = datetime.date(2026, 10, 7)
WAN_LIST_USD_PER_SECOND = {'480p': 0.05, '720p': 0.10, '1080p': 0.20}
WAN_PROMO_USD_PER_SECOND = {'720p': 0.04, '1080p': 0.08}


def local_today():
    return datetime.date.today()


def wan_promo_active(today=None):
    return (today or local_today()) <= WAN_PROMO_END_DATE


@dataclass(frozen=True)
class ModelSpec:
    id: str
    display_name: str
    display_name_zh: str
    family: str  # 'wan' | 'seedance' | 'kling'
    durations: tuple  # allowed integer seconds (no smart -1 in the UI)
    resolutions: tuple  # UI keys like '720p'
    ratios: tuple
    list_price_usd_per_second: dict  # resolution -> USD/s for typical T2V (no input video)
    docs_url: str
    docs_url_zh: str
    model_page_url: str
    model_page_url_zh: str
    default_duration: int = 5
    default_resolution: str = '720p'
    default_ratio: str = '16:9'
    supports_seed: bool = True
    generate_audio_default: bool = True
    # Kling maps resolution UI value -> mode
    kling_mode_map: dict = field(default_factory=dict)
    # How to submit / poll
    submit_path: str = '/v1/videos'
    # poll_kind: 'unified' | 'kling_t2v' | 'kling_i2v'
    poll_kind: str = 'unified'

    def label(self, lang=None):
        from i18n import is_zh
        use_zh = is_zh() if lang is None else lang == 'zh'
        return self.display_name_zh if use_zh else self.display_name

    def price_source(self, lang=None):
        from i18n import is_zh
        use_zh = is_zh() if lang is None else lang == 'zh'
        return self.model_page_url_zh if use_zh else self.model_page_url

    def current_prices(self, today=None):
        if self.id == 'wan3.0-video' and wan_promo_active(today):
            # Promo covers 720p/1080p; keep 480p at list so UI estimates still work.
            prices = {'480p': WAN_LIST_USD_PER_SECOND['480p']}
            prices.update(WAN_PROMO_USD_PER_SECOND)
            return prices
        return dict(self.list_price_usd_per_second)

    def list_prices_for_display(self, today=None):
        """Regular prices shown as "原价" while a promo runs; {} otherwise."""
        if self.id == 'wan3.0-video' and wan_promo_active(today):
            return dict(WAN_LIST_USD_PER_SECOND)
        return {}


def _wan_size(resolution):
    return resolution.upper()


def _seedance_size(resolution):
    return resolution.lower()


def build_wan_request(spec, prompt, duration, resolution, ratio, media=None, seed=''):
    media = media or []
    result = dict(
        model=spec.id,
        prompt=prompt.strip(),
        seconds=str(int(duration)),
        size=_wan_size(resolution),
        ratio=ratio,
        generate_audio=True,
    )
    if media:
        result['media'] = media
    if str(seed).strip():
        result['seed'] = int(seed)
    return result


def build_seedance_request(spec, prompt, duration, resolution, ratio, media=None, seed=''):
    media = media or []
    items = []
    text = prompt.strip()
    if text:
        items.append({'type': 'text', 'text': text})
    for item in media:
        items.append({'type': item['type'], 'url': item['url']})
    if not items:
        raise ValueError('empty')
    result = dict(
        model=spec.id,
        media=items,
        seconds=str(int(duration)),
        size=_seedance_size(resolution),
        ratio=ratio,
        generate_audio=True,
        watermark=False,
    )
    if str(seed).strip():
        result['seed'] = int(seed)
    return result


def build_kling_request(spec, prompt, duration, resolution, ratio, media=None, seed=''):
    media = media or []
    mode = spec.kling_mode_map.get(resolution)
    if not mode:
        raise ValueError('resolution')
    first = next((m['url'] for m in media if m['type'] == 'first_frame'), None)
    last = next((m['url'] for m in media if m['type'] == 'last_frame'), None)
    refs = [m for m in media if m['type'] not in ('first_frame', 'last_frame')]
    if refs:
        raise ValueError('kling_refs')
    body = dict(
        model_name=spec.id,
        prompt=prompt.strip(),
        duration=str(int(duration)),
        mode=mode,
        sound='on' if spec.generate_audio_default else 'off',
    )
    if first or last:
        if first:
            body['image'] = first
        if last:
            body['image_tail'] = last
        # I2V path; aspect_ratio omitted per docs examples
        body['_submit_path'] = '/kling/v1/videos/image2video'
        body['_poll_kind'] = 'kling_i2v'
    else:
        body['aspect_ratio'] = ratio
        body['_submit_path'] = '/kling/v1/videos/text2video'
        body['_poll_kind'] = 'kling_t2v'
    # seed not in Kling OpenAPI — ignore silently if blank; reject if set
    if str(seed).strip():
        raise ValueError('kling_seed')
    return body


MODELS = {
    'wan3.0-video': ModelSpec(
        id='wan3.0-video',
        display_name='Wan 3.0',
        display_name_zh='Wan 3.0',
        family='wan',
        durations=tuple(range(2, 31)),
        resolutions=('480p', '720p', '1080p'),
        ratios=('16:9', '9:16', '1:1', '4:3', '3:4', 'adaptive'),
        list_price_usd_per_second=dict(WAN_LIST_USD_PER_SECOND),
        docs_url='https://docs.powertokens.ai/en/zmodelVideo/ali/wan3.0-video-generation?' + UTM,
        docs_url_zh='https://docs.powertokens.ai/zh-Hans/zmodelVideo/ali/wan3.0-video-generation?' + UTM,
        model_page_url='https://powertokens.ai/models/wan3.0-video?' + UTM,
        model_page_url_zh='https://powertokens.ai/zh-Hans/models/wan3.0-video?' + UTM,
    ),
    'wan3.0-video-prime': ModelSpec(
        id='wan3.0-video-prime',
        display_name='Wan 3.0 Prime',
        display_name_zh='Wan 3.0 Prime',
        family='wan',
        durations=tuple(range(2, 31)),
        resolutions=('480p', '720p', '1080p'),
        ratios=('16:9', '9:16', '1:1', '4:3', '3:4', 'adaptive'),
        list_price_usd_per_second={'480p': 0.068, '720p': 0.14, '1080p': 0.28},
        docs_url='https://docs.powertokens.ai/en/zmodelVideo/ali/wan3.0-video-generation?' + UTM,
        docs_url_zh='https://docs.powertokens.ai/zh-Hans/zmodelVideo/ali/wan3.0-video-generation?' + UTM,
        model_page_url='https://powertokens.ai/models/wan3.0-video-prime?' + UTM,
        model_page_url_zh='https://powertokens.ai/zh-Hans/models/wan3.0-video-prime?' + UTM,
    ),
    'dreamina-seedance-2-0-fast-260128': ModelSpec(
        id='dreamina-seedance-2-0-fast-260128',
        display_name='Seedance 2.0 Fast',
        display_name_zh='Seedance 2.0 Fast',
        family='seedance',
        durations=tuple(range(4, 16)),  # docs [4,15]; OpenAPI enum gaps 13–14 noted in research
        resolutions=('480p', '720p'),
        ratios=('16:9', '9:16', '1:1', '4:3', '3:4', '21:9', 'adaptive'),
        list_price_usd_per_second={'480p': 0.121, '720p': 0.121},
        docs_url='https://docs.powertokens.ai/en/zmodelVideo/byteplus/dreamina-seedance-2-0-fast-text-to-video?' + UTM,
        docs_url_zh='https://docs.powertokens.ai/zh-Hans/zmodelVideo/byteplus/dreamina-seedance-2-0-fast-text-to-video?' + UTM,
        model_page_url='https://powertokens.ai/models/dreamina-seedance-2-0-fast-260128?' + UTM,
        model_page_url_zh='https://powertokens.ai/zh-Hans/models/dreamina-seedance-2-0-fast-260128?' + UTM,
    ),
    'dreamina-seedance-2-5-260628': ModelSpec(
        id='dreamina-seedance-2-5-260628',
        display_name='Seedance 2.5',
        display_name_zh='Seedance 2.5',
        family='seedance',
        durations=tuple(range(4, 31)),
        resolutions=('480p', '720p', '1080p'),
        ratios=('16:9', '9:16', '1:1', '4:3', '3:4', '21:9', 'adaptive'),
        list_price_usd_per_second={'480p': 0.105, '720p': 0.232, '1080p': 0.569},
        docs_url='https://docs.powertokens.ai/en/zmodelVideo/byteplus/dreamina-seedance-2-5-text-to-video?' + UTM,
        docs_url_zh='https://docs.powertokens.ai/zh-Hans/zmodelVideo/byteplus/dreamina-seedance-2-5-text-to-video?' + UTM,
        model_page_url='https://powertokens.ai/models/dreamina-seedance-2-5-260628?' + UTM,
        model_page_url_zh='https://powertokens.ai/zh-Hans/models/dreamina-seedance-2-5-260628?' + UTM,
    ),
    'kling-v3': ModelSpec(
        id='kling-v3',
        display_name='kling v3',
        display_name_zh='kling v3',
        family='kling',
        durations=tuple(range(3, 16)),
        resolutions=('720p', '1080p', '4k'),
        ratios=('16:9', '9:16', '1:1'),
        list_price_usd_per_second={'720p': 0.126, '1080p': 0.168, '4k': 0.42},  # with audio, unspecified voice
        docs_url='https://docs.powertokens.ai/en/zmodelVideo/kling/kling-v3-text2video?' + UTM,
        docs_url_zh='https://docs.powertokens.ai/zh-Hans/zmodelVideo/kling/kling-v3-text2video?' + UTM,
        model_page_url='https://powertokens.ai/models/kling-v3?' + UTM,
        model_page_url_zh='https://powertokens.ai/zh-Hans/models/kling-v3?' + UTM,
        supports_seed=False,
        kling_mode_map={'720p': 'std', '1080p': 'pro', '4k': '4k'},
        submit_path='/kling/v1/videos/text2video',
        poll_kind='kling_t2v',
    ),
}

DEFAULT_MODEL_ID = 'wan3.0-video'
MODEL_ORDER = (
    'wan3.0-video',
    'wan3.0-video-prime',
    'dreamina-seedance-2-0-fast-260128',
    'dreamina-seedance-2-5-260628',
    'kling-v3',
)

# Aliases users may type in Excel / CLI
MODEL_ALIASES = {
    'wan3.0-video': 'wan3.0-video',
    'wan3.0': 'wan3.0-video',
    'wan 3.0': 'wan3.0-video',
    'wan': 'wan3.0-video',
    'wan3.0-video-prime': 'wan3.0-video-prime',
    'wan3.0-prime': 'wan3.0-video-prime',
    'wan 3.0 prime': 'wan3.0-video-prime',
    'prime': 'wan3.0-video-prime',
    'dreamina-seedance-2-0-fast-260128': 'dreamina-seedance-2-0-fast-260128',
    'seedance-2-0-fast': 'dreamina-seedance-2-0-fast-260128',
    'seedance 2.0 fast': 'dreamina-seedance-2-0-fast-260128',
    'seedance2.0fast': 'dreamina-seedance-2-0-fast-260128',
    'seedance fast': 'dreamina-seedance-2-0-fast-260128',
    'dreamina-seedance-2-5-260628': 'dreamina-seedance-2-5-260628',
    'seedance-2-5': 'dreamina-seedance-2-5-260628',
    'seedance 2.5': 'dreamina-seedance-2-5-260628',
    'seedance2.5': 'dreamina-seedance-2-5-260628',
    'kling-v3': 'kling-v3',
    'kling v3': 'kling-v3',
    'klingv3': 'kling-v3',
    'kling 3': 'kling-v3',
}


BUILDERS = {
    'wan': build_wan_request,
    'seedance': build_seedance_request,
    'kling': build_kling_request,
}


def get_model(model_id=None):
    mid = model_id or DEFAULT_MODEL_ID
    if mid not in MODELS:
        raise KeyError(mid)
    return MODELS[mid]


def resolve_model_id(value, default=DEFAULT_MODEL_ID):
    text = str(value or '').strip()
    if not text:
        return default
    key = text.lower().replace('_', '-').strip()
    key_compact = key.replace(' ', '')
    if key in MODEL_ALIASES:
        return MODEL_ALIASES[key]
    if key_compact in MODEL_ALIASES:
        return MODEL_ALIASES[key_compact]
    # try display names
    for mid, spec in MODELS.items():
        if key == spec.display_name.lower() or key == spec.id.lower():
            return mid
    raise KeyError(text)


def list_models():
    return [MODELS[mid] for mid in MODEL_ORDER]


def validate_params(spec, duration, resolution, ratio, prompt='', media=None, seed=''):
    """Raise TaskError-compatible ValueError with friendly message, or return cleaned values."""
    from wan_core import TaskError
    media = media or []
    try:
        duration = int(duration)
    except (TypeError, ValueError):
        raise TaskError(t('duration_int'), 'PARAM')
    if duration not in spec.durations:
        raise TaskError(t('duration_not_allowed', spec.label(), spec.durations[0], spec.durations[-1]), 'PARAM')
    resolution = str(resolution).strip().lower()
    if resolution in ('720', '1080', '480', '4k'):
        if resolution != '4k':
            resolution += 'p'
    if resolution not in spec.resolutions:
        raise TaskError(t('resolution_not_allowed', spec.label(), ', '.join(spec.resolutions)), 'PARAM')
    ratio = str(ratio).replace('：', ':').replace(' ', '').strip() or spec.default_ratio
    if ratio not in spec.ratios:
        raise TaskError(t('ratio_not_allowed', spec.label(), ', '.join(spec.ratios)), 'PARAM')
    if not prompt.strip() and not media:
        raise TaskError(t('need_prompt_or_media'), 'PARAM')
    allowed_media = {
        'wan': ('first_frame', 'last_frame', 'reference_image', 'reference_video', 'reference_audio', 'file', 'link'),
        'seedance': ('first_frame', 'last_frame', 'reference_image', 'reference_video', 'reference_audio'),
        'kling': ('first_frame', 'last_frame'),
    }[spec.family]
    import urllib.parse
    for item in media:
        parsed = urllib.parse.urlsplit(item['url'])
        if parsed.scheme in ('http', 'https'):
            if not parsed.hostname:
                raise TaskError(t('media_url_invalid'), 'PARAM')
        elif parsed.scheme == 'asset':
            if not (parsed.netloc or parsed.path.strip('/')):
                raise TaskError(t('media_url_invalid'), 'PARAM')
        else:
            raise TaskError(t('media_url_invalid'), 'PARAM')
        if item['type'] not in allowed_media:
            raise TaskError(t('media_type_invalid'), 'PARAM')
    if str(seed).strip():
        if not spec.supports_seed:
            raise TaskError(t('seed_not_supported', spec.label()), 'PARAM')
        try:
            int(seed)
        except (TypeError, ValueError):
            raise TaskError(t('seed_invalid'), 'PARAM')
    return duration, resolution, ratio


def build_request(model_id, prompt, duration=5, resolution='720p', ratio='16:9', media=None, seed=''):
    from wan_core import TaskError
    try:
        spec = get_model(model_id)
    except KeyError:
        raise TaskError(t('model_unknown', model_id), 'PARAM')
    media = media or []
    duration, resolution, ratio = validate_params(spec, duration, resolution, ratio, prompt, media, seed)
    try:
        body = BUILDERS[spec.family](spec, prompt, duration, resolution, ratio, media, seed)
    except ValueError as exc:
        code = str(exc)
        if code == 'kling_refs':
            raise TaskError(t('kling_media_limited'), 'PARAM')
        if code == 'kling_seed':
            raise TaskError(t('seed_not_supported', spec.label()), 'PARAM')
        if code == 'empty':
            raise TaskError(t('need_prompt_or_media'), 'PARAM')
        raise TaskError(t('choose_valid_params'), 'PARAM')
    return body


def estimate_cost(duration, resolution, model_id=None, prices=None, today=None):
    spec = get_model(model_id or DEFAULT_MODEL_ID)
    prices = spec.current_prices(today) if prices is None else prices
    resolution = str(resolution).strip().lower()
    if resolution in ('720', '1080', '480'):
        resolution += 'p'
    if resolution not in prices:
        raise ValueError(t('resolution_unsupported'))
    return round(int(duration) * prices[resolution], 3)


def poll_url(api_base, task_id, poll_kind='unified'):
    if poll_kind == 'kling_t2v':
        return api_base + '/kling/v1/videos/text2video/' + task_id
    if poll_kind == 'kling_i2v':
        return api_base + '/kling/v1/videos/image2video/' + task_id
    return api_base + '/v1/videos/' + task_id


def submit_url(api_base, request):
    path = request.pop('_submit_path', None)
    if path:
        return api_base + path
    spec = MODELS.get(request.get('model') or request.get('model_name') or DEFAULT_MODEL_ID)
    if spec and spec.family == 'kling':
        return api_base + spec.submit_path
    return api_base + '/v1/videos'


def extract_task_id(body, family='wan'):
    if family == 'kling':
        data = body.get('data') if isinstance(body.get('data'), dict) else {}
        task_id = data.get('task_id') or body.get('task_id') or body.get('id')
    else:
        task_id = body.get('id') or body.get('task_id')
    return task_id if isinstance(task_id, str) and task_id else None


def normalize_status(body, family='wan'):
    """Return (status_lower, progress_or_None, video_url_or_empty)."""
    if family == 'kling':
        data = body.get('data') if isinstance(body.get('data'), dict) else body
        raw = str(data.get('task_status') or '').strip().lower()
        mapping = {
            'submitted': 'queued',
            'processing': 'in_progress',
            'succeed': 'succeeded',
            'success': 'succeeded',
            'failed': 'failed',
        }
        status = mapping.get(raw, raw)
        url = ''
        result = data.get('task_result') if isinstance(data.get('task_result'), dict) else {}
        videos = result.get('videos') if isinstance(result.get('videos'), list) else []
        if videos and isinstance(videos[0], dict):
            candidate = videos[0].get('url') or videos[0].get('watermark_url') or ''
            if isinstance(candidate, str) and candidate.startswith('https://'):
                url = candidate
        return status, None, url
    status = str(body.get('status') or '').strip().lower()
    progress = body.get('progress')
    url = ''
    meta = body.get('metadata') if isinstance(body.get('metadata'), dict) else {}
    content = body.get('content') if isinstance(body.get('content'), dict) else {}
    for candidate in (meta.get('url'), body.get('video_url'), content.get('video_url')):
        if isinstance(candidate, str) and candidate.startswith('https://'):
            url = candidate
            break
    return status, progress, url


def poll_kind_for_record(record):
    kind = (record or {}).get('poll_kind')
    if kind:
        return kind
    mid = (record or {}).get('model') or DEFAULT_MODEL_ID
    spec = MODELS.get(mid)
    if spec and spec.family == 'kling':
        return 'kling_t2v'
    return 'unified'


def family_for_record(record):
    mid = (record or {}).get('model') or DEFAULT_MODEL_ID
    spec = MODELS.get(mid)
    return spec.family if spec else 'wan'

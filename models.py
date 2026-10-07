"""Video model registry: IDs, limits, pricing, and request builders."""
import datetime
from dataclasses import dataclass, field
from typing import Callable, Optional

from i18n import t

# In-app links (UI, CLI, MCP) use medium=app; README links use medium=oss.
UTM = 'utm_source=github&utm_medium=app&utm_campaign=video-studio'
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
    short_name: str  # filename-safe slug for compare outputs
    description_en: str
    description_zh: str
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

    def description(self, lang=None):
        from i18n import is_zh
        use_zh = is_zh() if lang is None else lang == 'zh'
        return self.description_zh if use_zh else self.description_en

    def summary(self, lang=None):
        """One-line blurb for the compact model picker."""
        from i18n import is_zh
        use_zh = is_zh() if lang is None else lang == 'zh'
        text = self.description(lang)
        if use_zh:
            part = text.split('。')[0]
            return (part + '。') if part else text
        # English: first sentence
        for sep in ('. ', ' — ', ' - '):
            if sep in text:
                chunk = text.split(sep)[0].rstrip('.')
                return chunk + '.'
        return text

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
        def _kling_img(value):
            text = str(value)
            if text.startswith('data:') and ';base64,' in text:
                return text.split(';base64,', 1)[1]
            return text
        if first:
            body['image'] = _kling_img(first)
        if last:
            body['image_tail'] = _kling_img(last)
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
        short_name='wan',
        description_en='All-in-one Wan model for text-to-video, image-to-video (first / first-last frame), and reference-to-video with native audio. Up to 30 seconds at 480p–1080p — a solid default for product demos, talking-head clips, and everyday videos.',
        description_zh='全能型 Wan 模型：文生视频、图生视频（首帧 / 首尾帧）与参考生视频，带原生音频。最长 30 秒，支持 480p–1080p，适合产品展示、口播与日常成片，作为默认选项。',
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
        short_name='wan-prime',
        description_en='Same capabilities as Wan 3.0 (text / image / reference, up to 30s, 480p–1080p, native audio), tuned for significantly faster end-to-end generation when you want quicker turnaround.',
        description_zh='能力与 Wan 3.0 相同（文生 / 图生 / 参考、最长 30 秒、480p–1080p、原生音频），端到端生成明显更快，适合更赶时间的出片。',
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
        short_name='seedance-fast',
        description_en='Seedance 2.0 Fast prioritizes speed for drafts and previews: text-to-video, image-to-video, and multimodal reference, with native audio. Clips are 4–15 seconds and top out at 720p (no 1080p).',
        description_zh='Seedance 2.0 Fast 侧重速度，适合草稿与预览：支持文生、图生与多模态参考，带原生音频。时长 4–15 秒，最高 720p（不支持 1080p）。',
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
        short_name='seedance-25',
        description_en='Seedance 2.5 targets longer storytelling with native audio sync, up to 30 seconds and 480p–1080p. Supports text-to-video, image-to-video, and multimodal references for scene and character consistency.',
        description_zh='Seedance 2.5 面向更长叙事与原生音频同步，最长 30 秒，支持 480p–1080p。文生、图生与多模态参考可用于保持场景与角色一致性。',
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
        short_name='kling-v3',
        description_en='kling v3 offers text-to-video and image-to-video (first / last frame) with optional native audio. Duration 3–15 seconds; choose 720p, 1080p, or 4K mode when you need higher output resolution.',
        description_zh='kling v3 支持文生视频与图生视频（首帧 / 尾帧），可选原生音频。时长 3–15 秒；可选 720p、1080p 或 4K 模式，需要更高输出分辨率时选用。',
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




RES_RANK = {'480p': 480, '720p': 720, '1080p': 1080, '4k': 2160}
RATIO_ASPECT = {
    '16:9': 16 / 9, '9:16': 9 / 16, '1:1': 1.0, '4:3': 4 / 3, '3:4': 3 / 4,
    '21:9': 21 / 9, 'adaptive': None,
}


def normalize_resolution(value):
    value = str(value or '').strip().lower().replace(' ', '')
    if value in ('720', '1080', '480'):
        value += 'p'
    if value in ('2160p', '2160'):
        value = '4k'
    return value


def normalize_ratio(value, default='16:9'):
    value = str(value or '').replace('：', ':').replace(' ', '').strip()
    return value or default


def nearest_duration(spec, duration):
    try:
        duration = int(duration)
    except (TypeError, ValueError):
        return spec.default_duration
    if duration in spec.durations:
        return duration
    return min(spec.durations, key=lambda item: (abs(item - duration), item))


def nearest_resolution(spec, resolution):
    resolution = normalize_resolution(resolution)
    if resolution in spec.resolutions:
        return resolution
    rank = RES_RANK.get(resolution)
    if rank is None:
        return spec.default_resolution if spec.default_resolution in spec.resolutions else spec.resolutions[0]
    return min(spec.resolutions, key=lambda item: abs(RES_RANK.get(item, 10**9) - rank))


def nearest_ratio(spec, ratio):
    ratio = normalize_ratio(ratio, spec.default_ratio)
    if ratio in spec.ratios:
        return ratio
    aspect = RATIO_ASPECT.get(ratio)
    candidates = [(item, RATIO_ASPECT[item]) for item in spec.ratios
                  if item in RATIO_ASPECT and RATIO_ASPECT[item] is not None]
    if aspect is None or not candidates:
        if spec.default_ratio in spec.ratios:
            return spec.default_ratio
        return spec.ratios[0]
    return min(candidates, key=lambda pair: abs(pair[1] - aspect))[0]


def snap_params(spec, duration, resolution, ratio):
    """Snap unsupported params to the nearest allowed values.

    Returns (duration, resolution, ratio, changes) where each change is
    (field, old_value, new_value) with field in ('duration', 'resolution', 'ratio').
    """
    changes = []
    try:
        old_duration = int(duration)
    except (TypeError, ValueError):
        old_duration = duration
    new_duration = nearest_duration(spec, duration)
    if str(old_duration) != str(new_duration):
        changes.append(('duration', old_duration, new_duration))

    old_resolution = normalize_resolution(resolution) if str(resolution or '').strip() else resolution
    new_resolution = nearest_resolution(spec, resolution)
    if normalize_resolution(old_resolution) != new_resolution:
        changes.append(('resolution', old_resolution or resolution, new_resolution))

    old_ratio = normalize_ratio(ratio, '') if str(ratio or '').strip() else ratio
    new_ratio = nearest_ratio(spec, ratio)
    if normalize_ratio(str(old_ratio or ''), '') != new_ratio:
        changes.append(('ratio', old_ratio or ratio, new_ratio))

    return new_duration, new_resolution, new_ratio, changes


def _duration_limit_phrase(spec, old, new, use_zh):
    try:
        old_n = int(old)
    except (TypeError, ValueError):
        old_n = None
    lo, hi = spec.durations[0], spec.durations[-1]
    if old_n is not None and old_n < lo:
        return ('最短 %s 秒' % lo) if use_zh else ('at least %s s' % lo)
    return ('最长 %s 秒' % hi) if use_zh else ('up to %s s' % hi)


def _resolution_limit_phrase(spec, old, new, use_zh):
    ranked = sorted(spec.resolutions, key=lambda item: RES_RANK.get(item, 0))
    lo, hi = ranked[0], ranked[-1]
    old_n = RES_RANK.get(normalize_resolution(old))
    new_n = RES_RANK.get(new, RES_RANK.get(hi, 0))
    if old_n is not None and old_n < new_n:
        return ('最低 %s' % lo) if use_zh else ('at least %s' % lo)
    # EN matches "… and 720p" (plain ceiling); ZH keeps 最高.
    return ('最高 %s' % hi) if use_zh else hi


def _ratio_limit_phrase(spec, use_zh):
    joined = ' / '.join(spec.ratios)
    if use_zh:
        return '比例仅支持 %s' % joined
    if len(spec.ratios) == 1:
        return 'aspect ratio %s' % spec.ratios[0]
    return 'aspect ratios %s' % joined


def format_adjust_notice(spec, changes, *, for_batch=False, lang=None):
    """Plain notice naming only the model limits that forced a change.

    UI example (ZH): "Seedance 2.0 Fast 最长 15 秒、最高 720p，已自动调整"
    UI example (EN): "Seedance 2.0 Fast supports up to 15 s and 720p, so settings were adjusted automatically."
    Batch uses the same limit phrasing with a "please update" ending.
    """
    from i18n import is_zh
    if not changes:
        return ''
    use_zh = is_zh() if lang is None else lang == 'zh'
    name = spec.label(lang)
    phrases = []
    for field, old, new in changes:
        if field == 'duration':
            phrases.append(_duration_limit_phrase(spec, old, new, use_zh))
        elif field == 'resolution':
            phrases.append(_resolution_limit_phrase(spec, old, new, use_zh))
        elif field == 'ratio':
            phrases.append(_ratio_limit_phrase(spec, use_zh))
    if use_zh:
        limits = '、'.join(phrases)
        if for_batch:
            return '%s %s，请按此修改' % (name, limits)
        return '%s %s，已自动调整' % (name, limits)
    # English: "supports A and B" / "supports A, B and C"
    if len(phrases) == 1:
        limits = phrases[0]
    elif len(phrases) == 2:
        limits = '%s and %s' % (phrases[0], phrases[1])
    else:
        limits = '%s and %s' % (', '.join(phrases[:-1]), phrases[-1])
    if for_batch:
        return '%s supports %s; please update this row.' % (name, limits)
    return '%s supports %s, so settings were adjusted automatically.' % (name, limits)


# Back-compat alias used by older call sites / tests
def format_adjust_summary(changes, lang=None):
    parts = []
    use_zh = (lang == 'zh') if lang is not None else False
    for field, _old, new in changes:
        if field == 'duration':
            parts.append(('%s 秒' % new) if use_zh else ('%ss' % new))
        elif field == 'resolution':
            parts.append(str(new))
        elif field == 'ratio':
            parts.append(str(new))
    return ' / '.join(parts)

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
        url = item.get('url') or ''
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme in ('http', 'https'):
            if not parsed.hostname:
                raise TaskError(t('media_url_invalid'), 'PARAM')
        elif parsed.scheme == 'asset':
            if not (parsed.netloc or parsed.path.strip('/')):
                raise TaskError(t('media_url_invalid'), 'PARAM')
        elif parsed.scheme == 'data':
            # Seedance/Kling (and Wan live-key) accept data:image/…;base64,…
            if not url.lower().startswith('data:image/') or ';base64,' not in url:
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

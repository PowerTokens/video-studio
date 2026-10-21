"""Video model registry: IDs, limits, pricing, and request builders."""
import datetime
from dataclasses import dataclass, field
from typing import Callable, Optional

from i18n import t

# In-app links (UI, CLI, MCP) use medium=app; README links use medium=oss.
UTM = 'utm_source=videostudio&utm_medium=app&utm_campaign=video-studio'
PRICE_CHECKED_DATE = '2026-10-21'

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
    # Optional promo: current list_price is the sale rate; before_discount is struck-through list.
    list_price_before_discount_usd_per_second: dict = field(default_factory=dict)
    price_usd_per_million_tokens: Optional[float] = None
    list_price_before_discount_usd_per_million_tokens: Optional[float] = None

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
        if self.list_price_before_discount_usd_per_second:
            return dict(self.list_price_before_discount_usd_per_second)
        return {}

    def discount_percent(self):
        """Integer percent off from token prices when present, else from per-second rates. 0 = no promo."""
        if (isinstance(self.price_usd_per_million_tokens, (int, float))
                and isinstance(self.list_price_before_discount_usd_per_million_tokens, (int, float))):
            now = float(self.price_usd_per_million_tokens)
            before = float(self.list_price_before_discount_usd_per_million_tokens)
            if before > now > 0:
                return int(round((1 - now / before) * 100))
        before_map = self.list_price_before_discount_usd_per_second or {}
        best = 0
        for key, now in self.list_price_usd_per_second.items():
            before = before_map.get(key)
            if isinstance(now, (int, float)) and isinstance(before, (int, float)) and before > now > 0:
                pct = int(round((1 - now / before) * 100))
                if pct > best:
                    best = pct
        return best


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


def _h3_resolution(resolution):
    r = str(resolution).strip().lower()
    if r == '2k':
        return '2K'
    if r in ('768p', '768'):
        return '768P'
    if r in ('480p', '480'):
        return '480P'
    return r.upper() if r.endswith('p') else r


def _hailuo_size(resolution):
    r = str(resolution).strip().lower()
    if r in ('768', '768p'):
        return '768P'
    if r in ('1080', '1080p'):
        return '1080P'
    return r.upper()


def _vidu_resolution(resolution):
    r = str(resolution).strip().lower()
    if r == '2k':
        return '2K'
    if r == '4k':
        return '4K'
    return r


# Flat per-clip prices for Hailuo 2.3 (model page 2026-10-21). Not $/s.
HAILUO_FLAT_USD = {
    ('768p', 6): 0.266,
    ('768p', 10): 0.532,
    ('1080p', 6): 0.466,
}


def build_h3_request(spec, prompt, duration, resolution, ratio, media=None, seed=''):
    """MiniMax-H3 via POST /minimax/v2/video_generation (T2V content array)."""
    media = media or []
    text = prompt.strip()
    if not text:
        raise ValueError('empty')
    # T2V for Studio MVP: reject media for now (I2V/R2V can land later).
    if media:
        raise ValueError('h3_media')
    body = dict(
        model=spec.id,
        content=[{'type': 'text', 'text': text}],
        resolution=_h3_resolution(resolution),
        duration=int(duration),
        ratio=ratio,
    )
    return body


def build_hailuo_request(spec, prompt, duration, resolution, ratio, media=None, seed=''):
    """Hailuo 2.3 T2V via unified POST /v1/videos (model/prompt/seconds/size)."""
    media = media or []
    if media:
        raise ValueError('hailuo_media')
    text = prompt.strip()
    if not text:
        raise ValueError('empty')
    size = _hailuo_size(resolution)
    seconds = str(int(duration))
    if size == '1080P' and seconds != '6':
        raise ValueError('hailuo_1080_duration')
    return dict(
        model=spec.id,
        prompt=text,
        seconds=seconds,
        size=size,
    )


def build_vidu_request(spec, prompt, duration, resolution, ratio, media=None, seed=''):
    """Vidu Q4 preview: I2V (first_frame) or R2V (reference_image); no pure T2V."""
    media = media or []
    text = prompt.strip()
    first = [m['url'] for m in media if m['type'] == 'first_frame']
    last = [m['url'] for m in media if m['type'] == 'last_frame']
    refs = [m['url'] for m in media if m['type'] == 'reference_image']
    audios = [m['url'] for m in media if m['type'] == 'reference_audio']
    other = [m for m in media if m['type'] not in (
        'first_frame', 'last_frame', 'reference_image', 'reference_audio')]
    if other or last:
        raise ValueError('vidu_media')
    if first and refs:
        raise ValueError('vidu_media')
    if not first and not refs:
        raise ValueError('vidu_need_image')
    res = _vidu_resolution(resolution)
    body = dict(
        model=spec.id,
        duration=int(duration),
        resolution=res,
        audio=True if spec.generate_audio_default else False,
    )
    if text:
        body['prompt'] = text
    if str(seed).strip():
        body['seed'] = int(seed)
    if first:
        body['images'] = first[:1]
        body['_submit_path'] = '/vidu/ent/v2/img2video'
        body['_poll_kind'] = 'vidu'
        return body
    # reference-to-video
    if not text:
        raise ValueError('empty')
    body['images'] = refs[:15]
    if audios:
        body['sounds'] = audios[:3]
    body['aspect_ratio'] = ratio
    body['_submit_path'] = '/vidu/ent/v2/reference2video'
    body['_poll_kind'] = 'vidu'
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
    'dreamina-seedance-2-0-260128': ModelSpec(
        id='dreamina-seedance-2-0-260128',
        display_name='Seedance 2.0',
        display_name_zh='Seedance 2.0',
        short_name='seedance-20',
        description_en='Seedance 2.0 is the full-quality model for text-to-video, image-to-video, and multimodal reference, with native audio. Clips are 4–15 seconds at 480p, 720p, 1080p, or 4K.',
        description_zh='Seedance 2.0 为完整画质版本：支持文生、图生与多模态参考，带原生音频。时长 4–15 秒，支持 480p、720p、1080p 与 4K。',
        family='seedance',
        durations=tuple(range(4, 16)),
        resolutions=('480p', '720p', '1080p', '4k'),
        ratios=('16:9', '9:16', '1:1', '4:3', '3:4', '21:9', 'adaptive'),
        # Without-video ≈$/s from model page 2026-10-21 (480/720/1080/4K).
        list_price_usd_per_second={'480p': 0.069, '720p': 0.152, '1080p': 0.375, '4k': 0.778},
        docs_url='https://docs.powertokens.ai/en/zmodelVideo/byteplus/dreamina-seedance-2-0-text-to-video?' + UTM,
        docs_url_zh='https://docs.powertokens.ai/zh-Hans/zmodelVideo/byteplus/dreamina-seedance-2-0-text-to-video?' + UTM,
        model_page_url='https://powertokens.ai/models/dreamina-seedance-2-0-260128?' + UTM,
        model_page_url_zh='https://powertokens.ai/zh-Hans/models/dreamina-seedance-2-0-260128?' + UTM,
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
        # Sale $0.061/s (was $0.121) — 50% OFF on model page 2026-10-21.
        list_price_usd_per_second={'480p': 0.061, '720p': 0.061},
        list_price_before_discount_usd_per_second={'480p': 0.121, '720p': 0.121},
        price_usd_per_million_tokens=2.8,
        list_price_before_discount_usd_per_million_tokens=5.6,
        docs_url='https://docs.powertokens.ai/en/zmodelVideo/byteplus/dreamina-seedance-2-0-fast-text-to-video?' + UTM,
        docs_url_zh='https://docs.powertokens.ai/zh-Hans/zmodelVideo/byteplus/dreamina-seedance-2-0-fast-text-to-video?' + UTM,
        model_page_url='https://powertokens.ai/models/dreamina-seedance-2-0-fast-260128?' + UTM,
        model_page_url_zh='https://powertokens.ai/zh-Hans/models/dreamina-seedance-2-0-fast-260128?' + UTM,
    ),
    'dreamina-seedance-2-0-mini-260615': ModelSpec(
        id='dreamina-seedance-2-0-mini-260615',
        display_name='Seedance 2.0 Mini',
        display_name_zh='Seedance 2.0 Mini',
        short_name='seedance-20-mini',
        description_en='Seedance 2.0 Mini prioritizes lower cost and faster turnaround for high-volume clips: text-to-video, image-to-video, and multimodal reference, with native audio. Clips are 4–15 seconds and top out at 720p (no 1080p).',
        description_zh='Seedance 2.0 Mini 侧重更低成本与更快出片，适合大批量内容：支持文生、图生与多模态参考，带原生音频。时长 4–15 秒，最高 720p（不支持 1080p）。',
        family='seedance',
        durations=tuple(range(4, 16)),
        resolutions=('480p', '720p'),
        ratios=('16:9', '9:16', '1:1', '4:3', '3:4', '21:9', 'adaptive'),
        # Sale $0.031/s (was $0.076) — 60% OFF on model page 2026-10-21.
        list_price_usd_per_second={'480p': 0.031, '720p': 0.031},
        list_price_before_discount_usd_per_second={'480p': 0.076, '720p': 0.076},
        price_usd_per_million_tokens=1.4,
        list_price_before_discount_usd_per_million_tokens=3.5,
        docs_url='https://docs.powertokens.ai/en/zmodelVideo/byteplus/dreamina-seedance-2-0-mini-text-to-video?' + UTM,
        docs_url_zh='https://docs.powertokens.ai/zh-Hans/zmodelVideo/byteplus/dreamina-seedance-2-0-mini-text-to-video?' + UTM,
        model_page_url='https://powertokens.ai/models/dreamina-seedance-2-0-mini-260615?' + UTM,
        model_page_url_zh='https://powertokens.ai/zh-Hans/models/dreamina-seedance-2-0-mini-260615?' + UTM,
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
    'MiniMax-H3': ModelSpec(
        id='MiniMax-H3',
        display_name='MiniMax H3',
        display_name_zh='MiniMax H3',
        short_name='minimax-h3',
        description_en='MiniMax H3 generates 4–15 second clips at 768p or 2K with text-to-video (Studio). Multimodal reference and image conditioning are available on the API; this build focuses on text-to-video.',
        description_zh='MiniMax H3 支持 4–15 秒、768p 或 2K 的文生视频（Studio 当前）。API 另有图生与多模态参考；本版优先文生。',
        family='h3',
        durations=tuple(range(4, 16)),
        resolutions=('768p', '2k'),
        ratios=('21:9', '16:9', '4:3', '1:1', '3:4', '9:16'),
        # Output $/s from model page 2026-10-21 (768P $0.08, 2K $0.13).
        list_price_usd_per_second={'768p': 0.08, '2k': 0.13},
        docs_url='https://docs.powertokens.ai/en/zmodelVideo/minimax/minimax-h3/text-to-video?' + UTM,
        docs_url_zh='https://docs.powertokens.ai/zh-Hans/zmodelVideo/minimax/minimax-h3/text-to-video?' + UTM,
        model_page_url='https://powertokens.ai/models/MiniMax-H3?' + UTM,
        model_page_url_zh='https://powertokens.ai/zh-Hans/models/MiniMax-H3?' + UTM,
        default_resolution='768p',
        submit_path='/minimax/v2/video_generation',
        poll_kind='h3',
    ),
    'MiniMax-Hailuo-2.3': ModelSpec(
        id='MiniMax-Hailuo-2.3',
        display_name='Hailuo 2.3',
        display_name_zh='Hailuo 2.3',
        short_name='hailuo-23',
        description_en='Hailuo 2.3 text-to-video at 768p or 1080p with fixed 6 or 10 second clips (1080p is 6 seconds only). Priced per clip on PowerTokens.',
        description_zh='Hailuo 2.3 文生视频，支持 768p / 1080p，时长固定 6 或 10 秒（1080p 仅 6 秒）。PowerTokens 按条计费。',
        family='hailuo',
        durations=(6, 10),
        resolutions=('768p', '1080p'),
        ratios=('16:9',),
        # Effective $/s for 6s baseline (exact flat totals used in estimate_cost).
        list_price_usd_per_second={'768p': 0.044, '1080p': 0.078},
        docs_url='https://docs.powertokens.ai/en/zmodelVideo/minimax/minimax-hailuo-2.3-text-to-video?' + UTM,
        docs_url_zh='https://docs.powertokens.ai/zh-Hans/zmodelVideo/minimax/minimax-hailuo-2.3-text-to-video?' + UTM,
        model_page_url='https://powertokens.ai/models/MiniMax-Hailuo-2.3?' + UTM,
        model_page_url_zh='https://powertokens.ai/zh-Hans/models/MiniMax-Hailuo-2.3?' + UTM,
        default_duration=6,
        default_resolution='768p',
        supports_seed=False,
    ),
    'viduq4-preview': ModelSpec(
        id='viduq4-preview',
        display_name='Vidu Q4 Preview',
        display_name_zh='Vidu Q4 Preview',
        short_name='viduq4',
        description_en='Vidu Q4 Preview (I2V / reference-to-video only — no text-only). Clips 3–16 seconds at 540p–4K with optional native audio. Attach a first frame or reference images to generate.',
        description_zh='Vidu Q4 Preview（仅图生 / 参考生，不支持纯文生）。时长 3–16 秒，540p–4K，可选原生音频。请上传首帧或参考图后再生成。',
        family='vidu',
        durations=tuple(range(3, 17)),
        resolutions=('540p', '720p', '1080p', '2k', '4k'),
        ratios=('16:9', '9:16', '1:1', '3:4', '4:3'),
        # Peak (non off-peak) $/s from model page 2026-10-21.
        list_price_usd_per_second={
            '540p': 0.045, '720p': 0.095, '1080p': 0.120, '2k': 0.190, '4k': 0.390,
        },
        docs_url='https://docs.powertokens.ai/en/zmodelVideo/vidu/viduq4-preview-image-to-video?' + UTM,
        docs_url_zh='https://docs.powertokens.ai/zh-Hans/zmodelVideo/vidu/viduq4-preview-image-to-video?' + UTM,
        model_page_url='https://powertokens.ai/models/viduq4-preview?' + UTM,
        model_page_url_zh='https://powertokens.ai/zh-Hans/models/viduq4-preview?' + UTM,
        default_duration=5,
        default_resolution='720p',
        submit_path='/vidu/ent/v2/img2video',
        poll_kind='vidu',
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
    'dreamina-seedance-2-0-260128',
    'dreamina-seedance-2-0-fast-260128',
    'dreamina-seedance-2-0-mini-260615',
    'dreamina-seedance-2-5-260628',
    'MiniMax-H3',
    'MiniMax-Hailuo-2.3',
    'viduq4-preview',
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
    'dreamina-seedance-2-0-260128': 'dreamina-seedance-2-0-260128',
    'seedance-2-0': 'dreamina-seedance-2-0-260128',
    'seedance 2.0': 'dreamina-seedance-2-0-260128',
    'seedance2.0': 'dreamina-seedance-2-0-260128',
    'dreamina-seedance-2-0-mini-260615': 'dreamina-seedance-2-0-mini-260615',
    'seedance-2-0-mini': 'dreamina-seedance-2-0-mini-260615',
    'seedance 2.0 mini': 'dreamina-seedance-2-0-mini-260615',
    'seedance2.0mini': 'dreamina-seedance-2-0-mini-260615',
    'seedance mini': 'dreamina-seedance-2-0-mini-260615',
    'dreamina-seedance-2-5-260628': 'dreamina-seedance-2-5-260628',
    'seedance-2-5': 'dreamina-seedance-2-5-260628',
    'seedance 2.5': 'dreamina-seedance-2-5-260628',
    'seedance2.5': 'dreamina-seedance-2-5-260628',
    'minimax-h3': 'MiniMax-H3',
    'minimax h3': 'MiniMax-H3',
    'h3': 'MiniMax-H3',
    'MiniMax-H3': 'MiniMax-H3',
    'minimax-hailuo-2.3': 'MiniMax-Hailuo-2.3',
    'hailuo-2.3': 'MiniMax-Hailuo-2.3',
    'hailuo 2.3': 'MiniMax-Hailuo-2.3',
    'hailuo': 'MiniMax-Hailuo-2.3',
    'MiniMax-Hailuo-2.3': 'MiniMax-Hailuo-2.3',
    'viduq4-preview': 'viduq4-preview',
    'viduq4': 'viduq4-preview',
    'vidu q4': 'viduq4-preview',
    'vidu q4 preview': 'viduq4-preview',
    'kling-v3': 'kling-v3',
    'kling v3': 'kling-v3',
    'klingv3': 'kling-v3',
    'kling 3': 'kling-v3',
}


BUILDERS = {
    'wan': build_wan_request,
    'seedance': build_seedance_request,
    'kling': build_kling_request,
    'h3': build_h3_request,
    'hailuo': build_hailuo_request,
    'vidu': build_vidu_request,
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
    specs = [MODELS[mid] for mid in MODEL_ORDER]
    promo = [s for s in specs if s.discount_percent() > 0]
    rest = [s for s in specs if s.discount_percent() <= 0]
    return promo + rest




RES_RANK = {'480p': 480, '540p': 540, '720p': 720, '768p': 768, '1080p': 1080, '2k': 1440, '4k': 2160}
RATIO_ASPECT = {
    '16:9': 16 / 9, '9:16': 9 / 16, '1:1': 1.0, '4:3': 4 / 3, '3:4': 3 / 4,
    '21:9': 21 / 9, 'adaptive': None,
}


def normalize_resolution(value):
    value = str(value or '').strip().lower().replace(' ', '')
    if value in ('720', '1080', '480', '540', '768'):
        value += 'p'
    if value in ('2160p', '2160'):
        value = '4k'
    if value in ('1440p', '1440'):
        value = '2k'
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
    resolution = normalize_resolution(resolution)
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
        'h3': (),
        'hailuo': (),
        'vidu': ('first_frame', 'reference_image', 'reference_audio'),
    }[spec.family]
    if spec.family == 'hailuo' and resolution == '1080p' and duration != 6:
        raise TaskError(t('hailuo_1080_only_6s', spec.label()), 'PARAM')
    if spec.family == 'vidu' and not media:
        raise TaskError(t('vidu_need_image', spec.label()), 'PARAM')
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
        if code in ('h3_media', 'hailuo_media'):
            raise TaskError(t('model_t2v_only', spec.label()), 'PARAM')
        if code == 'hailuo_1080_duration':
            raise TaskError(t('hailuo_1080_only_6s', spec.label()), 'PARAM')
        if code == 'vidu_need_image':
            raise TaskError(t('vidu_need_image', spec.label()), 'PARAM')
        if code == 'vidu_media':
            raise TaskError(t('vidu_media_limited', spec.label()), 'PARAM')
        raise TaskError(t('choose_valid_params'), 'PARAM')
    return body


def estimate_cost(duration, resolution, model_id=None, prices=None, today=None):
    spec = get_model(model_id or DEFAULT_MODEL_ID)
    resolution = normalize_resolution(resolution)
    if spec.family == 'hailuo':
        key = (resolution, int(duration))
        if key not in HAILUO_FLAT_USD:
            raise ValueError(t('resolution_unsupported'))
        return HAILUO_FLAT_USD[key]
    prices = spec.current_prices(today) if prices is None else prices
    if resolution not in prices:
        raise ValueError(t('resolution_unsupported'))
    return round(int(duration) * prices[resolution], 3)


def poll_url(api_base, task_id, poll_kind='unified'):
    if poll_kind == 'kling_t2v':
        return api_base + '/kling/v1/videos/text2video/' + task_id
    if poll_kind == 'kling_i2v':
        return api_base + '/kling/v1/videos/image2video/' + task_id
    if poll_kind == 'h3':
        return api_base + '/minimax/v2/query/video_generation/' + task_id
    if poll_kind == 'vidu':
        return api_base + '/vidu/ent/v2/tasks/' + task_id + '/creations'
    return api_base + '/v1/videos/' + task_id


def submit_url(api_base, request):
    path = request.pop('_submit_path', None)
    if path:
        return api_base + path
    spec = MODELS.get(request.get('model') or request.get('model_name') or DEFAULT_MODEL_ID)
    if spec and spec.family in ('kling', 'h3', 'vidu'):
        return api_base + spec.submit_path
    return api_base + '/v1/videos'


def extract_task_id(body, family='wan'):
    body = body if isinstance(body, dict) else {}
    if family == 'kling':
        data = body.get('data') if isinstance(body.get('data'), dict) else {}
        task_id = data.get('task_id') or body.get('task_id') or body.get('id')
    elif family == 'h3':
        output = body.get('output') if isinstance(body.get('output'), dict) else {}
        task_id = body.get('task_id') or output.get('task_id') or body.get('id')
        if task_id is not None:
            task_id = str(task_id)
    elif family == 'vidu':
        task_id = body.get('task_id') or body.get('id')
    else:
        task_id = body.get('id') or body.get('task_id')
    return task_id if isinstance(task_id, str) and task_id else None


def normalize_status(body, family='wan'):
    """Return (status_lower, progress_or_None, video_url_or_empty)."""
    body = body if isinstance(body, dict) else {}
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
    if family == 'h3':
        task = body.get('task') if isinstance(body.get('task'), dict) else body
        raw = str(task.get('status') or '').strip().lower()
        mapping = {
            'pending': 'queued',
            'queueing': 'queued',
            'processing': 'in_progress',
            'running': 'in_progress',
            'succeed': 'succeeded',
            'success': 'succeeded',
            'succeeded': 'succeeded',
            'failed': 'failed',
        }
        status = mapping.get(raw, raw)
        url = ''
        content = task.get('content') if isinstance(task.get('content'), dict) else {}
        candidate = content.get('url') or task.get('video_url') or ''
        if isinstance(candidate, str) and candidate.startswith('https://'):
            url = candidate
        return status, None, url
    if family == 'vidu':
        raw = str(body.get('state') or body.get('status') or '').strip().lower()
        mapping = {
            'created': 'queued',
            'queueing': 'queued',
            'processing': 'in_progress',
            'success': 'succeeded',
            'succeeded': 'succeeded',
            'failed': 'failed',
        }
        status = mapping.get(raw, raw)
        url = ''
        creations = body.get('creations') if isinstance(body.get('creations'), list) else []
        if creations and isinstance(creations[0], dict):
            candidate = creations[0].get('url') or ''
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

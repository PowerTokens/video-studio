"""Extract a near-end video frame and encode it for each model's I2V input."""
from __future__ import annotations

import base64
import re
import shutil
import subprocess
from pathlib import Path

from i18n import t
from models import get_model
from wan_core import TaskError

MAX_FRAME_BYTES = 4 * 1024 * 1024
# Avoid the very last frame (often faded / incomplete); pull slightly earlier.
CHAIN_FRAME_OFFSET_S = 0.5


def find_ffmpeg():
    """Prefer the bundled imageio-ffmpeg binary; fall back to ffmpeg on PATH."""
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).is_file():
            return exe
    except Exception:
        pass
    return shutil.which('ffmpeg')


def chain_frame_path_for_video(video_path):
    """Sidecar preview next to the clip: <stem>_chain_frame.jpg."""
    video_path = Path(video_path)
    return video_path.with_name(video_path.stem + '_chain_frame.jpg')


def probe_duration_seconds(ffmpeg, video_path):
    """Best-effort duration from ffmpeg -i stderr. Returns float seconds or None."""
    try:
        result = subprocess.run(
            [ffmpeg, '-hide_banner', '-i', str(video_path)],
            capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (result.stderr or b'').decode('utf-8', errors='replace')
    match = re.search(r'Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)', text)
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def seek_time_before_end(duration, offset=CHAIN_FRAME_OFFSET_S):
    """Clamp to max(0, duration - offset) so short clips still work."""
    if duration is None:
        return None
    try:
        duration = float(duration)
    except (TypeError, ValueError):
        return None
    if duration < 0:
        return None
    return max(0.0, duration - float(offset))


def extract_chain_frame(video_path, out_path=None):
    """Grab a frame ~0.5s before the end; write JPEG next to the video by default."""
    video_path = Path(video_path)
    if out_path is None:
        out_path = chain_frame_path_for_video(video_path)
    else:
        out_path = Path(out_path)
    if not video_path.is_file():
        raise TaskError(t('chain_no_video'), 'PARAM')
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise TaskError(t('chain_need_ffmpeg'), 'PARAM')
    out_path.parent.mkdir(parents=True, exist_ok=True)
    duration = probe_duration_seconds(ffmpeg, video_path)
    seek = seek_time_before_end(duration)
    attempts = []
    if seek is not None:
        # Accurate seek after -i is slower but reliable for short clips.
        attempts.append([
            ffmpeg, '-hide_banner', '-loglevel', 'error', '-y',
            '-i', str(video_path), '-ss', '%.3f' % seek,
            '-frames:v', '1', '-q:v', '3', str(out_path),
        ])
    # Fallback when duration is unknown (or seek failed): 0.5s before EOF.
    attempts.append([
        ffmpeg, '-hide_banner', '-loglevel', 'error', '-y',
        '-sseof', '-%.3f' % CHAIN_FRAME_OFFSET_S, '-i', str(video_path),
        '-frames:v', '1', '-q:v', '3', str(out_path),
    ])
    # Last resort: near-start thumbnail if the clip is extremely short.
    attempts.append([
        ffmpeg, '-hide_banner', '-loglevel', 'error', '-y',
        '-i', str(video_path),
        '-vf', 'thumbnail', '-frames:v', '1', '-q:v', '3', str(out_path),
    ])
    last_err = None
    for cmd in attempts:
        try:
            if out_path.exists():
                out_path.unlink()
        except OSError:
            pass
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=180)
            if out_path.is_file() and out_path.stat().st_size >= 32:
                break
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            last_err = exc
    else:
        raise TaskError(t('chain_extract_failed'), 'PARAM') from last_err
    if out_path.stat().st_size > MAX_FRAME_BYTES:
        raise TaskError(t('chain_frame_too_large'), 'PARAM')
    return out_path


# Back-compat alias used by older call sites / tests
def extract_last_frame(video_path, out_path=None):
    return extract_chain_frame(video_path, out_path)



def encode_local_image_as_data_url(path):
    """Load a local image, re-encode JPEG ≤ MAX_FRAME_BYTES, return a data URL.

    Mirrors the web prepareImage path: longest side down to 2048/1536/1280 with
    falling JPEG quality. Requires Pillow for resize; without it, only files
    already ≤ MAX_FRAME_BYTES are accepted as-is.
    """
    import io
    path = Path(path)
    if not path.is_file():
        raise TaskError(t('first_frame_bad_image'), 'PARAM')
    try:
        from PIL import Image, ImageOps
    except ImportError:
        raw = path.read_bytes()
        if len(raw) > MAX_FRAME_BYTES:
            raise TaskError(t('first_frame_too_large'), 'PARAM')
        head = raw[:16]
        if head.startswith(b'\x89PNG\r\n\x1a\n'):
            mime = 'image/png'
        elif head[:6] in (b'GIF87a', b'GIF89a'):
            mime = 'image/gif'
        elif head.startswith(b'\xff\xd8'):
            mime = 'image/jpeg'
        elif head.startswith(b'RIFF') and b'WEBP' in raw[:16]:
            mime = 'image/webp'
        else:
            raise TaskError(t('first_frame_bad_image'), 'PARAM')
        return 'data:%s;base64,%s' % (mime, base64.b64encode(raw).decode('ascii'))

    attempts = [(2048, 90), (2048, 80), (1536, 80), (1280, 75)]
    resample = getattr(getattr(Image, 'Resampling', Image), 'LANCZOS', Image.LANCZOS)
    try:
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode in ('RGBA', 'LA'):
                background = Image.new('RGB', im.size, (255, 255, 255))
                background.paste(im, mask=im.split()[-1])
                im = background
            elif im.mode == 'P':
                im = im.convert('RGBA')
                background = Image.new('RGB', im.size, (255, 255, 255))
                background.paste(im, mask=im.split()[-1])
                im = background
            elif im.mode != 'RGB':
                im = im.convert('RGB')
            for max_side, quality in attempts:
                scale = min(1.0, float(max_side) / float(max(im.size)))
                size = im.size if scale >= 1.0 else (
                    max(1, int(round(im.size[0] * scale))),
                    max(1, int(round(im.size[1] * scale))))
                frame = im if size == im.size else im.resize(size, resample)
                buf = io.BytesIO()
                frame.save(buf, format='JPEG', quality=quality, optimize=True)
                data = buf.getvalue()
                if len(data) <= MAX_FRAME_BYTES:
                    return 'data:image/jpeg;base64,%s' % base64.b64encode(data).decode('ascii')
    except TaskError:
        raise
    except Exception as exc:
        raise TaskError(t('first_frame_bad_image'), 'PARAM') from exc
    raise TaskError(t('first_frame_too_large'), 'PARAM')


def data_url_for_image(path, mime='image/jpeg'):
    raw = Path(path).read_bytes()
    return 'data:%s;base64,%s' % (mime, base64.b64encode(raw).decode('ascii'))


def kling_image_value(path_or_data_url):
    text = str(path_or_data_url)
    if text.startswith('data:') and ';base64,' in text:
        return text.split(';base64,', 1)[1]
    if Path(text).is_file():
        return base64.b64encode(Path(text).read_bytes()).decode('ascii')
    return text


def supports_embedded_first_frame(model_id):
    return get_model(model_id).family in ('seedance', 'kling')


def first_frame_url_for_model(model_id, frame_path):
    path = Path(frame_path)
    if not path.is_file():
        raise TaskError(t('chain_extract_failed'), 'PARAM')
    return data_url_for_image(path)


def inject_first_frame_into_payload(model_id, payload, frame_path):
    from wan_core import payload as build_payload
    url = first_frame_url_for_model(model_id, frame_path)
    media = [{'type': 'first_frame', 'url': url}]
    spec = get_model(model_id)
    body = dict(payload or {})
    if spec.family == 'kling':
        prompt = body.get('prompt') or ''
        duration = body.get('duration') or 5
        resolution = '1080p' if body.get('mode') == 'pro' else '720p'
        ratio = body.get('aspect_ratio') or '16:9'
        return build_payload(prompt, duration, resolution, ratio, media=media, model=model_id)
    if spec.family == 'seedance':
        prompt = ''
        for item in body.get('media') or []:
            if item.get('type') == 'text':
                prompt = item.get('text') or ''
                break
        duration = body.get('seconds') or 5
        resolution = body.get('size') or '720p'
        ratio = body.get('ratio') or '16:9'
        return build_payload(prompt, duration, resolution, ratio, media=media, model=model_id)
    prompt = body.get('prompt') or ''
    duration = body.get('seconds') or 5
    resolution = body.get('size') or '720p'
    ratio = body.get('ratio') or '16:9'
    return build_payload(prompt, duration, resolution, ratio, media=media, model=model_id)

"""Extract a video's last frame and encode it for each model's I2V input."""
from __future__ import annotations

import base64
import shutil
import subprocess
from pathlib import Path

from i18n import t
from models import get_model
from wan_core import TaskError

MAX_FRAME_BYTES = 4 * 1024 * 1024


def find_ffmpeg():
    path = shutil.which('ffmpeg')
    if path:
        return path
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def extract_last_frame(video_path, out_path):
    """Write the last decoded frame of video_path to out_path (JPEG)."""
    video_path = Path(video_path)
    out_path = Path(out_path)
    if not video_path.is_file():
        raise TaskError(t('chain_no_video'), 'PARAM')
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise TaskError(t('chain_need_ffmpeg'), 'PARAM')
    out_path.parent.mkdir(parents=True, exist_ok=True)
    attempts = [
        [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y',
         '-sseof', '-0.05', '-i', str(video_path),
         '-frames:v', '1', '-q:v', '3', str(out_path)],
        [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y',
         '-i', str(video_path),
         '-vf', 'thumbnail', '-frames:v', '1', '-q:v', '3', str(out_path)],
    ]
    last_err = None
    for cmd in attempts:
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

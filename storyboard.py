"""Split a script/outline into batch video prompts via PT chat completions."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from i18n import t
from models import DEFAULT_MODEL_ID, get_model, resolve_model_id, snap_params, format_adjust_notice
from wan_core import API_BASE, TaskError, api, estimate_cost, payload

# Free default on PowerTokens; qwen3-max is the paid alternative. Never deepseek-v4 / Kimi.
DEFAULT_TEXT_MODEL = 'glm-4.7-flash'
TEXT_MODEL_QWEN = 'qwen3-max'
FREE_TEXT_MODELS = frozenset({DEFAULT_TEXT_MODEL})
ALLOWED_TEXT_MODELS = (DEFAULT_TEXT_MODEL, TEXT_MODEL_QWEN)

CHAT_PATH = '/v1/chat/completions'


def text_model_label(model_id, lang=None):
    if model_id == DEFAULT_TEXT_MODEL:
        return t('storyboard_text_free')
    if model_id == TEXT_MODEL_QWEN:
        return t('storyboard_text_qwen')
    return model_id


def is_free_text_model(model_id):
    return model_id in FREE_TEXT_MODELS


def extract_json_payload(text):
    """Pull a JSON array/object out of model output (fences, prose, etc.)."""
    if text is None:
        raise ValueError('empty')
    raw = str(text).strip()
    if not raw:
        raise ValueError('empty')
    # Strip ```json ... ``` or ``` ... ```
    fence = re.search(r'```(?:json|JSON)?\s*([\s\S]*?)```', raw)
    if fence:
        raw = fence.group(1).strip()
    # Direct parse
    try:
        return json.loads(raw)
    except ValueError:
        pass
    # First [...] or {...} span
    for opener, closer in (('[', ']'), ('{', '}')):
        start = raw.find(opener)
        end = raw.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(raw[start:end + 1])
            except ValueError:
                continue
    raise ValueError('no-json')


def normalize_rows(data):
    """Accept list, or {clips|rows|items|scenes: [...]}."""
    if isinstance(data, dict):
        for key in ('clips', 'rows', 'items', 'scenes', 'shots', 'data'):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            raise ValueError('no-rows')
    if not isinstance(data, list):
        raise ValueError('no-rows')
    rows = []
    for item in data:
        if not isinstance(item, dict):
            continue
        title = str(item.get('title') or item.get('name') or item.get('id') or '').strip()
        prompt = str(item.get('prompt') or item.get('text') or item.get('description') or '').strip()
        if not prompt:
            continue
        duration = item.get('duration') or item.get('seconds') or item.get('duration_s')
        try:
            duration = int(duration) if duration is not None else None
        except (TypeError, ValueError):
            duration = None
        rows.append({'title': title or ('clip-%03d' % (len(rows) + 1)),
                     'duration': duration, 'prompt': prompt})
    if not rows:
        raise ValueError('empty-rows')
    return rows


def _system_prompt(clip_count, duration, stricter=False):
    count_rule = (
        'Choose a sensible number of clips (about 3–12) from the script length.'
        if not clip_count else
        'Produce exactly %d clips.' % int(clip_count)
    )
    base = (
        'You split a video script or outline into short video-generation prompts.\n'
        'Return ONLY valid JSON (no markdown fences, no commentary).\n'
        'Schema: {"clips":[{"title":"string","duration":%d,"prompt":"string"},...]}\n'
        'Rules:\n'
        '- %s\n'
        '- Each duration is an integer seconds (default %d if unsure).\n'
        '- Every prompt must include the same short character/setting description for continuity.\n'
        '- Prompts are concrete visual directions for an AI video model (camera, action, lighting, sound).\n'
        '- title is a short label; prompt is the full generation text.\n'
    ) % (int(duration), count_rule, int(duration))
    if stricter:
        base += (
            'CRITICAL: Your entire reply must be a single JSON object starting with { and ending with }.\n'
            'Do not wrap it in ```. Do not add any text before or after the JSON.\n'
        )
    return base


def chat_completions(key, messages, text_model=DEFAULT_TEXT_MODEL, temperature=0.3):
    body = {
        'model': text_model,
        'messages': messages,
        'temperature': temperature,
    }
    code, resp = api('POST', API_BASE + CHAT_PATH, key, body)
    if code != 200:
        raise TaskError(t('storyboard_http_error', code or 'network'), 'NETWORK')
    try:
        content = resp['choices'][0]['message']['content']
    except (KeyError, IndexError, TypeError):
        raise TaskError(t('storyboard_parse_error'), 'PARAM')
    return content, resp


def storyboard_from_script(script, key, *, clip_count=0, duration=8, model_id=DEFAULT_MODEL_ID,
                           text_model=DEFAULT_TEXT_MODEL, ratio='9:16', resolution='720p',
                           character_setting=''):
    """Call PT chat API and return (jobs, meta) ready for BatchStore."""
    script = (script or '').strip()
    if not script:
        raise TaskError(t('storyboard_need_script'), 'PARAM')
    if not key:
        raise TaskError(t('storyboard_need_key'), 'AUTH')
    try:
        model_id = resolve_model_id(model_id)
    except KeyError:
        raise TaskError(t('model_unknown', model_id), 'PARAM')
    if text_model not in ALLOWED_TEXT_MODELS:
        # Fall back rather than calling banned models
        text_model = DEFAULT_TEXT_MODEL
    clip_count = int(clip_count or 0)
    duration = int(duration or 8)
    messages = [
        {'role': 'system', 'content': _system_prompt(clip_count, duration, stricter=False)},
        {'role': 'user', 'content': script},
    ]
    content, raw = chat_completions(key, messages, text_model=text_model)
    try:
        data = extract_json_payload(content)
        rows = normalize_rows(data)
    except ValueError:
        # Retry once with a stricter instruction
        messages = [
            {'role': 'system', 'content': _system_prompt(clip_count, duration, stricter=True)},
            {'role': 'user', 'content': script},
            {'role': 'assistant', 'content': content},
            {'role': 'user', 'content': 'Your previous reply was not valid JSON. Reply again with ONLY the JSON object.'},
        ]
        content, raw = chat_completions(key, messages, text_model=text_model, temperature=0.1)
        try:
            data = extract_json_payload(content)
            rows = normalize_rows(data)
        except ValueError:
            raise TaskError(t('storyboard_parse_error'), 'PARAM')

    character_setting = (character_setting or '').strip()
    spec = get_model(model_id)
    jobs = []
    for index, row in enumerate(rows, 1):
        prompt = row['prompt']
        if character_setting and character_setting not in prompt:
            # Match batch_import wrap style lightly without changing identity wrappers globally
            from batch_import import wrap_prompt
            prompt = wrap_prompt(character_setting, prompt)
        dur = row['duration'] if row['duration'] is not None else duration
        new_d, new_r, new_ratio, changes = snap_params(spec, dur, resolution, ratio)
        note = format_adjust_notice(spec, changes) if changes else ''
        try:
            body = payload(prompt, new_d, new_r, new_ratio, model=model_id)
            cost = float(estimate_cost(new_d, new_r, model_id=model_id))
            state = 'queued'
        except TaskError as exc:
            body, cost, state = None, 0, 'invalid'
            note = str(exc)
        import hashlib
        job = dict(
            id=hashlib.sha256(('storyboard:%s:%s' % (index, prompt[:80])).encode()).hexdigest()[:16],
            sheet='storyboard', row=index, title=row['title'], prompt=prompt,
            character_setting=character_setting, state=state, note=note or t('import_defaults'),
            record=None, model=model_id, payload=body, cost=cost,
            output='',  # filled by BatchStore
        )
        jobs.append(job)
    if not jobs:
        raise TaskError(t('storyboard_empty'), 'PARAM')
    meta = {
        'text_model': text_model,
        'video_model': model_id,
        'free_text': is_free_text_model(text_model),
        'raw_preview': (content or '')[:500],
    }
    return jobs, meta


def rows_to_xlsx(jobs, path):
    """Write a minimal xlsx the batch importer accepts."""
    from zipfile import ZipFile, ZIP_DEFLATED
    from xml.sax.saxutils import escape

    headers = ['Title', 'Duration (s)', 'Resolution', 'Aspect ratio', 'Prompt', 'Model']
    rows = [headers]
    for job in jobs:
        req = job.get('payload') or {}
        # payload size may be 720P / 720p
        res = str(req.get('size') or '720p')
        if res.endswith('P') and not res.endswith('p'):
            res = res[:-1] + 'p'
        rows.append([
            job.get('title') or '',
            str(req.get('seconds') or ''),
            res,
            str(req.get('ratio') or req.get('aspect_ratio') or '16:9'),
            job.get('prompt') or '',
            job.get('model') or DEFAULT_MODEL_ID,
        ])

    def col_letter(idx):
        return chr(ord('A') + idx)

    def sheet_xml(data):
        lines = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                 '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>']
        for r_i, row in enumerate(data, 1):
            lines.append('<row r="%d">' % r_i)
            for c_i, val in enumerate(row):
                ref = '%s%d' % (col_letter(c_i), r_i)
                lines.append('<c r="%s" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (
                    ref, escape(str(val))))
            lines.append('</row>')
        lines.append('</sheetData></worksheet>')
        return ''.join(lines)

    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>'''
    rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>'''
    wb_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>'''
    wb = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>
</workbook>'''
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(path, 'w', ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', content_types)
        z.writestr('_rels/.rels', rels)
        z.writestr('xl/workbook.xml', wb)
        z.writestr('xl/_rels/workbook.xml.rels', wb_rels)
        z.writestr('xl/worksheets/sheet1.xml', sheet_xml(rows))
    return path

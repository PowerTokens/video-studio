"""Helpers for the desktop Chat tab: prompts, plan parse, text-model allowlist reuse."""
from __future__ import annotations

from storyboard import extract_json_payload
from models import DEFAULT_MODEL_ID, get_model, resolve_model_id, snap_params

CHEAP_DEFAULT = {
    'model': DEFAULT_MODEL_ID,
    'duration': 5,
    'resolution': '720p',
    'ratio': '9:16',
}


def build_system_prompt(lang='zh', catalog=None):
    """System prompt for multi-turn chat → optional single-clip plan JSON."""
    cheap = CHEAP_DEFAULT
    catalog = catalog or cheap['model']
    if lang == 'zh':
        return (
            '你是 PowerTokens Video Studio 的出片助手。用简洁中文和用户多轮打磨想拍的内容。\n'
            '目标：先把方案聊清楚；满意后再给出可填进生成表的提示词。不要催充值或催立刻生成。\n'
            '底层视频模型目录（只能从中选 model）：%s。\n'
            '默认便宜档：model=%s，duration=%s，resolution=%s，ratio=%s。\n'
            '严禁默认 16 秒或 1080p/4k；除非用户明确要求更长/更高清，否则保持 5 秒、720p 或更低。\n'
            '每轮只回复一个 JSON 对象（不要 markdown 围栏外的闲聊），字段：\n'
            '{"assistant_message":"给用户看的白话","ready":false,"plan":null}\n'
            '前几轮优先提问与改写（ready=false, plan=null），帮用户细化主体/动作/场景/镜头。\n'
            '仅当用户明确说可以了/要提示词/要方案，或信息已足够且用户未再追问时，才设 ready=true 并填 plan：\n'
            '{"title":"短标题","prompt":"完整视频提示词","model":"模型id","duration":5,'
            '"resolution":"720p","ratio":"9:16","summary":"一句话方案摘要"}\n'
            'ready=true 时，assistant_message 应邀请用户继续改，或填进生成 / 拆成批量；不要提积分不足。\n'
            'prompt 要具体（主体、动作、场景、镜头、光影、声音），适合文生视频。'
        ) % (catalog, cheap['model'], cheap['duration'], cheap['resolution'], cheap['ratio'])
    return (
        'You are the PowerTokens Video Studio chat planner. Refine in short turns until the user '
        'is happy, then offer a fillable prompt.\n'
        'Do not push top-up or immediate Generate. Keep chatting and rewriting.\n'
        'Video model catalog (pick model only from this list): %s.\n'
        'Default cheap tier: model=%s, duration=%s, resolution=%s, ratio=%s.\n'
        'Never default to 16s or 1080p/4k; keep 5s and 720p or lower unless the user explicitly asks.\n'
        'Reply with ONE JSON object only each turn (no prose outside JSON):\n'
        '{"assistant_message":"user-facing text","ready":false,"plan":null}\n'
        'Early turns: ask and refine (ready=false, plan=null). Cover subject, action, scene, camera.\n'
        'Only when the user says they are ready / wants the prompt / wants the plan — or details '
        'are enough and they stop asking — set ready=true and fill plan:\n'
        '{"title":"short title","prompt":"full video prompt","model":"model id","duration":5,'
        '"resolution":"720p","ratio":"9:16","summary":"one-line summary"}\n'
        'When ready=true, invite them to keep refining, Fill Generate, or Split to batch. '
        'Never mention low credits or top-up.\n'
        'If info is missing, ready=false, plan=null, and ask in assistant_message.'
    ) % (catalog, cheap['model'], cheap['duration'], cheap['resolution'], cheap['ratio'])


def build_api_messages(history, user_text=None, *, lang='zh', catalog=None):
    """OpenAI-style messages: system + prior turns (+ optional new user turn)."""
    messages = [{'role': 'system', 'content': build_system_prompt(lang=lang, catalog=catalog)}]
    for item in history or []:
        role = (item.get('role') or '').strip()
        content = str(item.get('content') or '')
        if role in ('user', 'assistant') and content:
            messages.append({'role': role, 'content': content})
    if user_text is not None:
        text = str(user_text).strip()
        if text:
            messages.append({'role': 'user', 'content': text})
    return messages


def clamp_plan(raw):
    """Normalize a plan dict; return None if prompt is missing."""
    if not isinstance(raw, dict):
        return None
    plan = {
        'title': str(raw.get('title') or '').strip()[:80],
        'prompt': str(raw.get('prompt') or '').strip(),
        'model': CHEAP_DEFAULT['model'],
        'duration': CHEAP_DEFAULT['duration'],
        'resolution': CHEAP_DEFAULT['resolution'],
        'ratio': CHEAP_DEFAULT['ratio'],
        'summary': str(raw.get('summary') or raw.get('title') or '').strip()[:160],
    }
    if not plan['prompt']:
        return None
    model_raw = raw.get('model')
    if model_raw:
        try:
            plan['model'] = resolve_model_id(str(model_raw))
        except KeyError:
            pass
    try:
        duration = int(raw.get('duration'))
        if duration > 0:
            plan['duration'] = duration
    except (TypeError, ValueError):
        pass
    if raw.get('resolution'):
        plan['resolution'] = str(raw.get('resolution'))
    if raw.get('ratio'):
        plan['ratio'] = str(raw.get('ratio'))

    res = str(plan['resolution'] or '').lower()
    if res in ('1080', '1080p', '2k', '4k'):
        plan['resolution'] = '720p'
    if int(plan['duration']) >= 16:
        plan['duration'] = CHEAP_DEFAULT['duration']

    try:
        spec = get_model(plan['model'])
        new_d, new_r, new_ratio, _ = snap_params(
            spec, plan['duration'], plan['resolution'], plan['ratio'])
        plan['duration'] = new_d
        plan['resolution'] = new_r
        plan['ratio'] = new_ratio
    except Exception:
        pass
    if not plan['title']:
        plan['title'] = (plan['summary'] or plan['prompt'][:40] or 'clip').strip()
    return plan


def parse_assistant_payload(content, *, fallback_message=''):
    """Parse model JSON into {assistant_message, ready, plan, parse_ok}."""
    try:
        obj = extract_json_payload(content)
    except ValueError:
        text = str(content or '').strip() or fallback_message
        return {
            'assistant_message': text,
            'ready': False,
            'plan': None,
            'parse_ok': False,
        }
    if not isinstance(obj, dict):
        return {
            'assistant_message': str(content or '').strip() or fallback_message,
            'ready': False,
            'plan': None,
            'parse_ok': False,
        }
    msg = str(obj.get('assistant_message') or obj.get('message') or obj.get('reply') or '').strip()
    ready = bool(obj.get('ready'))
    plan = None
    if ready and obj.get('plan'):
        plan = clamp_plan(obj.get('plan'))
    if ready and not plan:
        ready = False
    if not msg:
        msg = fallback_message or (plan['prompt'][:120] if plan else '')
    return {
        'assistant_message': msg,
        'ready': ready,
        'plan': plan,
        'parse_ok': True,
    }


def script_outline_from_chat(messages, plan=None):
    """Build a script/outline string for storyboard_from_script."""
    user_bits = []
    for item in messages or []:
        if (item.get('role') or '') != 'user':
            continue
        text = str(item.get('content') or '').strip()
        if text:
            user_bits.append(text)
    body = '\n\n'.join(user_bits)
    if plan and plan.get('prompt'):
        header = []
        for key in ('title', 'summary', 'prompt'):
            val = str(plan.get(key) or '').strip()
            if val and val not in header:
                header.append(val)
        agreed = '\n'.join(header)
        if body and agreed and agreed not in body:
            return (body + '\n\n---\n' + agreed).strip()
        return (agreed or body).strip()
    return body.strip()


def has_usable_fill_content(plan):
    return bool(plan and str(plan.get('prompt') or '').strip())


def has_usable_batch_content(messages, plan=None):
    return bool(script_outline_from_chat(messages, plan))

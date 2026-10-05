"""Compare one prompt across 2–3 models: plan, cost, output paths, grouping."""
import re
import uuid
from pathlib import Path

from i18n import t
from models import (
    DEFAULT_MODEL_ID, estimate_cost, format_adjust_notice, get_model, list_models,
    resolve_model_id, snap_params,
)
from wan_core import TaskError, payload

MIN_COMPARE_MODELS = 2
MAX_COMPARE_MODELS = 3


def model_short_name(model_id):
    return get_model(model_id).short_name


def parse_model_list(text):
    """Parse comma/semicolon-separated model IDs or display names into unique IDs."""
    parts = [p.strip() for p in re.split(r'[,;|]+', str(text or '')) if p.strip()]
    ids = []
    for part in parts:
        try:
            mid = resolve_model_id(part)
        except KeyError:
            raise TaskError(t('model_unknown', part), 'PARAM')
        if mid not in ids:
            ids.append(mid)
    return ids


def validate_compare_models(model_ids):
    if len(model_ids) < MIN_COMPARE_MODELS:
        raise TaskError(t('compare_need_models', MIN_COMPARE_MODELS), 'PARAM')
    if len(model_ids) > MAX_COMPARE_MODELS:
        raise TaskError(t('compare_max_models', MAX_COMPARE_MODELS), 'PARAM')
    for mid in model_ids:
        get_model(mid)  # raises KeyError if unknown
    return list(model_ids)


def compare_stem(name=''):
    base = re.sub(r'[^\w\-]+', '-', (name or '').strip(), flags=re.UNICODE).strip('-')
    if not base:
        base = 'cmp'
    return (base[:40] + '_' + uuid.uuid4().hex[:8]).lower()


def output_path_for(folder, stem, model_id):
    folder = Path(folder)
    return folder / 'compare' / ('%s_%s.mp4' % (stem, model_short_name(model_id)))


def plan_compare(prompt, duration, resolution, ratio, model_ids, media=None, seed='',
                 name='', folder='.'):
    """Build a compare plan with per-model snapped params, notices, costs and paths."""
    model_ids = validate_compare_models(model_ids)
    prompt = (prompt or '').strip()
    if not prompt:
        raise TaskError(t('missing_prompt'), 'PARAM')
    stem = compare_stem(name)
    compare_id = uuid.uuid4().hex[:12]
    items = []
    total = 0.0
    for mid in model_ids:
        spec = get_model(mid)
        new_d, new_r, new_ratio, changes = snap_params(spec, duration, resolution, ratio)
        notice = format_adjust_notice(spec, changes) if changes else ''
        request = payload(prompt, new_d, new_r, new_ratio, media=media, seed=seed, model=mid)
        cost = float(estimate_cost(new_d, new_r, model_id=mid))
        total += cost
        out = output_path_for(folder, stem, mid)
        items.append({
            'model_id': mid,
            'label': spec.label(),
            'short_name': spec.short_name,
            'duration': new_d,
            'resolution': new_r,
            'ratio': new_ratio,
            'changes': changes,
            'notice': notice,
            'cost': cost,
            'request': request,
            'output': str(out),
        })
    return {
        'compare_id': compare_id,
        'stem': stem,
        'prompt': prompt,
        'items': items,
        'total_cost': round(total, 4),
        'folder': str(Path(folder) / 'compare'),
    }


def run_compare(client_factory, keys, plan, report=lambda text: None, stop=None):
    """Submit each compare item as a normal saved task (sequential).

    client_factory() must return a fresh Client; each item gets context.compare_id.
    Returns list of {model_id, ok, record|error}.
    """
    import threading
    stop = stop or threading.Event()
    results = []
    for item in plan['items']:
        if stop.is_set():
            results.append({'model_id': item['model_id'], 'ok': False, 'error': t('submit_stopped')})
            continue
        report(t('compare_submitting', item['label']))
        client = client_factory()
        client.context = dict(getattr(client, 'context', None) or {},
                              compare_id=plan['compare_id'],
                              compare_stem=plan['stem'],
                              model=item['model_id'])
        try:
            record = client.submit(keys, dict(item['request']), item['output'])
            results.append({'model_id': item['model_id'], 'ok': True, 'record': record,
                            'output': item['output']})
            report(t('compare_item_done', item['label']))
        except TaskError as exc:
            results.append({'model_id': item['model_id'], 'ok': False, 'error': str(exc),
                            'task_id': getattr(exc, 'task_id', ''), 'output': item['output']})
            report(t('compare_item_failed', item['label'], str(exc)))
        except Exception as exc:
            results.append({'model_id': item['model_id'], 'ok': False, 'error': str(exc),
                            'output': item['output']})
            report(t('compare_item_failed', item['label'], str(exc)))
    return results

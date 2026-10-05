#!/usr/bin/env python3
"""PowerTokens Video Studio CLI. Shares the same engine as the Windows app."""
import argparse
import json
import os
from pathlib import Path
import sys
from i18n import is_zh, t
from input_helpers import parse_keys
from models import DEFAULT_MODEL_ID, MODELS, get_model, list_models, resolve_model_id
from compare_core import parse_model_list, plan_compare, run_compare
from wan_core import (API_BASE, Client, DATA_DIR, LIST_PRICE_USD_PER_SECOND, MODEL, PROMO_END_DATE, PRICE_CHECKED_DATE,
                      PRICE_SOURCE_URL, PRICE_SOURCE_URL_EN, promo_active, current_prices, list_prices,
                      TaskError, api, atomic_json, category, estimate_cost, payload)

MODEL_ID = MODEL
CONFIG_PATH = Path(os.environ.get('PT_WAN_CONFIG', str(DATA_DIR / 'cli-config.json')))


def load_config():
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    legacy = Path.home() / '.config' / 'powertokens-wan' / 'config.json'
    if legacy.exists() and 'PT_WAN_CONFIG' not in os.environ:
        return json.loads(legacy.read_text(encoding='utf-8'))
    return {}


def save_config(data):
    atomic_json(CONFIG_PATH, data)
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass


def resolve_keys():
    raw = os.environ.get('POWERTOKENS_API_KEYS') or os.environ.get('POWERTOKENS_API_KEY')
    keys = raw.split(',') if raw else load_config().get('api_keys', [])
    return list(dict.fromkeys(k.strip() for k in keys if k.strip()))


def mask_key(key):
    return '****' + key[-4:]


def emit(value):
    print(json.dumps(value, ensure_ascii=False))


def models_payload():
    """JSON list of supported models with localized descriptions (no prices)."""
    items = []
    for spec in list_models():
        items.append({
            'id': spec.id,
            'name': spec.label(),
            'description': spec.description(),
            'durations': [spec.durations[0], spec.durations[-1]],
            'resolutions': list(spec.resolutions),
            'ratios': list(spec.ratios),
            'default': spec.id == DEFAULT_MODEL_ID,
        })
    return {'models': items, 'default_model': DEFAULT_MODEL_ID}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--list-models', action='store_true', help=t('cli_list_models_help'))
    subs = parser.add_subparsers(dest='command', required=False)
    subs.add_parser('check')
    subs.add_parser('list-models', help=t('cli_list_models_help'))
    verify = subs.add_parser('verify')
    verify.add_argument('--full', action='store_true', help=t('cli_full_help'))
    subs.add_parser('prune')
    config = subs.add_parser('config')
    config.add_argument('--add-key')
    config.add_argument('--remove-key')
    config.add_argument('--list-keys', action='store_true')
    config.add_argument('--show', action='store_true')
    model_choices = list(MODELS)
    for name in ('generate', 'estimate'):
        item = subs.add_parser(name)
        item.add_argument('-d', '--duration', type=int, default=5)
        item.add_argument('-r', '--resolution', default='720p')
        item.add_argument('-m', '--model', default=MODEL, help=t('cli_model_help'))
        if name == 'generate':
            item.add_argument('-p', '--prompt', default='')
            item.add_argument('--ratio', default='16:9')
            item.add_argument('--seed', default='')
            item.add_argument('-o', '--output')
            item.add_argument('-i', '--image', '--first-frame', dest='first_frame')
            for field in ('last-frame', 'reference-image', 'reference-video', 'reference-audio'):
                item.add_argument('--' + field)
    compare = subs.add_parser('compare', help=t('cli_compare_help'))
    compare.add_argument('--models', '-m', required=True, help=t('cli_compare_models_help'))
    compare.add_argument('--prompt', '-p', required=True)
    compare.add_argument('--duration', '-d', type=int, default=5)
    compare.add_argument('--resolution', '-r', default='720p')
    compare.add_argument('--ratio', default='16:9')
    compare.add_argument('--output-dir', '-o', default='')
    compare.add_argument('--name', default='')
    compare.add_argument('--seed', default='')
    for media_name in ('first_frame', 'last_frame', 'reference_image', 'reference_video', 'reference_audio'):
        compare.add_argument('--' + media_name.replace('_', '-'), default='', dest=media_name)

    resume = subs.add_parser('resume')
    resume.add_argument('task_id')
    resume.add_argument('-o', '--output', required=True)
    resume.add_argument('--key-index', type=int, default=1, help=t('cli_key_index_help'))
    args = parser.parse_args()
    if args.list_models or args.command == 'list-models':
        emit(models_payload())
        return 0
    if not args.command:
        parser.error(t('cli_command_required'))
    if args.command == 'config':
        cfg = load_config()
        pool = cfg.get('api_keys', [])
        if args.add_key:
            try:
                incoming = parse_keys(args.add_key)
            except ValueError as exc:
                raise TaskError(str(exc), 'PARAM')
            pool = list(dict.fromkeys(pool + incoming))
        if args.remove_key:
            matches = [k for k in pool if k == args.remove_key or (len(args.remove_key) == 4 and k.endswith(args.remove_key))]
            if len(matches) > 1:
                raise TaskError(t('cli_same_suffix'), 'PARAM')
            pool = [k for k in pool if k not in matches]
        if args.add_key or args.remove_key:
            cfg['api_keys'] = pool
            save_config(cfg)
        emit({'config_pool': [mask_key(k) for k in pool], 'effective_pool': [mask_key(k) for k in resolve_keys()], 'config_path': str(CONFIG_PATH)})
        return 0
    if args.command in ('estimate', 'generate'):
        try:
            model_id = resolve_model_id(args.model)
        except KeyError:
            raise TaskError(t('model_unknown', args.model), 'PARAM')
        args.model = model_id
    if args.command == 'estimate':
        spec = get_model(args.model)
        if args.duration not in spec.durations:
            raise TaskError(t('duration_not_allowed', spec.label(), spec.durations[0], spec.durations[-1]), 'PARAM')
        if args.resolution not in spec.resolutions:
            raise TaskError(t('resolution_not_allowed', spec.label(), ', '.join(spec.resolutions)), 'PARAM')
        result = {'model': args.model, 'est_cost_usd': estimate_cost(args.duration, args.resolution, model_id=args.model),
                  'price_checked_date': PRICE_CHECKED_DATE,
                  'price_source': spec.price_source()}
        regular = list_prices(model_id=args.model)
        if args.resolution in regular:
            result['list_cost_usd'] = estimate_cost(args.duration, args.resolution, prices=regular)
            if args.model == DEFAULT_MODEL_ID and promo_active():
                result['promo_end_date'] = PROMO_END_DATE.isoformat()
        result['note'] = t('cli_estimate_note')
        emit(result)
        return 0
    keys = resolve_keys()
    if args.command == 'check':
        emit({'key_pool_size': len(keys), 'keys_masked': [mask_key(k) for k in keys],
              'models': list(MODELS), 'default_model': DEFAULT_MODEL_ID, 'ready': bool(keys)})
        return 0 if keys else 2
    if not keys:
        emit({'error': t('cli_need_key'), 'error_type': 'AUTH'})
        return 2
    if args.command in ('verify', 'prune'):
        results, invalid = [], []
        for key in keys:
            code, body = api('GET', API_BASE + '/v1/models', key)
            kind = category(code, body)
            confirmed_invalid = code == 401 and kind == 'AUTH'
            alive = True if code == 200 else (False if confirmed_invalid else None)
            results.append({'key': mask_key(key), 'alive': alive, 'reason': 'ok' if alive else ('AUTH' if confirmed_invalid else t('cli_unconfirmed'))})
            if confirmed_invalid:
                invalid.append(key)
        if args.command == 'prune':
            cfg = load_config()
            cfg['api_keys'] = [k for k in cfg.get('api_keys', []) if k not in invalid]
            save_config(cfg)
        emit({'results': results, 'note': t('cli_prune_note')})
        return 5 if any(r['alive'] is not True for r in results) else 0
    if args.command == 'compare':
        model_ids = parse_model_list(args.models)
        media = [{'type': name, 'url': getattr(args, name)} for name in
                 ('first_frame', 'last_frame', 'reference_image', 'reference_video', 'reference_audio')
                 if getattr(args, name, '')]
        folder = args.output_dir or str(Path.cwd())
        plan = plan_compare(args.prompt, args.duration, args.resolution, args.ratio, model_ids,
                            media=media, seed=args.seed, name=args.name, folder=folder)
        if os.environ.get('PT_DRY_RUN'):
            emit({'dry_run': True, 'compare_id': plan['compare_id'], 'total_cost_usd': plan['total_cost'],
                  'items': [dict({k: item[k] for k in ('model_id', 'duration', 'resolution', 'ratio', 'cost', 'notice', 'output')},
                       request=item['request']) for item in plan['items']]})
            return 0
        def factory():
            return Client(report=lambda message: print(message, file=sys.stderr, flush=True))
        results = run_compare(factory, keys, plan,
                              report=lambda message: print(message, file=sys.stderr, flush=True))
        emit({'compare_id': plan['compare_id'], 'total_cost_usd': plan['total_cost'],
              'folder': plan['folder'], 'results': [
                  {'model_id': r['model_id'], 'ok': r['ok'],
                   'task_id': (r.get('record') or {}).get('task_id') or r.get('task_id') or '',
                   'output': r.get('output') or '',
                   'error': r.get('error') or ''} for r in results]})
        return 0 if all(r.get('ok') for r in results) else 4

    client = Client(report=lambda message: print(message, file=sys.stderr, flush=True))
    if args.command == 'resume':
        if not 1 <= args.key_index <= len(keys):
            raise TaskError(t('cli_key_index'), 'PARAM')
        result = client.resume(keys[args.key_index - 1], args.task_id, args.output)
    else:
        media = [{'type': name, 'url': getattr(args, name)} for name in ('first_frame', 'last_frame', 'reference_image', 'reference_video', 'reference_audio') if getattr(args, name)]
        request = payload(args.prompt, args.duration, args.resolution, args.ratio, media, args.seed, model=args.model)
        if os.environ.get('PT_DRY_RUN'):
            emit({'dry_run': True, 'payload': request})
            return 0
        import uuid
        output = args.output or str(Path.cwd() / ('wan_' + uuid.uuid4().hex[:12] + '.mp4'))
        result = client.submit(keys, request, output)
    emit(result)
    return 0


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    try:
        sys.exit(main())
    except TaskError as exc:
        emit({'error': str(exc), 'error_type': exc.kind, 'task_id': exc.task_id})
        sys.exit(3 if exc.kind == 'PARAM' else 4)
    except (OSError, ValueError):
        emit({'error': t('cli_io_error')})
        sys.exit(4)

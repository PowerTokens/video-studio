#!/usr/bin/env python3
"""PowerTokens Video Studio CLI. Shares the same engine as the Windows app."""
import argparse
import json
import os
from pathlib import Path
import sys
from input_helpers import parse_keys
from wan_core import (API_BASE, Client, DATA_DIR, LIST_PRICE_USD_PER_SECOND, MODEL, PROMO_END_DATE, PRICE_CHECKED_DATE,
                      PRICE_SOURCE_URL, promo_active,
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='command', required=True)
    subs.add_parser('check')
    verify = subs.add_parser('verify')
    verify.add_argument('--full', action='store_true', help='兼容旧参数；网络故障仍不判定 Key 失效')
    subs.add_parser('prune')
    config = subs.add_parser('config')
    config.add_argument('--add-key')
    config.add_argument('--remove-key')
    config.add_argument('--list-keys', action='store_true')
    config.add_argument('--show', action='store_true')
    for name in ('generate', 'estimate'):
        item = subs.add_parser(name)
        item.add_argument('-d', '--duration', type=int, default=5)
        item.add_argument('-r', '--resolution', choices=['720p', '1080p'], default='720p')
        if name == 'generate':
            item.add_argument('-p', '--prompt', default='')
            item.add_argument('-m', '--model', default=MODEL)
            item.add_argument('--ratio', choices=['16:9', '9:16', '1:1'], default='16:9')
            item.add_argument('--seed', default='')
            item.add_argument('-o', '--output')
            item.add_argument('-i', '--image', '--first-frame', dest='first_frame')
            for field in ('last-frame', 'reference-image', 'reference-video', 'reference-audio'):
                item.add_argument('--' + field)
    resume = subs.add_parser('resume')
    resume.add_argument('task_id')
    resume.add_argument('-o', '--output', required=True)
    resume.add_argument('--key-index', type=int, default=1, help='原任务 Key 在池中的序号，从 1 开始')
    args = parser.parse_args()
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
                raise TaskError('多个 Key 尾号相同，请使用完整 Key 删除', 'PARAM')
            pool = [k for k in pool if k not in matches]
        if args.add_key or args.remove_key:
            cfg['api_keys'] = pool
            save_config(cfg)
        emit({'config_pool': [mask_key(k) for k in pool], 'effective_pool': [mask_key(k) for k in resolve_keys()], 'config_path': str(CONFIG_PATH)})
        return 0
    if args.command == 'estimate':
        if not 2 <= args.duration <= 30:
            raise TaskError('时长须为 2–30 秒', 'PARAM')
        result = {'model': MODEL, 'est_cost_usd': estimate_cost(args.duration, args.resolution),
                  'price_checked_date': PRICE_CHECKED_DATE, 'price_source': PRICE_SOURCE_URL}
        if promo_active():
            result['list_cost_usd'] = estimate_cost(args.duration, args.resolution, LIST_PRICE_USD_PER_SECOND)
            result['promo_end_date'] = PROMO_END_DATE.isoformat()
        result['note'] = '按 PT 当前公示单价估算；实际扣费以平台账单为准'
        emit(result)
        return 0
    if args.command == 'generate' and args.model != MODEL:
        raise TaskError('模型仅支持 ' + MODEL, 'PARAM')
    keys = resolve_keys()
    if args.command == 'check':
        emit({'key_pool_size': len(keys), 'keys_masked': [mask_key(k) for k in keys], 'model_locked': MODEL, 'ready': bool(keys)})
        return 0 if keys else 2
    if not keys:
        emit({'error': '请配置 API Key', 'error_type': 'AUTH'})
        return 2
    if args.command in ('verify', 'prune'):
        results, invalid = [], []
        for key in keys:
            code, body = api('GET', API_BASE + '/v1/models', key)
            kind = category(code, body)
            confirmed_invalid = code == 401 and kind == 'AUTH'
            alive = True if code == 200 else (False if confirmed_invalid else None)
            results.append({'key': mask_key(key), 'alive': alive, 'reason': 'ok' if alive else ('AUTH' if confirmed_invalid else '未能确认；保留 Key')})
            if confirmed_invalid:
                invalid.append(key)
        if args.command == 'prune':
            cfg = load_config()
            cfg['api_keys'] = [k for k in cfg.get('api_keys', []) if k not in invalid]
            save_config(cfg)
        emit({'results': results, 'note': '只移除已确认 401 失效的本地配置 Key；环境变量不会修改'})
        return 5 if any(r['alive'] is not True for r in results) else 0
    client = Client(report=lambda message: print(message, file=sys.stderr, flush=True))
    if args.command == 'resume':
        if not 1 <= args.key_index <= len(keys):
            raise TaskError('Key 序号不在池中', 'PARAM')
        result = client.resume(keys[args.key_index - 1], args.task_id, args.output)
    else:
        media = [{'type': name, 'url': getattr(args, name)} for name in ('first_frame', 'last_frame', 'reference_image', 'reference_video', 'reference_audio') if getattr(args, name)]
        request = payload(args.prompt, args.duration, args.resolution, args.ratio, media, args.seed)
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
        emit({'error': '本地配置或文件读写失败。已提交的任务请从任务记录恢复。'})
        sys.exit(4)

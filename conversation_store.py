"""Bounded, atomic persistence for user-authored conversation/form content."""
import json
import os
import time
from wan_core import DATA_DIR

MAX_BYTES = 512000
TTL = 2 * 60 * 60


def read(name, data_dir=None):
    try:
        path = (data_dir or DATA_DIR) / (name + '.json')
        if path.stat().st_size > MAX_BYTES:
            return None
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def write(name, value, data_dir=None):
    path = (data_dir or DATA_DIR) / (name + '.json')
    try:
        text = json.dumps(value, ensure_ascii=False)
        if len(text.encode('utf-8')) > MAX_BYTES:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix('.tmp')
        with temp.open('w', encoding='utf-8') as stream:
            stream.write(text)
        os.replace(temp, path)
    except OSError:
        pass


def clear(name, data_dir=None):
    try:
        ((data_dir or DATA_DIR) / (name + '.json')).unlink(missing_ok=True)
    except OSError:
        pass


def intended(data_dir=None):
    state = read('intended_generate', data_dir)
    if state and 0 <= time.time() - state.get('at', 0) < TTL:
        return state.get('form')
    clear('intended_generate', data_dir)
    return None


def estimate_credits(usd):
    import math
    return math.ceil(float(usd) * 1000)


def suggested_amount(need, have):
    return 5 if need - have <= 5000 else 20 if need - have <= 20000 else 50

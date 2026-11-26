"""Local checkout references and order-scoped terminal deduplication.

No credentials or checkout responses are written here. All mutation is performed
on the Tk thread; HTTP work is dispatched by payment_ui.
"""
import hashlib
import json
import math
import os
import time
from platform_paths import user_data_dir
import analytics
import payment

TTL = 48 * 60 * 60


def owner(session):
    profile = (session or {}).get('profile') or {}
    identity = profile.get('user_id') or profile.get('sub')
    return hashlib.sha256(str(identity).encode()).hexdigest() if identity else None


def credits(info):
    value = (info or {}).get('credits')
    try:
        value = float(value)
        return value if math.isfinite(value) and not isinstance((info or {}).get('credits'), bool) else None
    except (TypeError, ValueError, OverflowError):
        return None


class PendingOrders:
    def __init__(self, path=None, clock=time.time):
        self.path = path or user_data_dir() / 'pending-payments.json'
        self.clock = clock
        self.pending = {}
        self.completed = set()
        try:
            data = json.loads(self.path.read_text())
            self.completed = {key for key in data.get('completed', []) if isinstance(key, str)}
            for key, record in data.get('pending', {}).items():
                if (isinstance(record, dict) and set(record) ==
                        {'owner', 'order', 'baseline', 'package_id', 'channel', 'created_at'}
                        and isinstance(record['owner'], str) and isinstance(record['order'], str)
                        and key == self.key(record['owner'], record['order'])
                        and type(record['created_at']) in (int, float)
                        and math.isfinite(record['created_at'])):
                    self.pending[key] = record
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        self.prune()

    @staticmethod
    def key(account, order):
        return hashlib.sha256((str(account) + ':' + order).encode()).hexdigest()

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            with open(temporary, 'w', encoding='utf-8') as stream:
                json.dump(dict(pending=self.pending, completed=sorted(self.completed)), stream)
            os.replace(temporary, self.path)
        except (OSError, ValueError, TypeError):
            pass

    def prune(self):
        self.pending = {key: record for key, record in self.pending.items()
                        if key not in self.completed and 0 <= self.clock() - record['created_at'] < TTL}
        self.save()

    def add(self, account, order, baseline, package_id, channel):
        key = self.key(account, order)
        if key not in self.completed and key not in self.pending:
            self.pending[key] = dict(owner=account, order=order, baseline=baseline,
                                     package_id=package_id, channel=channel, created_at=self.clock())
            self.save()
        return key

    def apply(self, key, status, info):
        self.prune()
        record = self.pending.get(key)
        if not record:
            return False
        baseline = credits({'credits': record['baseline']})
        current = credits(info)
        if status == 0 and baseline is not None and current is not None and current > baseline:
            status = 1
        if status not in (1, 2):
            return False
        # Persist the claim first to suppress duplicate terminal events after restart.
        self.completed.add(key)
        self.save()
        payment.track_paid_status(record, status)
        try:
            analytics.flush()
        except Exception:
            pass
        finally:
            self.pending.pop(key, None)
            self.save()
        return True

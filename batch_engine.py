"""Persistent bounded batch runner. Never automatically resubmits uncertain jobs."""
import copy
import json
import os
from pathlib import Path
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from collections.abc import Mapping
from batch_import import batch_identity
from i18n import t
from wan_core import Client, DATA_DIR, KeyPool, TaskError, atomic_json, fingerprint

STATES = ('queued', 'running', 'completed', 'deferred_403', 'paused', 'uncertain', 'failed', 'invalid', 'no_key')


class StateLabels(Mapping):
    """Row state -> label in the current UI language (read at lookup time)."""
    def __getitem__(self, state):
        if state not in STATES:
            raise KeyError(state)
        return t('state_' + state)

    def __iter__(self):
        return iter(STATES)

    def __len__(self):
        return len(STATES)


LABELS = StateLabels()


def safe_name(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', value).strip(' .')
    return value[:55] or 'video'


class BatchLease:
    """One operating-system lock per batch, released automatically after a crash."""
    def __init__(self, path):
        self.path = Path(path)
        self.file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open('a+b')
        if self.path.stat().st_size == 0:
            self.file.write(b'0')
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise ValueError(t('batch_locked'))
        return self

    def __exit__(self, *args):
        try:
            self.file.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        finally:
            self.file.close()


class BatchStore:
    def __init__(self, jobs, output_dir, source='', data_dir=None):
        self.data_dir = Path(data_dir) if data_dir else DATA_DIR
        self.id = batch_identity(jobs)
        self.path = self.data_dir / 'batches' / (self.id + '.json')
        self.lock = threading.RLock()
        with BatchLease(self.path.with_suffix('.lock')):
            self.reused = self.path.exists()
            if self.reused:
                self.data = json.loads(self.path.read_text(encoding='utf-8'))
                if self.data.get('id') != self.id or len(self.data.get('jobs', [])) != len(jobs):
                    raise ValueError(t('batch_corrupt'))
                self.recover()
            else:
                self.data = dict(id=self.id, source=str(source), created=time.strftime('%Y-%m-%d %H:%M:%S'), jobs=copy.deepcopy(jobs))
                folder = Path(output_dir).absolute() / ('batch_' + self.id[:8])
                for index, job in enumerate(self.data['jobs'], 1):
                    job['output'] = str(folder / ('%03d_%s.mp4' % (index, safe_name(job['title']))))
                self.persist()

    def persist(self):
        atomic_json(self.path, self.data)

    def recover(self):
        # Recover core task checkpoints even if the process stopped between two file writes.
        recovered = {}
        for path in (self.data_dir / 'tasks').glob('*.json'):
            try:
                row = json.loads(path.read_text(encoding='utf-8'))
                if row.get('batch_id') == self.id and row.get('task_id'):
                    recovered[row.get('batch_job_id')] = row
            except (OSError, ValueError):
                continue
        for job in self.data['jobs']:
            if job['state'] in ('running', 'uncertain'):
                record = recovered.get(job['id']) or job.get('record')
                job['record'] = record
                if record and record.get('task_id'):
                    job['state'] = 'paused'
                    job['note'] = t('note_recovered_id')
                else:
                    job['state'] = 'uncertain'
                    job['note'] = t('note_interrupted')
        self.persist()

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.data['jobs'])

    def update(self, job_id, **changes):
        with self.lock:
            job = next(j for j in self.data['jobs'] if j['id'] == job_id)
            job.update(changes)
            self.persist()
            return copy.deepcopy(job)


class BatchRunner:
    def __init__(self, store, keys, concurrency=3, stop=None, notify=lambda job: None, client_factory=Client, download_limit=2):
        if not 1 <= int(concurrency) <= 8:
            raise ValueError(t('concurrency_range'))
        if not 1 <= int(download_limit) <= 4:
            raise ValueError(t('download_range'))
        if not keys:
            raise ValueError(t('add_api_key'))
        self.store, self.keys = store, list(keys)
        self.concurrency = int(concurrency)
        self.stop = stop or threading.Event()
        self.notify = notify
        self.client_factory = client_factory
        self.download_slots = threading.BoundedSemaphore(int(download_limit))
        self.key_pool = KeyPool(self.keys)
        self.pool_exhausted = threading.Event()

    def update(self, job_id, **changes):
        job = self.store.update(job_id, **changes)
        self.notify(job)
        return job

    def eligible(self, job):
        return job['state'] == 'queued' or (job['state'] in ('paused', 'no_key', 'deferred_403') and bool((job.get('record') or {}).get('task_id')))

    def run_one(self, job, index, retry_deferred=False):
        if self.stop.is_set():
            return
        start = time.monotonic()
        record = job.get('record') or {}
        task_id = record.get('task_id')
        if task_id:
            key = next((k for k in self.keys if fingerprint(k) == record.get('key_hash')), None)
            if not key:
                self.update(job['id'], state='no_key', note=t('note_need_key') + record.get('key_hint', ''))
                return
        # Persist intent BEFORE any chargeable POST. A crash cannot silently requeue it.
        self.update(job['id'], state='running', note=t('note_resuming') if task_id else t('note_preparing'), started=time.strftime('%Y-%m-%d %H:%M:%S'))
        client = None
        try:
            def checkpoint(row):
                self.update(job['id'], record=row)
            def report(message):
                self.update(job['id'], note=message, elapsed=round(time.monotonic() - start, 1))
            if self.pool_exhausted.is_set() and not task_id:
                self.update(job['id'], state='paused', error_kind='POOL_EXHAUSTED',
                            note=t('batch_keys_exhausted'), elapsed=round(time.monotonic() - start, 1))
                return
            client = self.client_factory(report=report, stop=self.stop, data_dir=self.store.data_dir,
                        on_record=checkpoint, context={'batch_id': self.store.id, 'batch_job_id': job['id']},
                        download_slots=self.download_slots, defer_on_403=True, key_pool=self.key_pool)
            if task_id:
                result = client.resume(key, task_id, job['output'], record)
            else:
                offset = index % len(self.keys)
                ordered = self.keys[offset:] + self.keys[:offset]
                result = client.submit(ordered, job['payload'], job['output'])
            self.update(job['id'], state='completed', record=result, note=t('saved_to') + job['output'], elapsed=round(time.monotonic() - start, 1))
        except TaskError as exc:
            latest = getattr(client, 'record', None) or record
            if exc.kind == 'QUERY_FORBIDDEN' and (latest or {}).get('task_id'):
                state = 'paused' if retry_deferred else 'deferred_403'
                note = t('note_403_again') if retry_deferred else str(exc)
                self.update(job['id'], state=state, record=latest, error_kind=exc.kind,
                            note=note, elapsed=round(time.monotonic() - start, 1))
                return
            if exc.kind == 'POOL_EXHAUSTED':
                self.pool_exhausted.set()
                self.update(job['id'], state='paused', record=latest, error_kind=exc.kind,
                            note=t('batch_keys_exhausted'), elapsed=round(time.monotonic() - start, 1))
                self._pause_queued_for_exhausted_pool()
                return
            if (latest or {}).get('task_id'):
                state = 'paused' if exc.kind in ('PAUSED', 'TIMEOUT', 'STORAGE', 'QUERY_UNAVAILABLE') else 'failed'
            elif exc.kind == 'PAUSED':
                state = 'queued'  # Client checks stop before it sends POST.
            elif exc.kind in ('REJECTED', 'PARAM', 'AUTH'):
                state = 'failed'
            else:
                state = 'uncertain'
            self.update(job['id'], state=state, record=latest, error_kind=exc.kind, note=str(exc), elapsed=round(time.monotonic() - start, 1))
        except Exception:
            latest = getattr(client, 'record', None) or record
            state = 'paused' if (latest or {}).get('task_id') else 'uncertain'
            self.update(job['id'], state=state, record=latest,
                        note=t('note_exception'))

    def _pause_queued_for_exhausted_pool(self):
        """Leave remaining queued rows paused with a clear note; do not POST more."""
        for job in self.store.snapshot():
            if job['state'] == 'queued':
                self.update(job['id'], state='paused', error_kind='POOL_EXHAUSTED',
                            note=t('batch_keys_exhausted'))

    def run(self, selected=None):
        self.key_pool = KeyPool(self.keys)
        self.pool_exhausted.clear()
        with BatchLease(self.store.path.with_suffix('.lock')):
            # Another process may have completed rows since this window imported the file.
            with self.store.lock:
                self.store.data = json.loads(self.store.path.read_text(encoding='utf-8'))
                self.store.recover()
            for job in self.store.snapshot():
                self.notify(job)
            return self._run_locked(selected)

    def _run_locked(self, selected=None):
        selected = set(selected) if selected is not None else None
        jobs = [(i, j) for i, j in enumerate(self.store.snapshot())
                if self.eligible(j) and (selected is None or j['id'] in selected)]
        self._execute(jobs)
        if not self.stop.is_set():
            deferred = [(i, j) for i, j in enumerate(self.store.snapshot())
                        if j['state'] == 'deferred_403' and
                        (selected is None or j['id'] in selected)]
            if deferred:
                self._execute(deferred, retry_deferred=True)
        return self.store.snapshot()

    def _execute(self, jobs, retry_deferred=False):
        iterator = iter(jobs)
        with ThreadPoolExecutor(max_workers=self.concurrency) as executor:
            active = set()
            exhausted = False
            while active or not exhausted:
                while (not exhausted and not self.stop.is_set() and not self.pool_exhausted.is_set()
                       and len(active) < self.concurrency):
                    try:
                        index, job = next(iterator)
                    except StopIteration:
                        exhausted = True
                        break
                    active.add(executor.submit(self.run_one, job, index, retry_deferred))
                if self.stop.is_set():
                    exhausted = True
                if not active:
                    break
                done, active = wait(active, timeout=.25, return_when=FIRST_COMPLETED)
                for future in done:
                    future.result()

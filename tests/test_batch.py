import copy
import csv
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile
from batch_import import import_jobs, build_job, batch_identity
from batch_engine import BatchStore, BatchRunner, BatchLease
from batch_ui import select_character_setting
from wan_core import TaskError, fingerprint, atomic_json

PROMPT = '生成一段10秒、9:16竖屏、1080P的短视频。节奏：0-2秒钩子；2-7秒升级；7-10秒反转。'


def jobs(count):
    return [build_job('Prompt库', i + 2, {'duration': 0, 'resolution': 1, 'prompt': 2}, ['10', '1080P', PROMPT + str(i)]) for i in range(count)]


class ImportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)

    def test_csv_template_and_column_priority(self):
        path = self.dir / '模板.csv'
        with path.open('w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['时长(秒)', '分辨率', '完整 Wan 3.0 Prompt'])
            writer.writerow([12, '720P', PROMPT])
        items, skipped = import_jobs(path)
        self.assertEqual(skipped, [])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['payload']['seconds'], '12')
        self.assertEqual(items[0]['payload']['size'], '720P')
        self.assertEqual(items[0]['payload']['ratio'], '9:16')
        self.assertIs(items[0]['payload']['generate_audio'], True)
        self.assertEqual(items[0]['prompt'], PROMPT)

    def test_blank_rows_skipped_invalid_rows_retained(self):
        path = self.dir / 'rows.csv'
        path.write_text('时长,分辨率,Prompt\n10,720p,hi\n,,\n120,1080p,hi\n10,720p,\n', encoding='utf-8')
        rows, _ = import_jobs(path)
        self.assertEqual([r['state'] for r in rows], ['queued', 'invalid', 'invalid'])
        self.assertEqual([r['row'] for r in rows], [2, 4, 5])

    def test_missing_columns_fall_back_to_prompt(self):
        job = build_job('Sheet', 2, {'prompt': 0}, [PROMPT])
        self.assertEqual(job['state'], 'queued')
        self.assertEqual(job['payload']['seconds'], '10')
        self.assertEqual(job['payload']['size'], '1080P')

    def test_shared_character_setting_is_visible_and_changes_batch_identity(self):
        plain = build_job('故事', 2, {'prompt': 0}, ['生成一段10秒的追逐场景'])
        shared = build_job('故事', 2, {'prompt': 0}, ['生成一段10秒的追逐场景'],
                           '主角阿宁穿红色外套。参考设定不指定视频时长。')
        self.assertEqual(shared['payload']['seconds'], '10')
        self.assertIn('主角阿宁穿红色外套', shared['payload']['prompt'])
        self.assertIn('生成一段10秒的追逐场景', shared['prompt'])
        self.assertNotEqual(batch_identity([plain]), batch_identity([shared]))

    def test_price_uses_current_pt_published_rate(self):
        import datetime
        import wan_core
        for day, cost in (((2026, 10, 7), 1.6), ((2026, 10, 8), 4.0)):  # $0.08/s discount, then $0.20/s
            with patch.object(wan_core, 'local_today', return_value=datetime.date(*day)):
                job = build_job('故事', 2, {'duration': 0, 'resolution': 1, 'prompt': 2},
                                ['20', '1080P', '雨中街道'])
            self.assertEqual(job['cost'], cost, day)

    def test_timeline_duration_is_noted(self):
        job = build_job('故事', 2, {'prompt': 0}, ['0-5秒：走路；5-10秒：停下；10-16秒：回头'])
        self.assertEqual(job['payload']['seconds'], '16')
        self.assertIn('分镜时间轴', job['note'])

    def test_every_state_has_a_row_color(self):
        from batch_engine import LABELS
        from batch_ui import STATE_COLORS
        self.assertEqual(set(STATE_COLORS), set(LABELS))

    def test_character_setting_is_restored_per_source_and_can_be_changed(self):
        saved = {'story-a.xlsx': '阿宁穿红色外套', 'story-b.xlsx': '阿林穿蓝色外套'}
        self.assertEqual(select_character_setting('', '', saved, 'story-a.xlsx'), saved['story-a.xlsx'])
        self.assertEqual(select_character_setting(saved['story-a.xlsx'], saved['story-a.xlsx'],
                                                  saved, 'story-b.xlsx'), saved['story-b.xlsx'])
        self.assertEqual(select_character_setting('新的设定', saved['story-a.xlsx'],
                                                  saved, 'story-a.xlsx'), '新的设定')

    def test_explicit_ratio_overrides_prompt_conflict(self):
        job = build_job('Sheet', 2, {'prompt': 0, 'ratio': 1, 'duration': 2}, ['5秒或10秒，16:9或9:16', '1:1', '20'])
        self.assertEqual(job['state'], 'queued')
        self.assertEqual(job['payload']['ratio'], '1:1')
        self.assertEqual(job['payload']['seconds'], '20')

    def test_shared_strings_inline_strings_and_sheet_filtering(self):
        path = self.dir / 'small.xlsx'
        # Minimal fixture exercises ZIP/XML reading without a spreadsheet dependency.
        ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('xl/workbook.xml', '<workbook xmlns="%s" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Data" sheetId="1" r:id="r1"/><sheet name="Notes" sheetId="2" r:id="r2"/></sheets></workbook>' % ns)
            z.writestr('xl/_rels/workbook.xml.rels', '<Relationships><Relationship Id="r1" Target="worksheets/sheet1.xml"/><Relationship Id="r2" Target="/xl/worksheets/sheet2.xml"/></Relationships>')
            z.writestr('xl/sharedStrings.xml', '<sst xmlns="%s"><si><t>Prompt</t></si><si><r><t>hello </t></r><r><t>world</t></r></si></sst>' % ns)
            z.writestr('xl/worksheets/sheet1.xml', '<worksheet xmlns="%s"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c></row><row r="2"><c r="A2" t="s"><v>1</v></c></row><row r="3"><c r="A3" t="inlineStr"><is><t>hello again</t></is></c></row></sheetData></worksheet>' % ns)
            z.writestr('xl/worksheets/sheet2.xml', '<worksheet xmlns="%s"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>说明</t></is></c></row></sheetData></worksheet>' % ns)
        rows, skipped = import_jobs(path)
        self.assertEqual([r['prompt'] for r in rows], ['hello world', 'hello again'])
        self.assertEqual(skipped, ['Notes'])


class BatchTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        self.rows = jobs(8)
        self.store = BatchStore(self.rows, self.dir / 'videos', data_dir=self.dir)
        self.keys = ['key-one', 'key-two']
        self.calls = []
        self.lock = threading.Lock()
        self.active, self.peak = 0, 0

    def factory(self, **kwargs):
        outer = self
        class Fake:
            record = None
            def submit(self, keys, request, output):
                with outer.lock:
                    outer.calls.append(('POST', keys[0], request['prompt']))
                    outer.active += 1
                    outer.peak = max(outer.peak, outer.active)
                self.record = dict(local_id='local_' + str(len(outer.calls)), task_id='task_' + kwargs['context']['batch_job_id'], key_hash=fingerprint(keys[0]), key_hint='****' + keys[0][-4:], **kwargs['context'])
                kwargs['on_record'](self.record)
                time.sleep(.025)
                with outer.lock:
                    outer.active -= 1
                return self.record
            def resume(self, key, task, output, record):
                outer.calls.append(('GET', key, task))
                self.record = record
                return record
        return Fake()

    def test_bounded_parallelism_and_key_rotation(self):
        BatchRunner(self.store, self.keys, 3, client_factory=self.factory).run()
        self.assertEqual(len(self.calls), 8)
        self.assertGreater(self.peak, 1)
        self.assertLessEqual(self.peak, 3)
        for method, key, prompt in self.calls:
            index = next(i for i, job in enumerate(self.rows) if job['prompt'] == prompt)
            self.assertEqual(key, self.keys[index % 2])
        self.assertTrue(all(j['state'] == 'completed' for j in self.store.snapshot()))

    def test_reimport_and_second_run_skip_completed(self):
        BatchRunner(self.store, self.keys, 3, client_factory=self.factory).run()
        imported = BatchStore(self.rows, self.dir / 'different-folder', data_dir=self.dir)
        self.assertTrue(imported.reused)
        BatchRunner(imported, self.keys, 3, client_factory=self.factory).run()
        self.assertEqual(len(self.calls), 8)
        self.assertEqual(imported.snapshot()[0]['output'], self.store.snapshot()[0]['output'])

    def test_selected_only(self):
        BatchRunner(self.store, self.keys, 2, client_factory=self.factory).run([self.rows[2]['id']])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(sum(j['state'] == 'queued' for j in self.store.snapshot()), 7)

    def test_uncertain_never_resubmitted(self):
        outer = self
        def factory(**kwargs):
            class Fake:
                record = None
                def submit(self, *args):
                    outer.calls.append('POST')
                    raise TaskError('unknown', 'UNCERTAIN')
            return Fake()
        runner = BatchRunner(self.store, self.keys, 1, client_factory=factory)
        runner.run([self.rows[0]['id']])
        runner.run([self.rows[0]['id']])
        self.assertEqual(self.calls, ['POST'])
        self.assertEqual(self.store.snapshot()[0]['state'], 'uncertain')

    def test_pause_leaves_unstarted_queued_and_resume_only_gets(self):
        stop = threading.Event()
        outer = self
        def factory(**kwargs):
            class Fake:
                record = None
                def submit(self, keys, request, output):
                    outer.calls.append('POST')
                    self.record = dict(task_id='task_old', key_hash=fingerprint(keys[0]), key_hint='****one')
                    kwargs['on_record'](self.record)
                    stop.set()
                    raise TaskError('pause', 'PAUSED')
            return Fake()
        BatchRunner(self.store, self.keys, 1, stop=stop, client_factory=factory).run()
        rows = self.store.snapshot()
        self.assertEqual(rows[0]['state'], 'paused')
        self.assertTrue(all(j['state'] == 'queued' for j in rows[1:]))
        stop.clear()
        BatchRunner(self.store, self.keys, 1, stop=stop, client_factory=self.factory).run([rows[0]['id']])
        self.assertEqual(self.calls[-1][0], 'GET')
        self.assertEqual(self.calls.count('POST'), 1)

    def test_crash_without_id_is_not_requeued(self):
        self.store.update(self.rows[0]['id'], state='running')
        restored = BatchStore(self.rows, self.dir, data_dir=self.dir)
        self.assertEqual(restored.snapshot()[0]['state'], 'uncertain')

    def test_recovers_orphan_core_record_after_crash(self):
        job = self.rows[0]
        self.store.update(job['id'], state='running')
        record = dict(local_id='orphan', task_id='task_old', batch_id=self.store.id, batch_job_id=job['id'], key_hash=fingerprint(self.keys[0]))
        atomic_json(self.dir / 'tasks/orphan.json', record)
        restored = BatchStore(self.rows, self.dir, data_dir=self.dir)
        self.assertEqual(restored.snapshot()[0]['state'], 'paused')
        self.assertEqual(restored.snapshot()[0]['record']['task_id'], 'task_old')

    def test_missing_original_key_does_not_submit(self):
        self.store.update(self.rows[0]['id'], state='paused', record={'task_id': 'task_old', 'key_hash': fingerprint('missing')})
        BatchRunner(self.store, self.keys, 1, client_factory=self.factory).run([self.rows[0]['id']])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.store.snapshot()[0]['state'], 'no_key')

    def test_active_batch_lock_blocks_other_instance(self):
        with BatchLease(self.store.path.with_suffix('.lock')):
            with self.assertRaises(ValueError):
                BatchStore(self.rows, self.dir, data_dir=self.dir)

    def test_manifest_does_not_store_keys(self):
        BatchRunner(self.store, self.keys, 2, client_factory=self.factory).run([self.rows[0]['id']])
        text = self.store.path.read_text()
        for key in self.keys:
            self.assertNotIn(key, text)

    def test_real_client_checkpoints_and_second_run_does_not_post(self):
        calls = []
        lock = threading.Lock()
        def api(method, url, key, request=None):
            with lock:
                calls.append(method)
                if method == 'POST':
                    return 201, {'id': 'task_' + str(len(calls))}
            return 200, {'status': 'completed'}
        with patch('wan_core.api', side_effect=api), patch('wan_core.download', return_value=128):
            runner = BatchRunner(self.store, self.keys, 3)
            runner.run()
            runner.run()
        self.assertEqual(calls.count('POST'), 8)
        records = list((self.dir / 'tasks').glob('*.json'))
        self.assertEqual(len(records), 8)
        for path in records:
            row = json.loads(path.read_text())
            self.assertEqual(row['batch_id'], self.store.id)
            self.assertEqual(row['state'], '已完成')
        self.assertTrue(all(j['record']['task_id'] for j in self.store.snapshot()))

    def test_explicit_rejection_is_recorded_for_manual_recovery(self):
        with patch('wan_core.api', return_value=(401, {})) as api:
            runner = BatchRunner(self.store, self.keys, 1)
            runner.run([self.rows[0]['id']])
            runner.run([self.rows[0]['id']])
        self.assertEqual(api.call_count, 2)  # Two keys tried once, not two generations.
        first = self.store.snapshot()[0]
        self.assertEqual(first['state'], 'failed')
        self.assertEqual(first['error_kind'], 'REJECTED')
        self.assertFalse(first['record'].get('task_id'))

    def test_403_is_retried_with_original_key_after_other_rows(self):
        calls = []
        first_queries = 0
        def api(method, url, key, request=None):
            nonlocal first_queries
            if method == 'POST':
                task_id = 'task_' + str(1 + sum(item[0] == 'POST' for item in calls))
                calls.append(('POST', key, task_id))
                return 201, {'id': task_id}
            task_id = url.rsplit('/', 1)[-1]
            calls.append(('GET', key, task_id))
            if task_id == 'task_1':
                first_queries += 1
                if first_queries == 1:
                    return 403, {'message': 'forbidden'}
            return 200, {'status': 'completed'}
        selected = [row['id'] for row in self.rows[:3]]
        with patch('wan_core.api', side_effect=api), patch('wan_core.download', return_value=128):
            BatchRunner(self.store, self.keys, 1).run(selected)
        self.assertEqual(calls, [
            ('POST', 'key-one', 'task_1'), ('GET', 'key-one', 'task_1'),
            ('POST', 'key-two', 'task_2'), ('GET', 'key-two', 'task_2'),
            ('POST', 'key-one', 'task_3'), ('GET', 'key-one', 'task_3'),
            ('GET', 'key-one', 'task_1')])
        self.assertTrue(all(row['state'] == 'completed' for row in self.store.snapshot()[:3]))

    def test_second_403_stops_without_trying_another_key(self):
        self.store.update(self.rows[0]['id'], state='paused', record={
            'local_id': 'saved', 'task_id': 'task_1', 'key_hash': fingerprint('key-two'),
            'key_hint': '****-two'})
        calls = []
        def api(method, url, key, request=None):
            calls.append((method, key))
            return 403, {'message': 'forbidden'}
        with patch('wan_core.api', side_effect=api), patch('wan_core.download') as download:
            BatchRunner(self.store, self.keys, 1).run([self.rows[0]['id']])
        self.assertEqual(calls, [('GET', 'key-two'), ('GET', 'key-two')])
        download.assert_not_called()
        row = self.store.snapshot()[0]
        self.assertEqual(row['state'], 'paused')
        self.assertEqual(row['error_kind'], 'QUERY_FORBIDDEN')
        self.assertEqual(row['record']['task_id'], 'task_1')

if __name__ == '__main__':
    unittest.main()

"""Batch import tab, using only the Tk main thread for widget updates."""
import csv
import json
from pathlib import Path
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
import uuid
from studio_ui import heading, card, label, Columns, Flow, text_area
from batch_import import import_jobs
from batch_engine import BatchStore, BatchRunner, BatchLease, LABELS
from wan_core import DATA_DIR, PRICE_CHECKED_DATE, atomic_json, fingerprint, task_url

CHARACTER_SETTING_PATH = DATA_DIR / 'batch-character-setting.json'
# Row text colors by job state: completed green, running blue, failed red, everything pending gray.
STATE_COLORS = {'completed': '#1e8a4c', 'running': '#2563eb', 'failed': '#c62828', 'invalid': '#c62828',
                'uncertain': '#c62828', 'queued': '#6b7785', 'paused': '#6b7785', 'deferred_403': '#6b7785',
                'no_key': '#6b7785'}


def select_character_setting(entered, last_applied, saved_by_source, source_key):
    entered = entered.strip()
    return saved_by_source.get(source_key, '') if entered == last_applied else entered


class BatchTab:
    def __init__(self, app, frame):
        self.app, self.frame, self.store = app, frame, None
        self.jobs = {}
        self.output = tk.StringVar(value=app.output.get())
        self.concurrency = tk.StringVar(value='3')
        self.download_concurrency = tk.StringVar(value='2')
        self.imported_character_setting = ''
        self.last_applied_character_setting = ''
        self.character_presets = {}
        self.summary = tk.StringVar(value='导入 Excel 后先预览，点击开始才会提交付费生成任务。')
        self.source = tk.StringVar(value='支持的表头：时长(秒)、分辨率、完整 Wan 3.0 Prompt；可选名称、比例。')
        heading(frame, '批量导入视频任务', '用 Excel / CSV 一次整理分集剧情、人物设定和生成参数。')
        columns = Columns(frame, app.scale, weights=(1, 2))
        columns.pack(fill='x')
        setting_box = card(columns.left, '全剧人物设定', '选填，应用到每条导入的 Prompt。')
        self.character_setting = text_area(setting_box, height=5, undo=True)
        self.character_setting.pack(fill='x')
        try:
            saved = json.loads(CHARACTER_SETTING_PATH.read_text(encoding='utf-8'))
            if isinstance(saved, dict):
                presets = saved.get('by_source', {})
                if isinstance(presets, dict):
                    self.character_presets = {k: v for k, v in presets.items()
                                              if isinstance(k, str) and isinstance(v, str)}
        except (OSError, ValueError):
            pass
        label(setting_box, '设定按表格文件记住。导入后可核对最终 Prompt；改设定需重新导入，已提交任务不会自动复用。').pack(fill='x', pady=(10, 0))
        import_box = card(columns.left, '导入文件')
        self.import_btn = ttk.Button(import_box, text='导入 Excel / CSV', style='Accent.TButton', command=self.import_file)
        self.import_btn.pack(fill='x')
        label(import_box, variable=self.source).pack(fill='x', pady=(12, 0))
        output_box = card(columns.left, '保存文件夹')
        ttk.Entry(output_box, textvariable=self.output, width=12).pack(fill='x')
        ttk.Button(output_box, text='选择…', command=self.choose_folder).pack(anchor='w', pady=(10, 0))
        task_box = card(columns.right, '任务预览', '表格列优先；比例从 prompt 识别。每条任务默认开启原生音频。')
        row = Flow(task_box)
        row.pack(fill='x', pady=(0, 10))
        for title, variable, limit in [('同时生成', self.concurrency, 8), ('同时下载', self.download_concurrency, 4)]:
            group = ttk.Frame(row)
            ttk.Label(group, text=title, style='Muted.TLabel').pack(side='left', padx=(0, 6))
            ttk.Spinbox(group, from_=1, to=limit, width=3, textvariable=variable).pack(side='left')
        row.schedule()
        label(task_box, 'Key 轮流分配 · 生成与下载并发独立控制').pack(fill='x', pady=(0, 12))
        table = ttk.Frame(task_box)
        table.pack(fill='both', expand=True)
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=('row', 'duration', 'resolution', 'ratio', 'state', 'time', 'task'), show='headings', selectmode='extended', height=8)
        for key, title, width in [('row', 'Excel 行', 70), ('duration', '秒', 50), ('resolution', '分辨率', 80), ('ratio', '比例', 65), ('state', '状态', 145), ('time', '本轮耗时', 90), ('task', '任务 ID', 300)]:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=45, stretch=key in ('state', 'task'))
        self.tree.grid(row=0, column=0, sticky='nsew')
        sy = ttk.Scrollbar(table, command=self.tree.yview)
        sy.grid(row=0, column=1, sticky='ns')
        sx = ttk.Scrollbar(table, orient='horizontal', command=self.tree.xview)
        sx.grid(row=1, column=0, sticky='ew')
        self.tree.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        for state, color in STATE_COLORS.items():
            self.tree.tag_configure(state, foreground=color)
        self.tree.bind('<<TreeviewSelect>>', lambda event: self.show_detail())
        self.tree.bind('<Control-c>', self.copy_selected_task_ids)
        self.tree.bind('<Control-a>', self.select_all_task_rows)
        actions = Flow(task_box)
        actions.pack(fill='x', pady=(10, 0))
        self.start_btn = ttk.Button(actions, text='开始 / 继续全部（付费生成）', style='Accent.TButton', command=self.start_all)
        self.selected_btn = ttk.Button(actions, text='仅开始 / 继续选中行', command=self.start_selected)
        self.stop_btn = ttk.Button(actions, text='暂停批次', command=app.stop_wait, state='disabled')
        actions.schedule()
        more = Flow(task_box)
        more.pack(fill='x', pady=(4, 10))
        self.attach_btn = ttk.Button(more, text='关联已有任务 ID', command=self.attach_task)
        self.retry_btn = ttk.Button(more, text='恢复选中失败行', command=self.recover_selected)
        ttk.Button(more, text='复制选中任务 ID', command=self.copy_selected_task_ids)
        ttk.Button(more, text='复制全部任务 ID', command=self.copy_all_task_ids)
        ttk.Button(more, text='导出任务结果 CSV', command=self.export_csv)
        more.schedule()
        self.progress = ttk.Progressbar(task_box, maximum=1)
        self.progress.pack(fill='x', pady=8)
        label(task_box, variable=self.summary).pack(fill='x')
        label(task_box, '暂停只停止等待，任务仍在云端继续并可能计费。并发不保证单条生成更快。').pack(fill='x', pady=(8, 0))
        details = card(columns.right, '最终 Prompt 与任务详情')
        self.detail = text_area(details, height=6, state='disabled')
        self.detail.pack(fill='x')

    def choose_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.output.set(folder)

    def set_busy(self, busy):
        for button in (self.import_btn, self.start_btn, self.selected_btn, self.attach_btn, self.retry_btn):
            button.configure(state='disabled' if busy else 'normal')
        self.stop_btn.configure(state='normal' if busy else 'disabled')

    def import_file(self):
        if self.app.busy:
            return
        path = filedialog.askopenfilename(filetypes=[('Excel / CSV', '*.xlsx *.csv'), ('Excel', '*.xlsx'), ('CSV', '*.csv')])
        if not path:
            return
        try:
            source_key = str(Path(path).resolve())
            entered = self.character_setting.get('1.0', 'end-1c').strip()
            setting = select_character_setting(entered, self.last_applied_character_setting,
                                               self.character_presets, source_key)
            jobs, skipped = import_jobs(path, setting)
            store = BatchStore(jobs, self.output.get(), source=path)
        except Exception as exc:
            messagebox.showerror('导入失败', str(exc))
            return
        self.store = store
        self.imported_character_setting = setting
        self.last_applied_character_setting = setting
        self.character_setting.delete('1.0', 'end')
        self.character_setting.insert('1.0', setting)
        self.character_presets[source_key] = setting
        try:
            atomic_json(CHARACTER_SETTING_PATH, {'by_source': self.character_presets})
        except OSError:
            pass
        self.jobs = {j['id']: j for j in store.snapshot()}
        if store.reused and self.jobs:
            self.output.set(str(Path(next(iter(self.jobs.values()))['output']).parent.parent))
        self.tree.delete(*self.tree.get_children())
        for job in self.jobs.values():
            self.update_job(job, summarize=False)
        suffix = ' · 已恢复本地记录，不会重提已完成任务' if store.reused else ''
        self.source.set('%s · %d 行%s%s%s' % (Path(path).name, len(jobs),
                        (' · 人物设定已加入每条 Prompt' if setting else ''), suffix,
                        (' · 已跳过：' + '、'.join(skipped)) if skipped else ''))
        self.summarize()

    def update_job(self, job, summarize=True):
        self.jobs[job['id']] = job
        request = job.get('payload') or {}
        values = (job['row'], request.get('seconds', '—'), request.get('size', '—'), request.get('ratio', '—'),
                  LABELS.get(job['state'], job['state']), '%ss' % job.get('elapsed', 0), (job.get('record') or {}).get('task_id', ''))
        tags = (job['state'],) if job['state'] in STATE_COLORS else ()
        if self.tree.exists(job['id']):
            self.tree.item(job['id'], values=values, tags=tags)
        else:
            self.tree.insert('', 'end', iid=job['id'], values=values, tags=tags)
        if summarize:
            self.summarize()
            if job['id'] in self.tree.selection():
                self.show_detail()

    def summarize(self):
        jobs = list(self.jobs.values())
        counts = {state: sum(j['state'] == state for j in jobs) for state in LABELS}
        amount = sum(j.get('cost', 0) for j in jobs if j['state'] == 'queued')
        self.progress.configure(maximum=max(1, len(jobs)), value=counts['completed'])
        self.summary.set(' / '.join('%s %d' % (LABELS[s], n) for s, n in counts.items() if n) +
                         ' · 待提交任务估算 $%.2f（PT %s 公示价；实际以账单为准）' %
                         (amount, PRICE_CHECKED_DATE))

    def show_detail(self):
        selection = self.tree.selection()
        if not selection:
            return
        job = self.jobs[selection[0]]
        text = '%s · 行 %s\n%s\n输出：%s\n\n%s' % (job['sheet'], job['row'], job['note'], job['output'], job['prompt'])
        self.detail.configure(state='normal')
        self.detail.delete('1.0', 'end')
        self.detail.insert('end', text)
        self.detail.configure(state='disabled')

    def select_all_task_rows(self, event=None):
        rows = self.tree.get_children()
        if rows:
            self.tree.selection_set(*rows)
        return 'break'

    def copy_selected_task_ids(self, event=None):
        selected = set(self.tree.selection())
        task_ids = [(self.jobs[item].get('record') or {}).get('task_id')
                    for item in self.tree.get_children() if item in selected]
        self.app.copy_task_ids_to_clipboard([task for task in task_ids if task])
        return 'break'

    def copy_all_task_ids(self):
        task_ids = [(self.jobs[item].get('record') or {}).get('task_id')
                    for item in self.tree.get_children()]
        self.app.copy_task_ids_to_clipboard([task for task in task_ids if task])

    def start_all(self):
        self.start(None)

    def start_selected(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo('选择任务', '请在表格中选中需要处理的行；可按 Ctrl / Shift 多选。')
            return
        self.start(selected)

    def start(self, selected):
        if self.app.busy or self.store is None:
            return
        if self.character_setting.get('1.0', 'end-1c').strip() != self.imported_character_setting:
            messagebox.showerror('人物设定已更改', '请重新导入表格，核对每行最终 Prompt 后再开始。')
            return
        try:
            runner = BatchRunner(self.store, list(self.app.keys), int(self.concurrency.get()), stop=self.app.stop,
                         notify=lambda job: self.app.events.put(('batch_update', job)),
                         download_limit=int(self.download_concurrency.get()))
            with BatchLease(self.store.path.with_suffix('.lock')):
                self.store.data = json.loads(self.store.path.read_text(encoding='utf-8'))
                folder = Path(self.output.get()).absolute() / ('batch_' + self.store.id[:8])
                for job in self.store.snapshot():
                    if job['state'] == 'queued' and (selected is None or job['id'] in selected):
                        changed = self.store.update(job['id'], output=str(folder / Path(job['output']).name))
                        self.update_job(changed)
            eligible = [j for j in self.store.snapshot() if runner.eligible(j) and (selected is None or j['id'] in selected)]
            if not eligible:
                raise ValueError('没有可处理的任务。参数错误请修改源表；提交结果未知的任务请先查仪表盘并关联原任务 ID。')
            # Preflight the actual saved output directories, including restored batches.
            for folder in {str(Path(j['output']).parent) for j in eligible}:
                path = Path(folder)
                path.mkdir(parents=True, exist_ok=True)
                probe = path / ('.write-test-' + uuid.uuid4().hex)
                probe.touch()
                probe.unlink()
        except Exception as exc:
            messagebox.showerror('无法开始', str(exc))
            return
        self.app.stop.clear()
        self.app.busy = True
        for button in (self.app.generate_btn, self.app.resume_btn, self.app.manual_btn):
            button.configure(state='disabled')
        self.app.stop_btn.configure(state='normal')
        self.set_busy(True)
        self.app.status.set('批量任务处理中；可在当前页查看每行状态。')
        def worker():
            try:
                runner.run(selected)
                text = '批次已暂停，可继续原任务。' if self.app.stop.is_set() else '本轮批量处理结束，请查看各行状态。'
            except Exception as exc:
                text = '批次停止：%s。请保留任务记录并检查存储权限。' % type(exc).__name__
            self.app.events.put(('done', text))
        threading.Thread(target=worker, daemon=True).start()

    def recover_selected(self):
        if self.app.busy or self.store is None:
            return
        selected = self.tree.selection()
        if not selected:
            return
        count = 0
        try:
            with BatchLease(self.store.path.with_suffix('.lock')):
                self.store.data = json.loads(self.store.path.read_text(encoding='utf-8'))
                for job in self.store.snapshot():
                    if job['id'] not in selected or job['state'] not in ('failed', 'no_key', 'paused'):
                        continue
                    if (job.get('record') or {}).get('task_id'):
                        updated = self.store.update(job['id'], state='paused', note='已准备继续查询原任务，不重新提交。')
                    elif job.get('error_kind') in ('REJECTED', 'PARAM', 'AUTH'):
                        updated = self.store.update(job['id'], state='queued', note='之前提交被明确拒绝，已重新排队；点击开始后提交新请求。')
                    else:
                        continue
                    self.update_job(updated)
                    count += 1
            messagebox.showinfo('恢复结果', '已准备 %d 行。未知提交结果不会自动重排，请先查仪表盘并关联任务 ID。' % count)
        except Exception as exc:
            messagebox.showerror('无法恢复', str(exc))

    def attach_task(self):
        if self.app.busy or self.store is None:
            return
        selected = self.tree.selection()
        if len(selected) != 1:
            messagebox.showinfo('选择任务', '请选中一行，再关联它在平台上的任务 ID。')
            return
        job = self.jobs[selected[0]]
        if job['state'] == 'completed':
            messagebox.showinfo('任务已完成', '该行已完成。')
            return
        try:
            key = self.app.selected_key()
            task = simpledialog.askstring('关联原任务', '填写此行已有的任务 ID。将使用 API Key 页选中的原 Key，仅查询，不重新生成。')
            if not task:
                return
            task = task.strip()
            task_url(task)
            record = dict(local_id=uuid.uuid4().hex, task_id=task, created=time.strftime('%Y-%m-%d %H:%M:%S'),
                          output=job['output'], state='继续查询', key_hash=fingerprint(key), key_hint='****' + key[-4:],
                          batch_id=self.store.id, batch_job_id=job['id'])
            with BatchLease(self.store.path.with_suffix('.lock')):
                self.store.data = json.loads(self.store.path.read_text(encoding='utf-8'))
                current = next(j for j in self.store.snapshot() if j['id'] == job['id'])
                if current['state'] == 'completed':
                    raise ValueError('这个任务已经完成，请重新导入表格以刷新状态。')
                updated = self.store.update(job['id'], record=record, state='paused', note='已关联原任务，可继续查询。')
            self.update_job(updated)
        except Exception as exc:
            messagebox.showerror('无法关联', str(exc))

    def export_csv(self):
        if not self.store:
            return
        path = filedialog.asksaveasfilename(defaultextension='.csv', initialfile='批量视频结果.csv', filetypes=[('CSV', '*.csv')])
        if not path:
            return
        def safe(value):
            text = str(value)
            return "'" + text if text.startswith(('=', '+', '-', '@', '\t', '\r')) else text
        try:
            with open(path, 'w', encoding='utf-8-sig', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['工作表', 'Excel行', '状态', '任务ID', '文件位置', '说明'])
                for j in self.store.snapshot():
                    writer.writerow([safe(v) for v in [j['sheet'], j['row'], LABELS[j['state']],
                        (j.get('record') or {}).get('task_id', ''), j['output'], j['note']]])
        except OSError as exc:
            messagebox.showerror('导出失败', str(exc))

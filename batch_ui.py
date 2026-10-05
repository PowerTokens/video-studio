"""Batch import tab, using only the Tk main thread for widget updates."""
import csv
import json
from pathlib import Path
import shutil
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
import tkinter.font as tkfont
import uuid
from i18n import is_zh, t
from studio_ui import heading, card, label, Columns, Flow, text_area, resource_path
from batch_import import import_jobs
from batch_engine import BatchStore, BatchRunner, BatchLease, LABELS
from wan_core import DATA_DIR, PRICE_CHECKED_DATE, atomic_json, fingerprint, task_url

CHARACTER_SETTING_PATH = DATA_DIR / 'batch-character-setting.json'
# Sample spreadsheets shipped next to app.py (and bundled into the EXE).
TEMPLATE_FILES = {
    'drama': {'zh': '短剧批量示例模板.xlsx', 'en': 'short-drama-batch-template.xlsx'},
    'product': {'zh': '产品批量示例模板.xlsx', 'en': 'product-batch-template.xlsx'},
}


def template_file(kind='drama'):
    files = TEMPLATE_FILES.get(kind) or TEMPLATE_FILES['drama']
    return files['zh' if is_zh() else 'en']
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
        self.chain_clips = tk.BooleanVar(value=False)
        self.download_concurrency = tk.StringVar(value='2')
        self.imported_character_setting = ''
        self.last_applied_character_setting = ''
        self.character_presets = {}
        self.import_info = None  # (file name, rows, character setting, reused, skipped) of the last import.
        self.summary = tk.StringVar(value=t('batch_summary_default'))
        self.source = tk.StringVar(value=t('batch_source_default'))
        heading(frame, t('batch_title'), t('batch_desc'))
        columns = Columns(frame, app.scale, weights=(1, 2))
        columns.pack(fill='x')
        setting_box = card(columns.left, t('character_card'), t('character_card_desc'))
        self.character_setting = text_area(setting_box, height=3, undo=True)
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
        label(setting_box, t('character_note')).pack(fill='x', pady=(10, 0))
        import_box = card(columns.left, t('import_card'))
        self.import_btn = ttk.Button(import_box, text=t('import_btn'), style='Accent.TButton', command=self.import_file)
        self.import_btn.pack(fill='x')
        label(import_box, variable=self.source).pack(fill='x', pady=(12, 0))
        self.template_btn = ttk.Button(import_box, text=t('template_btn_drama'),
                                       command=lambda: self.save_template('drama'))
        self.template_btn.pack(anchor='w', pady=(10, 0))
        self.product_template_btn = ttk.Button(import_box, text=t('template_btn_product'),
                                               command=lambda: self.save_template('product'))
        self.product_template_btn.pack(anchor='w', pady=(6, 0))
        self.storyboard_btn = ttk.Button(import_box, text=t('storyboard_btn'),
                                         command=self.open_storyboard)
        self.storyboard_btn.pack(anchor='w', pady=(10, 0))
        output_box = card(columns.left, t('batch_output_card'))
        ttk.Entry(output_box, textvariable=self.output, width=12).pack(fill='x')
        ttk.Button(output_box, text=t('browse'), command=self.choose_folder).pack(anchor='w', pady=(10, 0))
        task_box = card(columns.right, t('preview_card'), t('preview_card_desc'))
        row = Flow(task_box)
        row.pack(fill='x', pady=(0, 10))
        for title, variable, limit in [(t('concurrency'), self.concurrency, 8), (t('download_concurrency'), self.download_concurrency, 4)]:
            group = ttk.Frame(row)
            ttk.Label(group, text=title, style='Muted.TLabel').pack(side='left', padx=(0, 6))
            ttk.Spinbox(group, from_=1, to=limit, width=3, textvariable=variable).pack(side='left')
        row.schedule()
        label(task_box, t('rotation_note')).pack(fill='x', pady=(0, 8))
        self.chain_check = ttk.Checkbutton(task_box, text=t('chain_enable'), variable=self.chain_clips)
        self.chain_check.pack(anchor='w')
        label(task_box, t('chain_enable_desc')).pack(fill='x', pady=(2, 12))
        table = ttk.Frame(task_box)
        table.pack(fill='both', expand=True)
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=('row', 'duration', 'resolution', 'ratio', 'state', 'time', 'task'), show='headings', selectmode='extended', height=8)
        heading_font = tkfont.Font(font=ttk.Style(frame).lookup('Treeview.Heading', 'font') or 'TkDefaultFont')
        for key, width in [('row', 70), ('duration', 50), ('resolution', 80), ('ratio', 65), ('state', 145), ('time', 90), ('task', 300)]:
            title = t('bcol_' + key)
            self.tree.heading(key, text=title)
            # Never narrower than the heading text (English headings are longer).
            width = max(width, heading_font.measure(title) + 28)
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
        self.start_btn = ttk.Button(actions, text=t('start_all'), style='Accent.TButton', command=self.start_all)
        self.selected_btn = ttk.Button(actions, text=t('start_selected'), command=self.start_selected)
        self.stop_btn = ttk.Button(actions, text=t('pause_batch'), command=app.stop_wait, state='disabled')
        actions.schedule()
        more = Flow(task_box)
        more.pack(fill='x', pady=(4, 10))
        self.attach_btn = ttk.Button(more, text=t('attach_btn'), command=self.attach_task)
        self.retry_btn = ttk.Button(more, text=t('recover_btn'), command=self.recover_selected)
        ttk.Button(more, text=t('copy_selected_task_ids'), command=self.copy_selected_task_ids)
        ttk.Button(more, text=t('copy_all_task_ids'), command=self.copy_all_task_ids)
        ttk.Button(more, text=t('export_csv'), command=self.export_csv)
        more.schedule()
        self.progress = ttk.Progressbar(task_box, maximum=1)
        self.progress.pack(fill='x', pady=8)
        label(task_box, variable=self.summary).pack(fill='x')
        label(task_box, t('pause_note')).pack(fill='x', pady=(8, 0))
        details = card(columns.right, t('detail_card'))
        self.detail = text_area(details, height=6, state='disabled')
        self.detail.pack(fill='x')

    def export_state(self):
        """Everything the user entered or imported, so a language switch can rebuild the tab."""
        return dict(store=self.store, jobs=self.jobs, import_info=self.import_info,
                    imported=self.imported_character_setting, last_applied=self.last_applied_character_setting,
                    presets=self.character_presets, setting=self.character_setting.get('1.0', 'end-1c'),
                    output=self.output.get(), concurrency=self.concurrency.get(),
                    download_concurrency=self.download_concurrency.get(), selection=self.tree.selection())

    def restore_state(self, state):
        self.store, self.import_info = state['store'], state['import_info']
        self.imported_character_setting = state['imported']
        self.last_applied_character_setting = state['last_applied']
        self.character_presets = state['presets']
        self.character_setting.insert('1.0', state['setting'])
        for name in ('output', 'concurrency', 'download_concurrency'):
            getattr(self, name).set(state[name])
        self.jobs = {}
        for job in state['jobs'].values():
            self.update_job(job, summarize=False)
        if self.import_info:
            self.show_source()
        if self.jobs:
            self.summarize()
        selection = [item for item in state['selection'] if self.tree.exists(item)]
        if selection:
            self.tree.selection_set(*selection)

    def save_template(self, kind='drama'):
        name = template_file(kind)
        source = resource_path(name)
        if not Path(source).is_file():
            messagebox.showerror(t('import_failed'), name)
            return
        path = filedialog.asksaveasfilename(defaultextension='.xlsx', initialfile=name,
                                            filetypes=[('Excel', '*.xlsx')])
        if not path:
            return
        shutil.copyfile(source, path)
        self.app.status.set(t('template_saved', path))


    def choose_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.output.set(folder)

    def set_busy(self, busy):
        for button in (self.import_btn, self.start_btn, self.selected_btn, self.attach_btn, self.retry_btn,
                       getattr(self, 'storyboard_btn', None), getattr(self, 'product_template_btn', None),
                       getattr(self, 'template_btn', None)):
            if button is not None:
                button.configure(state='disabled' if busy else 'normal')
        self.stop_btn.configure(state='normal' if busy else 'disabled')


    def load_jobs(self, jobs, source='storyboard', character_setting=''):
        """Put prepared jobs into the preview table (shared by Excel import and From-script)."""
        store = BatchStore(jobs, self.output.get(), source=source)
        self.store = store
        self.imported_character_setting = character_setting
        self.last_applied_character_setting = character_setting
        self.jobs = {j['id']: j for j in store.snapshot()}
        self.tree.delete(*self.tree.get_children())
        for job in self.jobs.values():
            self.update_job(job, summarize=False)
        self.import_info = (Path(str(source)).name if source else 'storyboard', len(jobs),
                            bool(character_setting), store.reused, [])
        self.show_source()
        self.summarize()
        return store

    def open_storyboard(self):
        if self.app.busy:
            return
        StoryboardDialog(self)

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
            messagebox.showerror(t('import_failed'), str(exc))
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
        self.import_info = (Path(path).name, len(jobs), bool(setting), store.reused, list(skipped))
        self.show_source()
        self.summarize()

    def show_source(self):
        name, rows, setting, reused, skipped = self.import_info
        suffix = t('import_reused') if reused else ''
        self.source.set(t('import_summary', name, rows,
                          (t('import_character') if setting else ''), suffix,
                          (t('import_skipped') + t('list_sep').join(skipped)) if skipped else '', count=rows))

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
                         t('batch_estimate', amount, PRICE_CHECKED_DATE))

    def show_detail(self):
        selection = self.tree.selection()
        if not selection:
            return
        job = self.jobs[selection[0]]
        text = t('job_detail', job['sheet'], job['row'], job['note'], job['output'], job['prompt'])
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
            messagebox.showinfo(t('select_task_title'), t('select_rows_body'))
            return
        self.start(selected)

    def start(self, selected):
        if self.app.busy or self.store is None:
            return
        if self.character_setting.get('1.0', 'end-1c').strip() != self.imported_character_setting:
            messagebox.showerror(t('character_changed_title'), t('character_changed_body'))
            return
        try:
            runner = BatchRunner(self.store, list(self.app.keys), int(self.concurrency.get()), stop=self.app.stop,
                         notify=lambda job: self.app.events.put(('batch_update', job)),
                         download_limit=int(self.download_concurrency.get()),
                         chain=bool(self.chain_clips.get()))
            with BatchLease(self.store.path.with_suffix('.lock')):
                self.store.data = json.loads(self.store.path.read_text(encoding='utf-8'))
                folder = Path(self.output.get()).absolute() / ('batch_' + self.store.id[:8])
                for job in self.store.snapshot():
                    if job['state'] == 'queued' and (selected is None or job['id'] in selected):
                        changed = self.store.update(job['id'], output=str(folder / Path(job['output']).name))
                        self.update_job(changed)
            eligible = [j for j in self.store.snapshot() if runner.eligible(j) and (selected is None or j['id'] in selected)]
            if not eligible:
                raise ValueError(t('nothing_eligible'))
            # Preflight the actual saved output directories, including restored batches.
            for folder in {str(Path(j['output']).parent) for j in eligible}:
                path = Path(folder)
                path.mkdir(parents=True, exist_ok=True)
                probe = path / ('.write-test-' + uuid.uuid4().hex)
                probe.touch()
                probe.unlink()
        except Exception as exc:
            messagebox.showerror(t('cannot_start'), str(exc))
            return
        self.app.stop.clear()
        self.app.busy = True
        for button in (self.app.generate_btn, self.app.resume_btn, self.app.manual_btn):
            button.configure(state='disabled')
        self.app.stop_btn.configure(state='normal')
        self.set_busy(True)
        self.app.status.set(t('batch_running'))
        def worker():
            try:
                runner.run(selected)
                text = t('batch_paused') if self.app.stop.is_set() else t('batch_finished')
            except Exception as exc:
                text = t('batch_crashed', type(exc).__name__)
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
                        updated = self.store.update(job['id'], state='paused', note=t('note_resume_ready'))
                    elif job.get('error_kind') in ('REJECTED', 'PARAM', 'AUTH'):
                        updated = self.store.update(job['id'], state='queued', note=t('note_requeued'))
                    else:
                        continue
                    self.update_job(updated)
                    count += 1
            messagebox.showinfo(t('recover_title'), t('recover_body', count, count=count))
        except Exception as exc:
            messagebox.showerror(t('cannot_recover'), str(exc))

    def attach_task(self):
        if self.app.busy or self.store is None:
            return
        selected = self.tree.selection()
        if len(selected) != 1:
            messagebox.showinfo(t('select_task_title'), t('attach_select_body'))
            return
        job = self.jobs[selected[0]]
        if job['state'] == 'completed':
            messagebox.showinfo(t('already_done_title'), t('already_done_body'))
            return
        try:
            key = self.app.selected_key()
            task = simpledialog.askstring(t('attach_title'), t('attach_prompt'))
            if not task:
                return
            task = task.strip()
            task_url(task)
            record = dict(local_id=uuid.uuid4().hex, task_id=task, created=time.strftime('%Y-%m-%d %H:%M:%S'),
                          output=job['output'], state=t('state_resuming'), key_hash=fingerprint(key), key_hint='****' + key[-4:],
                          batch_id=self.store.id, batch_job_id=job['id'])
            with BatchLease(self.store.path.with_suffix('.lock')):
                self.store.data = json.loads(self.store.path.read_text(encoding='utf-8'))
                current = next(j for j in self.store.snapshot() if j['id'] == job['id'])
                if current['state'] == 'completed':
                    raise ValueError(t('attach_completed'))
                updated = self.store.update(job['id'], record=record, state='paused', note=t('note_attached'))
            self.update_job(updated)
        except Exception as exc:
            messagebox.showerror(t('cannot_attach'), str(exc))

    def export_csv(self):
        if not self.store:
            return
        path = filedialog.asksaveasfilename(defaultextension='.csv', initialfile=t('export_filename'), filetypes=[('CSV', '*.csv')])
        if not path:
            return
        def safe(value):
            text = str(value)
            return "'" + text if text.startswith(('=', '+', '-', '@', '\t', '\r')) else text
        try:
            with open(path, 'w', encoding='utf-8-sig', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(list(t('export_headers')))
                for j in self.store.snapshot():
                    writer.writerow([safe(v) for v in [j['sheet'], j['row'], LABELS[j['state']],
                        (j.get('record') or {}).get('task_id', ''), j['output'], j['note']]])
        except OSError as exc:
            messagebox.showerror(t('export_failed'), str(exc))


class StoryboardDialog(tk.Toplevel):
    """Paste a script → text model → rows in the batch preview. Batch tab only."""

    def __init__(self, batch_tab):
        super().__init__(batch_tab.frame.winfo_toplevel())
        self.batch = batch_tab
        self.app = batch_tab.app
        self.title(t('storyboard_title'))
        self.transient(self.app.root)
        self.grab_set()
        self.geometry('640x720')
        body = ttk.Frame(self, padding=16)
        body.pack(fill='both', expand=True)
        label(body, t('storyboard_desc')).pack(fill='x', pady=(0, 10))
        ttk.Label(body, text=t('storyboard_script'), style='Muted.TLabel').pack(anchor='w')
        self.script = text_area(body, height=6, undo=True)
        self.script.pack(fill='both', expand=True, pady=(4, 10))

        from models import DEFAULT_MODEL_ID, list_models
        from storyboard import DEFAULT_TEXT_MODEL, TEXT_MODEL_QWEN, text_model_label, is_free_text_model

        grid = ttk.Frame(body)
        grid.pack(fill='x', pady=(0, 8))
        self.clips = tk.StringVar(value='0')
        self.duration = tk.StringVar(value='8')
        self.ratio = tk.StringVar(value='9:16')
        self.resolution = tk.StringVar(value='720p')
        self.video_model = tk.StringVar(value=DEFAULT_MODEL_ID)
        self.text_model = tk.StringVar(value=DEFAULT_TEXT_MODEL)
        self._model_labels = {}
        labels = []
        for spec in list_models():
            lab = spec.label()
            self._model_labels[lab] = spec.id
            labels.append(lab)
        self.video_label = tk.StringVar(value=list_models()[0].label())
        self.text_choices = {
            text_model_label(DEFAULT_TEXT_MODEL): DEFAULT_TEXT_MODEL,
            text_model_label(TEXT_MODEL_QWEN): TEXT_MODEL_QWEN,
        }
        self.text_label = tk.StringVar(value=text_model_label(DEFAULT_TEXT_MODEL))

        fields = [
            (t('storyboard_clips'), self.clips, None),
            (t('storyboard_duration'), self.duration, None),
            (t('storyboard_resolution'), self.resolution, ('720p', '1080p', '480p')),
            (t('storyboard_ratio'), self.ratio, ('9:16', '16:9', '1:1')),
        ]
        for i, (title, var, values) in enumerate(fields):
            grid.columnconfigure(i % 2, weight=1)
            box = ttk.Frame(grid)
            box.grid(row=i // 2, column=i % 2, sticky='ew', padx=(0, 10), pady=4)
            ttk.Label(box, text=title, style='Muted.TLabel').pack(anchor='w')
            if values is None:
                ttk.Entry(box, textvariable=var, width=8).pack(fill='x')
            else:
                ttk.Combobox(box, textvariable=var, values=values, state='readonly', width=8).pack(fill='x')

        tm = ttk.Frame(body)
        tm.pack(fill='x', pady=4)
        ttk.Label(tm, text=t('storyboard_text_model'), style='Muted.TLabel').pack(anchor='w')
        self.text_combo = ttk.Combobox(tm, textvariable=self.text_label,
                                       values=list(self.text_choices.keys()), state='readonly')
        self.text_combo.pack(fill='x')
        self.text_combo.bind('<<ComboboxSelected>>', lambda *_: self._sync_credit_note())
        self.credit_note = tk.StringVar()
        self.credit_label = label(body, variable=self.credit_note)
        # packed only when non-free model selected
        self._sync_credit_note()
        vm = ttk.Frame(body)
        vm.pack(fill='x', pady=4)
        ttk.Label(vm, text=t('storyboard_model'), style='Muted.TLabel').pack(anchor='w')
        ttk.Combobox(vm, textvariable=self.video_label, values=labels, state='readonly').pack(fill='x')

        self.chain_var = tk.BooleanVar(value=bool(self.batch.chain_clips.get()))
        ttk.Checkbutton(body, text=t('chain_enable'), variable=self.chain_var).pack(anchor='w', pady=(8, 0))
        label(body, t('chain_enable_desc')).pack(fill='x', pady=(2, 0))
        actions = ttk.Frame(body)
        actions.pack(fill='x', pady=(12, 0))
        ttk.Button(actions, text=t('storyboard_run'), style='Accent.TButton',
                   command=self.run).pack(side='left')
        ttk.Button(actions, text=t('storyboard_cancel'), command=self.destroy).pack(side='right')

    def _sync_credit_note(self):
        from storyboard import is_free_text_model
        mid = self.text_choices.get(self.text_label.get(), self.text_model.get())
        self.text_model.set(mid)
        if is_free_text_model(mid):
            self.credit_note.set('')
            self.credit_label.pack_forget()
        else:
            self.credit_note.set(t('storyboard_credit_note'))
            if not self.credit_label.winfo_manager():
                self.credit_label.pack(fill='x', pady=(6, 0))

    def run(self):
        from storyboard import storyboard_from_script
        script = self.script.get('1.0', 'end').strip()
        if not script:
            messagebox.showerror(t('storyboard_title'), t('storyboard_need_script'), parent=self)
            return
        try:
            key = self.app.selected_key()
        except Exception:
            messagebox.showerror(t('storyboard_title'), t('storyboard_need_key'), parent=self)
            return
        try:
            clips = int(self.clips.get() or 0)
            duration = int(self.duration.get() or 8)
        except ValueError:
            messagebox.showerror(t('storyboard_title'), t('storyboard_need_script'), parent=self)
            return
        video_id = self._model_labels.get(self.video_label.get(), self.video_model.get())
        text_id = self.text_choices.get(self.text_label.get(), self.text_model.get())
        character = self.batch.character_setting.get('1.0', 'end-1c').strip()
        self.configure(cursor='watch')
        self.update()
        try:
            jobs, meta = storyboard_from_script(
                script, key, clip_count=clips, duration=duration, model_id=video_id,
                text_model=text_id, ratio=self.ratio.get(), resolution=self.resolution.get(),
                character_setting=character)
            self.batch.chain_clips.set(bool(self.chain_var.get()))
            self.batch.load_jobs(jobs, source='storyboard', character_setting=character)
            self.app.status.set(t('storyboard_ok', len(jobs)))
            messagebox.showinfo(t('storyboard_title'), t('storyboard_ok', len(jobs)), parent=self)
            self.destroy()
        except Exception as exc:
            messagebox.showerror(t('storyboard_title'), str(exc), parent=self)
        finally:
            try:
                self.configure(cursor='')
            except tk.TclError:
                pass

"""Desktop Compare tab: one prompt, 2–3 models, side-by-side results."""
import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from compare_core import (
    MAX_COMPARE_MODELS, MIN_COMPARE_MODELS, plan_compare, run_compare,
)
from i18n import t
from models import DEFAULT_MODEL_ID, list_models, snap_params, format_adjust_notice, get_model
from studio_ui import Columns, Flow, card, heading, label, text_area
from wan_core import Client, TaskError


class CompareTab:
    def __init__(self, app, frame):
        self.app = app
        self.frame = frame
        self.model_vars = {}
        self.result_rows = {}
        self.plan = None
        self._build()

    def _build(self):
        heading(self.frame, t('compare_title'), t('compare_desc'))
        columns = Columns(self.frame, self.app.scale)
        columns.pack(fill='x')

        prompt_box = card(columns.left, t('compare_prompt_card'), t('compare_prompt_desc'))
        self.prompt = text_area(prompt_box, height=5, undo=True)
        self.prompt.pack(fill='x', pady=(0, 8))

        params = card(columns.left, t('compare_params_card'))
        row = ttk.Frame(params)
        row.pack(fill='x')
        self.duration = tk.StringVar(value='5')
        self.resolution = tk.StringVar(value='720p')
        self.ratio = tk.StringVar(value='16:9')
        all_resolutions = sorted({r for m in list_models() for r in m.resolutions},
                                 key=lambda x: {'480p': 0, '720p': 1, '1080p': 2, '4k': 3}.get(x, 9))
        all_ratios = []
        for m in list_models():
            for r in m.ratios:
                if r not in all_ratios:
                    all_ratios.append(r)
        for index, (title, var, values) in enumerate([
                (t('param_duration'), self.duration, None),
                (t('param_resolution'), self.resolution, all_resolutions),
                (t('param_ratio'), self.ratio, all_ratios)]):
            row.columnconfigure(index, weight=1, uniform='cmp')
            field = ttk.Frame(row)
            field.grid(row=0, column=index, sticky='ew', padx=(0, 12 if index < 2 else 0))
            ttk.Label(field, text=title, style='Muted.TLabel').pack(anchor='w', pady=(0, 8))
            if values is None:
                ttk.Spinbox(field, from_=2, to=30, textvariable=var, width=6).pack(fill='x')
            else:
                ttk.Combobox(field, textvariable=var, values=values, state='readonly', width=8).pack(fill='x')
            var.trace_add('write', lambda *_: self.refresh_plan_preview())

        models_card = card(columns.right, t('compare_models_card'), t('compare_models_desc'))
        self.model_vars = {}
        defaults = {DEFAULT_MODEL_ID, 'dreamina-seedance-2-5-260628', 'kling-v3'}
        for spec in list_models():
            var = tk.BooleanVar(value=spec.id in defaults)
            cb = ttk.Checkbutton(models_card, text=spec.label(), variable=var,
                                 command=self._on_model_toggle)
            cb.pack(anchor='w', pady=2)
            self.model_vars[spec.id] = var
        self.notices = tk.StringVar()
        label(models_card, variable=self.notices).pack(fill='x', pady=(8, 0))
        self.cost = tk.StringVar()
        label(models_card, variable=self.cost, style='Badge.TLabel').pack(fill='x', pady=(8, 0))

        save = card(columns.left, t('save_card'))
        ttk.Label(save, text=t('output_folder'), style='Muted.TLabel').pack(anchor='w', pady=(0, 6))
        path_row = ttk.Frame(save)
        path_row.pack(fill='x')
        self.output = tk.StringVar(value=str(Path.home() / 'Videos' / 'PowerTokensVideoStudio'))
        ttk.Entry(path_row, textvariable=self.output, width=12).pack(side='left', fill='x', expand=True)
        ttk.Button(path_row, text=t('browse'), command=self._choose_folder).pack(side='left', padx=(10, 0))
        label(save, t('compare_output_note')).pack(fill='x', pady=(8, 0))
        actions = Flow(save)
        actions.pack(fill='x', pady=(12, 0))
        self.start_btn = ttk.Button(actions, text=t('compare_start'), style='Accent.TButton',
                                    command=self.start_compare)
        actions.schedule()

        results = card(self.frame, t('compare_results_card'), t('compare_results_desc'))
        self.results_frame = ttk.Frame(results)
        self.results_frame.pack(fill='x')
        self.refresh_plan_preview()

    def selected_models(self):
        return [mid for mid, var in self.model_vars.items() if var.get()]

    def _on_model_toggle(self):
        selected = self.selected_models()
        if len(selected) > MAX_COMPARE_MODELS:
            # Untick the last toggled by finding extras - keep first 3 in registry order
            keep = set(selected[:MAX_COMPARE_MODELS])
            for mid, var in self.model_vars.items():
                if var.get() and mid not in keep:
                    var.set(False)
            messagebox.showinfo(t('compare_title'), t('compare_max_models', MAX_COMPARE_MODELS))
        self.refresh_plan_preview()

    def refresh_plan_preview(self):
        selected = self.selected_models()
        notice_lines = []
        try:
            if len(selected) < MIN_COMPARE_MODELS:
                self.cost.set(t('compare_cost_invalid'))
                self.notices.set('')
                self._rebuild_result_grid([])
                return
            plan = plan_compare(
                self.prompt.get('1.0', 'end') or 'preview',
                self.duration.get(), self.resolution.get(), self.ratio.get(),
                selected, folder=self.output.get().strip() or '.')
            # Recompute notices without requiring non-empty prompt for display
            items = []
            total = 0.0
            for mid in selected:
                spec = get_model(mid)
                d, r, ratio, changes = snap_params(spec, self.duration.get(), self.resolution.get(), self.ratio.get())
                notice = format_adjust_notice(spec, changes)
                if notice:
                    notice_lines.append(notice)
                from models import estimate_cost
                cost = float(estimate_cost(d, r, model_id=mid))
                total += cost
                items.append({
                    'model_id': mid, 'label': spec.label(), 'duration': d, 'resolution': r,
                    'ratio': ratio, 'cost': cost, 'notice': notice, 'output': '',
                })
            self.cost.set(t('compare_cost', total, len(selected)))
            self.notices.set('\n'.join(notice_lines))
            self._rebuild_result_grid(items)
        except Exception:
            self.cost.set(t('compare_cost_invalid'))
            self.notices.set('')

    def _rebuild_result_grid(self, items):
        for child in self.results_frame.winfo_children():
            child.destroy()
        self.result_rows = {}
        header = ttk.Frame(self.results_frame)
        header.pack(fill='x', pady=(0, 6))
        for col, key, w in [
                (0, 'compare_col_model', 18), (1, 'compare_col_params', 16),
                (2, 'compare_col_cost', 10), (3, 'compare_col_status', 12),
                (4, 'compare_col_actions', 20)]:
            header.columnconfigure(col, weight=1 if col < 4 else 0)
            ttk.Label(header, text=t(key), style='Muted.TLabel').grid(row=0, column=col, sticky='w', padx=4)

        if not items:
            return
        for item in items:
            row = ttk.Frame(self.results_frame)
            row.pack(fill='x', pady=4)
            for c in range(5):
                row.columnconfigure(c, weight=1 if c < 4 else 0)
            ttk.Label(row, text=item['label']).grid(row=0, column=0, sticky='w', padx=4)
            params = '%ss · %s · %s' % (item['duration'], item['resolution'], item['ratio'])
            ttk.Label(row, text=params).grid(row=0, column=1, sticky='w', padx=4)
            ttk.Label(row, text='$%.2f' % item['cost']).grid(row=0, column=2, sticky='w', padx=4)
            status_var = tk.StringVar(value=t('compare_status_idle'))
            ttk.Label(row, textvariable=status_var).grid(row=0, column=3, sticky='w', padx=4)
            actions = ttk.Frame(row)
            actions.grid(row=0, column=4, sticky='e', padx=4)
            open_file = ttk.Button(actions, text=t('compare_open_file'), state='disabled',
                                   command=lambda mid=item['model_id']: self._open_output(mid, file=True))
            open_folder = ttk.Button(actions, text=t('compare_open_folder'), state='disabled',
                                     command=lambda mid=item['model_id']: self._open_output(mid, file=False))
            open_file.pack(side='left', padx=(0, 6))
            open_folder.pack(side='left')
            self.result_rows[item['model_id']] = {
                'status': status_var, 'open_file': open_file, 'open_folder': open_folder,
                'output': item.get('output') or '', 'row': row,
            }

    def _choose_folder(self):
        from tkinter import filedialog
        path = filedialog.askdirectory(initialdir=self.output.get())
        if path:
            self.output.set(path)
            self.refresh_plan_preview()

    def _open_path(self, path, file=False):
        path = Path(path)
        target = path if file else path.parent
        if not target.exists():
            messagebox.showinfo(t('compare_title'), str(target))
            return
        try:
            if sys.platform == 'win32':
                os.startfile(str(target))  # noqa: S606
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', str(target)])
            else:
                subprocess.Popen(['xdg-open', str(target)])
        except OSError as exc:
            messagebox.showerror(t('compare_title'), str(exc))

    def _open_output(self, model_id, file=True):
        info = self.result_rows.get(model_id) or {}
        output = info.get('output')
        if not output:
            return
        self._open_path(output, file=file)

    def set_busy(self, busy):
        state = 'disabled' if busy else 'normal'
        if hasattr(self, 'start_btn'):
            self.start_btn.configure(state=state)

    def export_state(self):
        return {
            'prompt': self.prompt.get('1.0', 'end-1c'),
            'duration': self.duration.get(),
            'resolution': self.resolution.get(),
            'ratio': self.ratio.get(),
            'output': self.output.get(),
            'models': self.selected_models(),
        }

    def import_state(self, state):
        if not state:
            return
        self.prompt.delete('1.0', 'end')
        self.prompt.insert('1.0', state.get('prompt', ''))
        for name in ('duration', 'resolution', 'ratio', 'output'):
            if name in state:
                getattr(self, name).set(state[name])
        selected = set(state.get('models') or [])
        for mid, var in self.model_vars.items():
            var.set(mid in selected)
        self.refresh_plan_preview()

    def start_compare(self):
        if self.app.busy:
            return
        try:
            if not self.app.keys:
                raise TaskError(t('need_key'))
            selected = self.selected_models()
            plan = plan_compare(
                self.prompt.get('1.0', 'end'),
                self.duration.get(), self.resolution.get(), self.ratio.get(),
                selected, folder=self.output.get().strip() or '.')
            self.plan = plan
            # Refresh grid with real outputs
            self._rebuild_result_grid(plan['items'])
            for item in plan['items']:
                if item['model_id'] in self.result_rows:
                    self.result_rows[item['model_id']]['output'] = item['output']
        except Exception as exc:
            messagebox.showerror(t('check_input'), str(exc))
            return

        keys = list(self.app.keys)
        plan = self.plan

        def operation(client):
            # Ignore the shared client; create one per model via factory for clean records.
            def factory():
                return Client(
                    report=lambda text: self.app.events.put(('log', text)),
                    stop=self.app.stop,
                    data_dir=None,
                )

            def report(text):
                self.app.events.put(('log', text))

            def on_status(model_id, status_key):
                self.app.events.put(('compare_status', (model_id, status_key)))

            for item in plan['items']:
                on_status(item['model_id'], 'compare_status_running')
            results = run_compare(factory, keys, plan, report=report, stop=self.app.stop)
            for result in results:
                mid = result['model_id']
                if result.get('ok'):
                    on_status(mid, 'compare_status_done')
                    self.app.events.put(('compare_output', (mid, result.get('output') or '')))
                else:
                    on_status(mid, 'compare_status_failed')
            # Ensure history refresh
            failed = [r for r in results if not r.get('ok')]
            if failed and len(failed) == len(results):
                raise TaskError(failed[0].get('error') or t('unfinished'))

        # Mark rows running before launch
        for mid in self.result_rows:
            self.result_rows[mid]['status'].set(t('compare_status_running'))
        self.app.launch(operation, prefer_tab='compare')

    def handle_event(self, kind, payload):
        if kind == 'compare_status':
            model_id, status_key = payload
            row = self.result_rows.get(model_id)
            if row:
                row['status'].set(t(status_key))
                if status_key == 'compare_status_done':
                    row['open_file'].configure(state='normal')
                    row['open_folder'].configure(state='normal')
            return True
        if kind == 'compare_output':
            model_id, output = payload
            row = self.result_rows.get(model_id)
            if row:
                row['output'] = output
                if output and Path(output).exists():
                    row['open_file'].configure(state='normal')
                    row['open_folder'].configure(state='normal')
            return True
        return False

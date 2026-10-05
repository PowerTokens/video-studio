"""PowerTokens Video Studio; run with Python 3.9+ / tkinter."""
import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import uuid
import webbrowser

from i18n import LANGUAGES, LANGUAGE_NAMES, get_language, is_zh, save_language, set_language, t
from studio_ui import (APP_NAME, VERSION, subtitle, promotion_text, FONT, WHITE, INK, LINE, ACCENT, LOGO_FILE,
                       apply_theme, label, heading, card, Columns, Flow, ScrollPage, text_area,
                       load_image, header_logo_file)
from input_helpers import parse_keys, infer_prompt
from batch_ui import BatchTab
from compare_ui import CompareTab
from models import DEFAULT_MODEL_ID, list_models, get_model, resolve_model_id, snap_params, format_adjust_notice
from wan_core import (Client, DATA_DIR, PRICE_CHECKED_DATE, UTM, current_prices, list_prices,
                      TaskError, atomic_json, estimate_cost, fingerprint, payload)

OFFICIAL_KEY_URL = 'https://powertokens.ai/zh-Hans/api-keys?' + UTM
OFFICIAL_KEY_URL_EN = 'https://powertokens.ai/api-keys?' + UTM
KEY_QUOTA_TIP_URL = 'https://powertokens.ai/zh-Hans/api-keys?utm_source=github&utm_medium=app&utm_campaign=video-studio'
KEY_QUOTA_TIP_URL_EN = 'https://powertokens.ai/api-keys?utm_source=github&utm_medium=app&utm_campaign=video-studio'


def key_quota_tip_url():
    return KEY_QUOTA_TIP_URL if is_zh() else KEY_QUOTA_TIP_URL_EN


def official_key_url():
    return OFFICIAL_KEY_URL if is_zh() else OFFICIAL_KEY_URL_EN


def protect(raw, decrypt=False):
    """Windows DPAPI: saved secrets are bound to the current Windows account."""
    if os.name != 'nt':
        raise RuntimeError(t('remember_windows_only'))
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buf = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        kernel.LocalFree(result.data)


class App:
    def __init__(self, root):
        self.root = root
        self.keys, self.records = [], {}
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.busy = False
        self.prompt_timer = None
        self.scale = apply_theme(root)
        root.title('%s · v%s' % (APP_NAME, VERSION))
        # Keep image references on the instance; a missing logo must never stop the app.
        self.icon_image = load_image(LOGO_FILE)
        self.logo_image = load_image(header_logo_file(self.scale))
        if self.icon_image is not None:
            try:
                root.iconphoto(True, self.icon_image)
            except tk.TclError:
                pass
        width = min(int(1280 * self.scale), root.winfo_screenwidth() - 60)
        height = min(int(920 * self.scale), root.winfo_screenheight() - 90)
        root.geometry('%dx%d' % (width, height))
        root.minsize(min(860, width), min(620, height))
        self.build_ui()
        self.load_keys()
        self.refresh_history()
        root.after(150, self.drain)
        root.protocol('WM_DELETE_WINDOW', self.close)

    def build_ui(self):
        """Build every widget in the current language; rebuilt in place by switch_language()."""
        root = self.root
        shell = self.shell = ttk.Frame(root, style='Page.TFrame')
        shell.pack(fill='both', expand=True)
        header = ttk.Frame(shell, padding=(28, 20, 28, 20))
        header.pack(fill='x')
        # Keep the long name on its own line on smaller displays.
        brand = ttk.Frame(header)
        brand.pack(fill='x')
        if self.logo_image is not None:
            self.logo_label = ttk.Label(brand, image=self.logo_image)
            self.logo_label.pack(side='left', padx=(0, 12))
        ttk.Label(brand, text=APP_NAME, style='Brand.TLabel').pack(side='left')
        # 中文 / English switch, top right. Applies immediately (the window is rebuilt in place).
        switch = ttk.Frame(brand)
        switch.pack(side='right')
        self.lang_buttons = {}
        for code in LANGUAGES:
            button = ttk.Button(switch, text=LANGUAGE_NAMES[code], takefocus=0,
                                style='LangActive.TButton' if code == get_language() else 'Lang.TButton',
                                command=lambda code=code: self.switch_language(code))
            button.pack(side='left')
            self.lang_buttons[code] = button
        header_row = ttk.Frame(header)
        header_row.pack(fill='x', pady=(8, 0))
        ttk.Label(header_row, text=subtitle(), style='Muted.TLabel').pack(side='left')
        self.key_badge = tk.StringVar(value=t('badge_no_key'))
        ttk.Button(header_row, text=t('header_get_key'), style='Link.TButton',
                   command=self.open_official_keys).pack(side='right')
        ttk.Label(header_row, textvariable=self.key_badge, style='Badge.TLabel').pack(side='right', padx=12)
        tabs = ttk.Notebook(shell)
        tabs.pack(fill='both', expand=True)
        pages = [ScrollPage(tabs) for _ in range(5)]
        for page, name in zip(pages, ('tab_generate', 'tab_compare', 'tab_batch', 'tab_history', 'tab_keys')):
            tabs.add(page, text=t(name))
        self.tabs, self.pages = tabs, pages
        self.build_create(pages[0].body)
        self.compare = CompareTab(self, pages[1].body)
        self.batch = BatchTab(self, pages[2].body)
        self.build_history(pages[3].body)
        self.build_keys(pages[4].body)
        self.status = tk.StringVar(value=t('status_ready'))
        label(shell, variable=self.status, style='Page.TLabel').pack(fill='x', padx=28, pady=(8, 12))

    def switch_language(self, lang):
        """Switch the UI language now and remember it in settings.json."""
        if lang == get_language():
            return
        if self.busy:
            # Widgets are in use by a running task: remember the choice for the next start.
            try:
                save_language(lang)
            except OSError:
                pass
            messagebox.showinfo(t('lang_saved_title'), t('lang_saved_busy'))
            return
        state = self.capture_state()
        try:
            set_language(lang, persist=True)
        except OSError:
            set_language(lang)  # Still switch for this session if the settings file is not writable.
        if self.prompt_timer:
            self.root.after_cancel(self.prompt_timer)
            self.prompt_timer = None
        self.shell.destroy()
        for name in ('promo_label', 'cost'):  # Rebuilt below; update_cost() must not touch the old ones.
            self.__dict__.pop(name, None)
        self.build_ui()
        self.restore_state(state)

    def capture_state(self):
        return dict(
            tab=self.tabs.index('current'), prompt=self.prompt.get('1.0', 'end-1c'),
            auto=self.auto_prompt.get(), model=getattr(self, 'model_id', tk.StringVar(value=DEFAULT_MODEL_ID)).get(),
            duration=self.duration.get(), resolution=self.resolution.get(),
            ratio=self.ratio.get(), output=self.output.get(), seed=self.seed.get(),
            media={kind: var.get() for kind, var in self.media.items()}, log=self.log.get('1.0', 'end-1c'),
            key_input=self.key_input.get(), show_key=self.show_key.get(), remember=self.remember.get(),
            key_selection=self.key_list.curselection(), task_id=self.task_id.get(),
            video_link=self.video_link.get(), batch=self.batch.export_state(),
            compare=self.compare.export_state() if hasattr(self, 'compare') else {})

    def restore_state(self, state):
        self.auto_prompt.set(state['auto'])
        self.prompt.insert('1.0', state['prompt'])
        self.prompt.edit_modified(False)
        if 'model' in state and hasattr(self, 'model_id'):
            try:
                mid = resolve_model_id(state['model'])
            except KeyError:
                mid = DEFAULT_MODEL_ID
            self.model_id.set(mid)
            self._sync_model_widgets(preserve=True)
        for name in ('duration', 'resolution', 'ratio', 'output', 'seed', 'key_input', 'task_id', 'video_link'):
            if name in state:
                getattr(self, name).set(state[name])
        for kind, value in state['media'].items():
            self.media[kind].set(value)
        self.show_key.set(state['show_key'])
        self.key_entry.configure(show='' if state['show_key'] else '•')
        self.remember.set(state['remember'])
        if state['log']:
            self.log.configure(state='normal')
            self.log.insert('end', state['log'])
            self.log.configure(state='disabled')
        self.sync_keys()
        if state['key_selection'] and state['key_selection'][0] < len(self.keys):
            self.key_list.selection_clear(0, 'end')
            self.key_list.selection_set(state['key_selection'][0])
        if state['auto'] and state['prompt'].strip():
            self.apply_prompt()
        elif not state['auto']:
            self.prompt_hint.set(t('hint_off'))
        self.batch.restore_state(state['batch'])
        if hasattr(self, 'compare'):
            self.compare.import_state(state.get('compare') or {})
        self.update_cost()
        self.refresh_history()
        self.status.set(t('status_idle') if self.keys else t('status_ready'))
        self.tabs.select(state['tab'])

    def build_create(self, frame):
        heading(frame, t('gen_title'), t('gen_desc'))
        columns = Columns(frame, self.scale)
        columns.pack(fill='x')
        prompt_box = card(columns.left, t('prompt_card'), t('prompt_card_desc'))
        self.prompt = text_area(prompt_box, height=6, undo=True)
        self.prompt.pack(fill='x', pady=(0, 12))
        self.auto_prompt = tk.BooleanVar(value=True)
        self.prompt_hint = tk.StringVar(value=t('prompt_hint_default'))
        ttk.Checkbutton(prompt_box, text=t('auto_detect'), variable=self.auto_prompt,
                        command=self.apply_prompt).pack(anchor='w')
        label(prompt_box, variable=self.prompt_hint).pack(fill='x', pady=(4, 0))
        self.prompt.bind('<<Modified>>', self.prompt_changed)
        self.prompt.edit_modified(False)
        params = card(columns.left, t('params_card'), t('params_card_desc'))
        self.model_id = tk.StringVar(value=DEFAULT_MODEL_ID)
        self.model_label = tk.StringVar()
        model_row = ttk.Frame(params)
        model_row.pack(fill='x', pady=(0, 12))
        ttk.Label(model_row, text=t('param_model'), style='Muted.TLabel').pack(anchor='w', pady=(0, 8))
        self.model_combo = ttk.Combobox(model_row, textvariable=self.model_label, state='readonly', width=28)
        self.model_combo.pack(fill='x')
        self.model_desc = tk.StringVar()
        label(model_row, variable=self.model_desc).pack(fill='x', pady=(6, 0))
        self.model_adjust = tk.StringVar()
        self.model_adjust_label = label(model_row, variable=self.model_adjust, style='Badge.TLabel')
        self.model_adjust_label.pack(fill='x', pady=(6, 0))
        self.model_adjust_label.pack_forget()
        self._model_labels = {}
        self._refresh_model_combo()
        self.model_combo.bind('<<ComboboxSelected>>', lambda *_: self._on_model_picked())
        row = ttk.Frame(params)
        row.pack(fill='x')
        self.duration, self.resolution, self.ratio = tk.StringVar(value='5'), tk.StringVar(value='720p'), tk.StringVar(value='16:9')
        self.duration_spin = self.resolution_combo = self.ratio_combo = None
        widgets = []
        for index, (name, var, kind) in enumerate([
                (t('param_duration'), self.duration, 'duration'),
                (t('param_resolution'), self.resolution, 'resolution'),
                (t('param_ratio'), self.ratio, 'ratio')]):
            row.columnconfigure(index, weight=1, uniform='params')
            field = ttk.Frame(row)
            field.grid(row=0, column=index, sticky='ew', padx=(0, 12 if index < 2 else 0))
            ttk.Label(field, text=name, style='Muted.TLabel').pack(anchor='w', pady=(0, 8))
            if kind == 'duration':
                widget = ttk.Spinbox(field, from_=2, to=30, textvariable=var, width=6)
                self.duration_spin = widget
            else:
                widget = ttk.Combobox(field, textvariable=var, values=('720p', '1080p') if kind == 'resolution' else ('16:9', '9:16', '1:1'),
                                      state='readonly', width=6)
                if kind == 'resolution':
                    self.resolution_combo = widget
                else:
                    self.ratio_combo = widget
            widget.pack(fill='x')
            var.trace_add('write', lambda *_: self.update_cost())
        self.cost = tk.StringVar()
        label(params, variable=self.cost).pack(fill='x', pady=(12, 0))
        self._sync_model_widgets()
        self.update_cost()
        save_box = card(columns.left, t('save_card'))
        ttk.Label(save_box, text=t('output_folder'), style='Muted.TLabel').pack(anchor='w', pady=(0, 6))
        row = ttk.Frame(save_box)
        row.pack(fill='x')
        self.output = tk.StringVar(value=str(Path.home() / 'Videos' / 'PowerTokensVideoStudio'))
        ttk.Entry(row, textvariable=self.output, width=12).pack(side='left', fill='x', expand=True)
        ttk.Button(row, text=t('browse'), command=self.choose_folder).pack(side='left', padx=(10, 0))
        actions = Flow(save_box)
        actions.pack(fill='x', pady=(12, 0))
        self.generate_btn = ttk.Button(actions, text=t('generate_btn'), style='Accent.TButton', command=self.generate)
        self.stop_btn = ttk.Button(actions, text=t('stop_btn'), command=self.stop_wait, state='disabled')
        actions.schedule()
        label(save_box, t('cloud_note')).pack(fill='x', pady=(6, 12))
        self.bar = ttk.Progressbar(save_box, mode='indeterminate')
        self.bar.pack(fill='x')
        advanced = card(columns.right, t('media_card'), t('media_card_desc'))
        self.media = {}
        for kind in ('first_frame', 'last_frame', 'reference_image', 'reference_video', 'reference_audio'):
            ttk.Label(advanced, text=t('media_' + kind), style='Muted.TLabel').pack(anchor='w', pady=(0, 6))
            var = tk.StringVar()
            ttk.Entry(advanced, textvariable=var, width=12).pack(fill='x', pady=(0, 12))
            self.media[kind] = var
        ttk.Label(advanced, text=t('seed_label'), style='Muted.TLabel').pack(anchor='w', pady=(0, 6))
        self.seed = tk.StringVar()
        ttk.Entry(advanced, textvariable=self.seed, width=12).pack(fill='x')
        current = card(columns.right, t('current_card'))
        self.current_model_badge = tk.StringVar()
        label(current, variable=self.current_model_badge, style='Badge.TLabel').pack(fill='x')
        label(current, t('current_saved')).pack(fill='x', pady=(10, 0))
        self._update_current_model_badge()
        logs = card(frame, t('progress_card'))
        self.log = text_area(logs, height=5, state='disabled')
        self.log.pack(fill='x')

    def build_history(self, frame):
        heading(frame, t('history_title'), t('history_desc'))
        table_card = card(frame, t('history_card'), t('history_card_desc'))
        table = ttk.Frame(table_card)
        table.pack(fill='both', expand=True)
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=('time', 'task', 'state', 'key'), show='headings', selectmode='extended', height=8)
        for column, name, width in [('time', 'col_created', 155), ('task', 'col_task', 350), ('state', 'col_state', 230), ('key', 'col_key', 90)]:
            self.tree.heading(column, text=t(name))
            self.tree.column(column, width=width, minwidth=60)
        self.tree.grid(row=0, column=0, sticky='nsew')
        sy = ttk.Scrollbar(table, command=self.tree.yview)
        sy.grid(row=0, column=1, sticky='ns')
        sx = ttk.Scrollbar(table, orient='horizontal', command=self.tree.xview)
        sx.grid(row=1, column=0, sticky='ew')
        self.tree.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        self.tree.bind('<Control-c>', self.copy_selected_task_ids)
        self.tree.bind('<Control-a>', self.select_all_task_rows)
        row = Flow(table_card)
        row.pack(fill='x', pady=(10, 0))
        self.resume_btn = ttk.Button(row, text=t('resume_selected'), style='Accent.TButton', command=self.resume_selected)
        ttk.Button(row, text=t('refresh'), command=self.refresh_history)
        ttk.Button(row, text=t('copy_selected_ids'), command=self.copy_selected_task_ids)
        ttk.Button(row, text=t('copy_all_ids'), command=self.copy_all_task_ids)
        ttk.Button(row, text=t('open_folder'), command=self.open_folder)
        row.schedule()
        box = card(frame, t('manual_card'), t('manual_card_desc'))
        row = ttk.Frame(box)
        row.pack(fill='x')
        self.task_id = tk.StringVar()
        ttk.Entry(row, textvariable=self.task_id, width=12).pack(side='left', fill='x', expand=True)
        self.manual_btn = ttk.Button(row, text=t('manual_btn'), command=self.resume_manual)
        self.manual_btn.pack(side='left', padx=(10, 0))
        link_box = card(frame, t('link_card'), t('link_card_desc'))
        row = ttk.Frame(link_box)
        row.pack(fill='x')
        self.video_link = tk.StringVar()
        ttk.Entry(row, textvariable=self.video_link, width=12).pack(side='left', fill='x', expand=True)
        self.link_btn = ttk.Button(row, text=t('link_btn'), command=self.resume_from_link)
        self.link_btn.pack(side='left', padx=(10, 0))
        label(frame, t('history_footer'), style='Page.TLabel').pack(fill='x')

    def build_keys(self, frame):
        heading(frame, t('keys_title'), t('keys_desc'))
        columns = Columns(frame, self.scale)
        columns.pack(fill='x')
        add = card(columns.left, t('add_card'), t('add_card_desc'))
        self.key_input = tk.StringVar()
        key_row = ttk.Frame(add)
        key_row.pack(fill='x', pady=(0, 10))
        self.key_entry = ttk.Entry(key_row, textvariable=self.key_input, show='•', width=12)
        self.key_entry.pack(side='left', fill='x', expand=True)
        self.show_key = tk.BooleanVar(value=False)
        ttk.Checkbutton(key_row, text=t('show_key'), variable=self.show_key,
                        command=lambda: self.key_entry.configure(show='' if self.show_key.get() else '•')).pack(side='left', padx=(10, 0))
        self.key_entry.bind('<Return>', lambda event: self.add_keys())
        label(add, t('paste_hint')).pack(fill='x', pady=(0, 12))
        ttk.Button(add, text=t('add_btn'), style='Accent.TButton', command=self.add_keys).pack(anchor='w')
        pool = card(columns.left, t('pool_card'), t('pool_card_desc'))
        self.key_count = tk.StringVar(value=t('key_count', 0, count=0))
        label(pool, variable=self.key_count, style='Badge.TLabel').pack(anchor='w', pady=(0, 12))
        list_row = ttk.Frame(pool)
        list_row.pack(fill='x')
        self.key_list = tk.Listbox(list_row, height=8, width=1, exportselection=False, font=(FONT, 11),
                                  background=WHITE, foreground=INK, relief='flat', borderwidth=0,
                                  highlightthickness=1, highlightbackground=LINE, highlightcolor=ACCENT,
                                  selectbackground='#e3f3ed', selectforeground='#236a5e', activestyle='none')
        self.key_list.pack(side='left', fill='x', expand=True)
        scroll = ttk.Scrollbar(list_row, command=self.key_list.yview)
        scroll.pack(side='right', fill='y')
        self.key_list.configure(yscrollcommand=scroll.set)
        ttk.Button(pool, text=t('remove_key'), command=self.remove_key).pack(anchor='w', pady=(12, 0))
        label(pool, t('pool_note')).pack(fill='x', pady=(10, 0))
        label(pool, t('key_quota_tip'), style='Badge.TLabel').pack(fill='x', pady=(12, 0))
        self.key_quota_btn = ttk.Button(pool, text=t('key_quota_tip_btn'), style='Link.TButton',
                                        command=self.open_key_quota_tip)
        self.key_quota_btn.pack(anchor='w', pady=(8, 0))
        get = card(columns.right, t('get_card'), t('get_card_desc'))
        self.promo_label = label(get, promotion_text(), style='Badge.TLabel')
        self.get_key_btn = ttk.Button(get, text=t('get_btn'), style='Accent.TButton', command=self.open_official_keys)
        self.get_key_btn.pack(fill='x')
        self.refresh_promo()
        label(get, t('get_note')).pack(fill='x', pady=(12, 0))
        save = card(columns.right, t('local_card'))
        self.remember = tk.BooleanVar(value=False)
        ttk.Checkbutton(save, text=t('remember'), variable=self.remember, command=self.save_keys).pack(anchor='w')
        label(save, t('dpapi_badge'), style='Badge.TLabel').pack(anchor='w', pady=(10, 14))
        label(save, t('remember_note')).pack(fill='x')
        ttk.Separator(save).pack(fill='x', pady=16)
        label(save, t('no_repeat_note')).pack(fill='x')

    def prompt_changed(self, event=None):
        if not self.prompt.edit_modified():
            return
        self.prompt.edit_modified(False)
        if self.prompt_timer:
            self.root.after_cancel(self.prompt_timer)
        self.prompt_timer = self.root.after(400, self.apply_prompt)

    def apply_prompt(self):
        self.prompt_timer = None
        if not self.auto_prompt.get():
            self.prompt_hint.set(t('hint_off'))
            return {'warnings': []}
        result = infer_prompt(self.prompt.get('1.0', 'end'))
        hints = []
        if result['duration'] is not None:
            self.duration.set(str(result['duration']))
            hints.append(t('hint_seconds', result['duration']))
        if result['ratio'] is not None:
            self.ratio.set(result['ratio'])
            hints.append(result['ratio'])
        if result['warnings']:
            self.prompt_hint.set(t('warning_sep').join(result['warnings']))
        elif hints:
            # e.g. "已识别：16 秒 · 16:9" plus "时长根据分镜时间轴推断…" when inferred from shot ranges.
            self.prompt_hint.set(t('hint_detected') + ' · '.join(hints) +
                                 ''.join(t('hint_note_sep') + n for n in result.get('notes', [])))
        else:
            self.prompt_hint.set(t('hint_none'))
        return result

    def refresh_promo(self):
        """Show the campaign badge only while the discount runs; hides itself after PROMO_END_DATE."""
        if not hasattr(self, 'promo_label'):
            return
        text = promotion_text()
        if text:
            self.promo_label.configure(text=text)
            if not self.promo_label.winfo_manager():
                self.promo_label.pack(fill='x', pady=(0, 16), before=self.get_key_btn)
        else:
            self.promo_label.pack_forget()

    def _refresh_model_combo(self):
        self._model_labels = {}
        labels = []
        for spec in list_models():
            label_text = spec.label()
            self._model_labels[label_text] = spec.id
            labels.append(label_text)
        self.model_combo.configure(values=labels)
        current = get_model(self.model_id.get())
        self.model_label.set(current.label())
        self._update_model_description()

    def _on_model_picked(self):
        mid = self._model_labels.get(self.model_label.get(), DEFAULT_MODEL_ID)
        self.model_id.set(mid)
        self._sync_model_widgets()
        self.update_cost()

    def _sync_model_widgets(self, preserve=False):
        spec = get_model(self.model_id.get())
        if self.duration_spin is not None:
            self.duration_spin.configure(from_=spec.durations[0], to=spec.durations[-1])
        if self.resolution_combo is not None:
            self.resolution_combo.configure(values=spec.resolutions)
        if self.ratio_combo is not None:
            self.ratio_combo.configure(values=spec.ratios)
        changes = []
        if not preserve:
            try:
                old_duration = int(self.duration.get())
            except (TypeError, ValueError):
                old_duration = self.duration.get()
            old_resolution = self.resolution.get()
            old_ratio = self.ratio.get()
            new_duration, new_resolution, new_ratio, changes = snap_params(
                spec, old_duration, old_resolution, old_ratio)
            if str(self.duration.get()) != str(new_duration):
                self.duration.set(str(new_duration))
            if self.resolution.get() != new_resolution:
                self.resolution.set(new_resolution)
            if self.ratio.get() != new_ratio:
                self.ratio.set(new_ratio)
            self._show_model_adjust(changes)
        else:
            self._show_model_adjust([])
        self._update_current_model_badge()
        self._update_model_description()

    def _show_model_adjust(self, changes):
        if not hasattr(self, 'model_adjust'):
            return
        if changes:
            spec = get_model(self.model_id.get())
            self.model_adjust.set(format_adjust_notice(spec, changes))
            if not self.model_adjust_label.winfo_manager():
                self.model_adjust_label.pack(fill='x', pady=(6, 0))
        else:
            self.model_adjust.set('')
            self.model_adjust_label.pack_forget()

    def _update_model_description(self):
        if not hasattr(self, 'model_desc'):
            return
        spec = get_model(self.model_id.get())
        self.model_desc.set(spec.description())

    def _update_current_model_badge(self):
        if not hasattr(self, 'current_model_badge'):
            return
        spec = get_model(self.model_id.get())
        self.current_model_badge.set(t('current_model_fmt', spec.label(), spec.id))

    def update_cost(self):
        if not hasattr(self, 'cost'):
            return
        self.refresh_promo()  # Also catches the end date passing while the app stays open.
        try:
            mid = self.model_id.get() if hasattr(self, 'model_id') else DEFAULT_MODEL_ID
            resolution = self.resolution.get()
            value = estimate_cost(self.duration.get(), resolution, model_id=mid)
            prices = current_prices(model_id=mid)
            text = t('cost_estimate', PRICE_CHECKED_DATE, value, prices[resolution])
            regular = list_prices(model_id=mid)
            if resolution in regular:
                text += t('cost_regular', regular[resolution])
            self.cost.set(text + t('cost_tail'))
        except (ValueError, KeyError, TaskError):
            self.cost.set(t('cost_invalid'))

    def open_key_quota_tip(self):
        url = key_quota_tip_url()
        try:
            if not webbrowser.open(url):
                messagebox.showinfo(t('official_site'), url)
        except OSError:
            messagebox.showinfo(t('official_site'), url)

    def open_official_keys(self):
        try:
            if not webbrowser.open(official_key_url()):
                messagebox.showinfo(t('official_site'), official_key_url())
        except OSError:
            messagebox.showinfo(t('official_site'), official_key_url())

    def choose_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.output.set(folder)

    def sync_keys(self):
        self.key_list.delete(0, 'end')
        for i, key in enumerate(self.keys):
            self.key_list.insert('end', '%d. ****%s' % (i + 1, key[-4:]))
        self.key_count.set(t('key_count', len(self.keys), count=len(self.keys)))
        self.key_badge.set(t('badge_keys', len(self.keys), count=len(self.keys)) if self.keys else t('badge_no_key'))
        if self.keys:
            self.key_list.selection_set(0)

    def add_keys(self):
        try:
            keys = parse_keys(self.key_input.get())
        except ValueError as exc:
            messagebox.showerror(t('key_format_title'), str(exc))
            return
        self.keys = list(dict.fromkeys(self.keys + keys))
        self.key_input.set('')
        self.status.set(t('keys_added_status', len(self.keys), count=len(self.keys)))
        self.sync_keys()
        self.save_keys()

    def remove_key(self):
        selected = self.key_list.curselection()
        if selected:
            self.keys.pop(selected[0])
            self.sync_keys()
            self.save_keys()

    def save_keys(self):
        path = DATA_DIR / 'keys.json'
        try:
            if self.remember.get():
                encrypted = protect(json.dumps(self.keys).encode())
                atomic_json(path, {'dpapi': base64.b64encode(encrypted).decode()})
            else:
                path.unlink(missing_ok=True)
        except Exception:
            self.remember.set(False)
            messagebox.showerror(t('save_failed_title'), t('save_failed_body'))

    def load_keys(self):
        path = DATA_DIR / 'keys.json'
        if path.exists():
            try:
                encrypted = base64.b64decode(json.loads(path.read_text(encoding='utf-8'))['dpapi'])
                self.keys = json.loads(protect(encrypted, decrypt=True))
                self.remember.set(True)
            except Exception:
                messagebox.showwarning(t('load_failed_title'), t('load_failed_body'))
        self.sync_keys()

    def selected_key(self):
        if not self.keys:
            raise TaskError(t('need_key'))
        selected = self.key_list.curselection()
        return self.keys[selected[0] if selected else 0]

    def new_output(self):
        folder = Path(self.output.get().strip()).expanduser()
        folder.mkdir(parents=True, exist_ok=True)
        # Validate write access before making a chargeable request.
        test = folder / ('.write-test-' + uuid.uuid4().hex)
        test.touch()
        test.unlink()
        return str(folder / ('wan_' + uuid.uuid4().hex[:12] + '.mp4'))

    def generate(self):
        if self.busy:
            return
        try:
            if not self.keys:
                raise TaskError(t('need_key'))
            recognized = self.apply_prompt()
            if recognized['warnings']:
                raise TaskError(t('warning_sep').join(recognized['warnings']))
            request = payload(self.prompt.get('1.0', 'end'), self.duration.get(), self.resolution.get(),
                self.ratio.get(), [{'type': k, 'url': v.get().strip()} for k, v in self.media.items() if v.get().strip()],
                self.seed.get(), model=self.model_id.get())
            output = self.new_output()
        except Exception as exc:
            messagebox.showerror(t('check_input'), str(exc))
            return
        keys = list(self.keys)
        self.launch(lambda client: client.submit(keys, request, output))

    def resume_selected(self):
        if self.busy:
            return
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo(t('select_task_title'), t('select_task_body'))
            return
        record = self.records[selected[0]]
        try:
            if not record.get('task_id'):
                raise TaskError(t('record_no_id'))
            key = next((k for k in self.keys if fingerprint(k) == record.get('key_hash')), None)
            if not key:
                raise TaskError(t('need_original_key') + record.get('key_hint', ''))
            output = record['output']
            if Path(output).exists():
                output = self.new_output()
            self.launch(lambda client: client.resume(key, record['task_id'], output, record))
        except Exception as exc:
            messagebox.showerror(t('cannot_resume'), str(exc))

    def resume_manual(self):
        if self.busy:
            return
        try:
            from wan_core import task_url
            task = self.task_id.get().strip()
            task_url(task)
            key, output = self.selected_key(), self.new_output()
            self.launch(lambda client: client.resume(key, task, output))
        except Exception as exc:
            messagebox.showerror(t('check_input'), str(exc))

    def resume_from_link(self):
        if self.busy:
            return
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo(t('select_task_title'), t('select_download_body'))
            return
        try:
            record = self.records[selected[0]]
            key = next((k for k in self.keys if fingerprint(k) == record.get('key_hash')), None)
            if not key:
                raise TaskError(t('need_original_key') + record.get('key_hint', ''))
            link = self.video_link.get().strip()
            if not link:
                raise TaskError(t('need_link'))
            output = record['output']
            if Path(output).exists():
                output = self.new_output()
            self.launch(lambda client: client.resume_download_url(key, record['task_id'], output, link, record))
        except Exception as exc:
            messagebox.showerror(t('link_failed_title'), str(exc))

    def launch(self, operation, prefer_tab=None):
        self.busy = True
        self.stop.clear()
        for button in (self.generate_btn, self.resume_btn, self.manual_btn, self.link_btn):
            button.configure(state='disabled')
        self.stop_btn.configure(state='normal')
        self.batch.set_busy(True)
        if hasattr(self, 'compare'):
            self.compare.set_busy(True)
        self.bar.start(12)
        tab_map = {'generate': 0, 'compare': 1, 'batch': 2, 'history': 3, 'keys': 4}
        self.tabs.select(tab_map.get(prefer_tab, 0))
        self.status.set(t('working'))
        def worker():
            client = Client(report=lambda text: self.events.put(('log', text)), stop=self.stop)
            try:
                operation(client)
                self.events.put(('done', t('downloaded')))
            except TaskError as exc:
                self.events.put(('done', str(exc)))
            except Exception:
                self.events.put(('done', t('unfinished')))
        threading.Thread(target=worker, daemon=True).start()

    def stop_wait(self):
        self.stop.set()
        self.status.set(t('stopping_status'))

    def drain(self):
        try:
            while True:
                kind, text = self.events.get_nowait()
                if kind == 'batch_update':
                    self.batch.update_job(text)
                    continue
                self.log.configure(state='normal')
                self.log.insert('end', text + '\n')
                self.log.see('end')
                self.log.configure(state='disabled')
                if kind in ('compare_status', 'compare_output'):
                    if hasattr(self, 'compare'):
                        self.compare.handle_event(kind, text)
                    continue
                if kind == 'done':
                    self.busy = False
                    self.batch.set_busy(False)
                    if hasattr(self, 'compare'):
                        self.compare.set_busy(False)
                    self.bar.stop()
                    for button in (self.generate_btn, self.resume_btn, self.manual_btn, self.link_btn):
                        button.configure(state='normal')
                    self.stop_btn.configure(state='disabled')
                    self.status.set(text)
                self.refresh_history()
        except queue.Empty:
            pass
        self.root.after(150, self.drain)

    def refresh_history(self):
        selection = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        self.records = {}
        folder = DATA_DIR / 'tasks'
        for path in sorted(folder.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                row = json.loads(path.read_text(encoding='utf-8'))
                self.records[path.stem] = row
                task_label = row.get('task_id') or t('no_task_id')
                if row.get('compare_id'):
                    task_label = t('compare_group_tag', row['compare_id'][:6]) + ' · ' + task_label
                self.tree.insert('', 'end', iid=path.stem, values=(row['created'], task_label, row['state'], row.get('key_hint', '')))
            except (OSError, ValueError, KeyError):
                continue
        retained = [item for item in selection if item in self.records]
        if retained:
            self.tree.selection_set(*retained)

    def select_all_task_rows(self, event=None):
        rows = self.tree.get_children()
        if rows:
            self.tree.selection_set(*rows)
        return 'break'

    def copy_selected_task_ids(self, event=None):
        selected = set(self.tree.selection())
        task_ids = [self.records[item].get('task_id') for item in self.tree.get_children()
                    if item in selected and self.records[item].get('task_id')]
        self.copy_task_ids_to_clipboard(task_ids)
        return 'break'

    def copy_all_task_ids(self):
        task_ids = [self.records[item].get('task_id') for item in self.tree.get_children()
                    if self.records[item].get('task_id')]
        self.copy_task_ids_to_clipboard(task_ids)

    def copy_task_ids_to_clipboard(self, task_ids):
        task_ids = list(dict.fromkeys(task_ids))
        if not task_ids:
            self.status.set(t('nothing_to_copy'))
            return
        self.root.clipboard_clear()
        self.root.clipboard_append('\n'.join(task_ids))
        self.root.update_idletasks()
        self.status.set(t('copied_ids', len(task_ids), count=len(task_ids)))

    def open_folder(self):
        selected = self.tree.selection()
        folder = Path(self.records[selected[0]]['output']).parent if selected else Path(self.output.get())
        folder.mkdir(parents=True, exist_ok=True)
        if os.name == 'nt':
            os.startfile(str(folder))
        else:
            messagebox.showinfo(t('save_location'), str(folder))

    def close(self):
        if self.busy:
            self.stop_wait()
            messagebox.showinfo(t('closing_title'), t('closing_body'))
            return
        self.root.destroy()


if __name__ == '__main__':
    if os.name == 'nt':
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    App(root)
    root.mainloop()

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

from studio_ui import (APP_NAME, VERSION, SUBTITLE, promotion_text, FONT, WHITE, INK, LINE, ACCENT, LOGO_FILE,
                       apply_theme, label, heading, card, Columns, Flow, ScrollPage, text_area,
                       load_image, header_logo_file)
from input_helpers import parse_keys, infer_prompt
from batch_ui import BatchTab
from wan_core import (Client, DATA_DIR, PRICE_CHECKED_DATE, UTM, current_prices, list_prices,
                      TaskError, atomic_json, estimate_cost, fingerprint, payload)

OFFICIAL_KEY_URL = 'https://powertokens.ai/zh-Hans/api-keys?' + UTM


def protect(raw, decrypt=False):
    """Windows DPAPI: saved secrets are bound to the current Windows account."""
    if os.name != 'nt':
        raise RuntimeError('记住 Key 功能仅支持 Windows；其他系统可临时使用 Key。')
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
        shell = ttk.Frame(root, style='Page.TFrame')
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
        header_row = ttk.Frame(header)
        header_row.pack(fill='x', pady=(8, 0))
        ttk.Label(header_row, text=SUBTITLE, style='Muted.TLabel').pack(side='left')
        self.key_badge = tk.StringVar(value='尚未添加 API Key')
        ttk.Button(header_row, text='获取 API Key ↗', style='Link.TButton',
                   command=self.open_official_keys).pack(side='right')
        ttk.Label(header_row, textvariable=self.key_badge, style='Badge.TLabel').pack(side='right', padx=12)
        tabs = ttk.Notebook(shell)
        tabs.pack(fill='both', expand=True)
        pages = [ScrollPage(tabs) for _ in range(4)]
        for page, name in zip(pages, ('生成视频', '批量导入', '任务记录 / 恢复', 'API Key')):
            tabs.add(page, text=name)
        self.tabs, self.pages = tabs, pages
        self.build_create(pages[0].body)
        self.batch = BatchTab(self, pages[1].body)
        self.build_history(pages[2].body)
        self.build_keys(pages[3].body)
        self.status = tk.StringVar(value='就绪 · 请先在「API Key」页添加 Key')
        label(shell, variable=self.status, style='Page.TLabel').pack(fill='x', padx=28, pady=(8, 12))
        self.load_keys()
        self.refresh_history()
        root.after(150, self.drain)
        root.protocol('WM_DELETE_WINDOW', self.close)

    def build_create(self, frame):
        heading(frame, '生成视频', '输入创意，调整参数，生成带原生音效的视频。')
        columns = Columns(frame, self.scale)
        columns.pack(fill='x')
        prompt_box = card(columns.left, '视频描述', '用自然语言描述画面、镜头、动作与声音。')
        self.prompt = text_area(prompt_box, height=6, undo=True)
        self.prompt.pack(fill='x', pady=(0, 12))
        self.auto_prompt = tk.BooleanVar(value=True)
        self.prompt_hint = tk.StringVar(value='可写：20 秒、9:16 竖屏。未识别到的参数使用下方选择。')
        ttk.Checkbutton(prompt_box, text='从提示词自动识别时长和比例', variable=self.auto_prompt,
                        command=self.apply_prompt).pack(anchor='w')
        label(prompt_box, variable=self.prompt_hint).pack(fill='x', pady=(4, 0))
        self.prompt_timer = None
        self.prompt.bind('<<Modified>>', self.prompt_changed)
        self.prompt.edit_modified(False)
        params = card(columns.left, '生成参数', '自动识别优先；取消勾选后可手动修改时长与比例。')
        row = ttk.Frame(params)
        row.pack(fill='x')
        self.duration, self.resolution, self.ratio = tk.StringVar(value='5'), tk.StringVar(value='720p'), tk.StringVar(value='16:9')
        for index, (name, var, values) in enumerate([
                ('时长（秒）', self.duration, None), ('分辨率', self.resolution, ('720p', '1080p')),
                ('画面比例', self.ratio, ('16:9', '9:16', '1:1'))]):
            row.columnconfigure(index, weight=1, uniform='params')
            field = ttk.Frame(row)
            field.grid(row=0, column=index, sticky='ew', padx=(0, 12 if index < 2 else 0))
            ttk.Label(field, text=name, style='Muted.TLabel').pack(anchor='w', pady=(0, 8))
            widget = ttk.Combobox(field, textvariable=var, values=values, state='readonly', width=6) if values else ttk.Spinbox(field, from_=2, to=30, textvariable=var, width=6)
            widget.pack(fill='x')
            var.trace_add('write', lambda *_: self.update_cost())
        self.cost = tk.StringVar()
        label(params, variable=self.cost).pack(fill='x', pady=(12, 0))
        self.update_cost()
        save_box = card(columns.left, '保存与生成')
        ttk.Label(save_box, text='保存文件夹', style='Muted.TLabel').pack(anchor='w', pady=(0, 6))
        row = ttk.Frame(save_box)
        row.pack(fill='x')
        self.output = tk.StringVar(value=str(Path.home() / 'Videos' / 'PowerTokensVideoStudio'))
        ttk.Entry(row, textvariable=self.output, width=12).pack(side='left', fill='x', expand=True)
        ttk.Button(row, text='选择…', command=self.choose_folder).pack(side='left', padx=(10, 0))
        actions = Flow(save_box)
        actions.pack(fill='x', pady=(12, 0))
        self.generate_btn = ttk.Button(actions, text='生成视频', style='Accent.TButton', command=self.generate)
        self.stop_btn = ttk.Button(actions, text='停止等待', command=self.stop_wait, state='disabled')
        actions.schedule()
        label(save_box, '任务仍在云端继续，可在任务记录里找回').pack(fill='x', pady=(6, 12))
        self.bar = ttk.Progressbar(save_box, mode='indeterminate')
        self.bar.pack(fill='x')
        advanced = card(columns.right, '可选素材', '填写网络可访问的图片 / 视频 / 音频链接，暂不支持本地上传；不需要可留空')
        self.media = {}
        for name, kind in [('首帧图片', 'first_frame'), ('尾帧图片', 'last_frame'),
                           ('参考图片', 'reference_image'), ('参考视频', 'reference_video'), ('参考音频', 'reference_audio')]:
            ttk.Label(advanced, text=name, style='Muted.TLabel').pack(anchor='w', pady=(0, 6))
            var = tk.StringVar()
            ttk.Entry(advanced, textvariable=var, width=12).pack(fill='x', pady=(0, 12))
            self.media[kind] = var
        ttk.Label(advanced, text='随机种子（留空则随机）', style='Muted.TLabel').pack(anchor='w', pady=(0, 6))
        self.seed = tk.StringVar()
        ttk.Entry(advanced, textvariable=self.seed, width=12).pack(fill='x')
        current = card(columns.right, '当前配置')
        label(current, 'wan3.0-video · 原生音频开启', style='Badge.TLabel').pack(fill='x')
        label(current, '下载完成后自动保存到本地。').pack(fill='x', pady=(10, 0))
        logs = card(frame, '任务进度')
        self.log = text_area(logs, height=5, state='disabled')
        self.log.pack(fill='x')

    def build_history(self, frame):
        heading(frame, '任务记录 / 恢复', '继续查询原任务并下载，不会提交新生成任务。')
        table_card = card(frame, '任务记录', '可用 Ctrl / Shift 多选；按 Ctrl+C 复制任务 ID。')
        table = ttk.Frame(table_card)
        table.pack(fill='both', expand=True)
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=('time', 'task', 'state', 'key'), show='headings', selectmode='extended', height=8)
        for column, name, width in [('time', '创建时间', 155), ('task', '任务 ID', 350), ('state', '状态', 230), ('key', 'Key', 90)]:
            self.tree.heading(column, text=name)
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
        self.resume_btn = ttk.Button(row, text='继续查询选中任务', style='Accent.TButton', command=self.resume_selected)
        ttk.Button(row, text='刷新', command=self.refresh_history)
        ttk.Button(row, text='复制选中 ID', command=self.copy_selected_task_ids)
        ttk.Button(row, text='复制全部 ID', command=self.copy_all_task_ids)
        ttk.Button(row, text='打开视频文件夹', command=self.open_folder)
        row.schedule()
        box = card(frame, '恢复其他任务', '手动恢复使用 API Key 页选中的 Key，需为提交该任务的原 Key。')
        row = ttk.Frame(box)
        row.pack(fill='x')
        self.task_id = tk.StringVar()
        ttk.Entry(row, textvariable=self.task_id, width=12).pack(side='left', fill='x', expand=True)
        self.manual_btn = ttk.Button(row, text='查询 / 下载', command=self.resume_manual)
        self.manual_btn.pack(side='left', padx=(10, 0))
        link_box = card(frame, '用视频链接恢复选中任务', '先选中任务，再粘贴该视频的有效下载链接。')
        row = ttk.Frame(link_box)
        row.pack(fill='x')
        self.video_link = tk.StringVar()
        ttk.Entry(row, textvariable=self.video_link, width=12).pack(side='left', fill='x', expand=True)
        self.link_btn = ttk.Button(row, text='从链接续传', command=self.resume_from_link)
        self.link_btn.pack(side='left', padx=(10, 0))
        label(frame, '停止等待或关闭窗口不会取消任务；任务仍在云端继续并可能产生费用，可在任务记录里找回。', style='Page.TLabel').pack(fill='x')

    def build_keys(self, frame):
        heading(frame, 'API Key 管理', '添加多个 Key 供视频任务使用；Key 池仅以掩码显示。')
        columns = Columns(frame, self.scale)
        columns.pack(fill='x')
        add = card(columns.left, '添加 API Key', '一次可粘贴多个 Key，用空格、逗号或分号分隔。')
        self.key_input = tk.StringVar()
        key_row = ttk.Frame(add)
        key_row.pack(fill='x', pady=(0, 10))
        self.key_entry = ttk.Entry(key_row, textvariable=self.key_input, show='•', width=12)
        self.key_entry.pack(side='left', fill='x', expand=True)
        self.show_key = tk.BooleanVar(value=False)
        ttk.Checkbutton(key_row, text='显示', variable=self.show_key,
                        command=lambda: self.key_entry.configure(show='' if self.show_key.get() else '•')).pack(side='left', padx=(10, 0))
        self.key_entry.bind('<Return>', lambda event: self.add_keys())
        label(add, '直接粘贴从 PowerTokens 官网复制的 Key 即可').pack(fill='x', pady=(0, 12))
        ttk.Button(add, text='添加到 Key 池', style='Accent.TButton', command=self.add_keys).pack(anchor='w')
        pool = card(columns.left, 'Key 池', '生成按顺序使用 Key；只有提交被明确拒绝时才换下一个。')
        self.key_count = tk.StringVar(value='共 0 个 Key')
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
        ttk.Button(pool, text='移除选中 Key', command=self.remove_key).pack(anchor='w', pady=(12, 0))
        label(pool, '不会因状态接口的 403 或网络故障自动删除 Key。').pack(fill='x', pady=(10, 0))
        get = card(columns.right, '获取 Key', '还没有 Key？去 PowerTokens 注册即可使用。本工具目前仅支持 Wan 3.0 视频模型。')
        self.promo_label = label(get, promotion_text(), style='Badge.TLabel')
        self.get_key_btn = ttk.Button(get, text='注册 / 获取 API Key ↗', style='Accent.TButton', command=self.open_official_keys)
        self.get_key_btn.pack(fill='x')
        self.refresh_promo()
        label(get, '打开 PowerTokens 官网 API Key 页面').pack(fill='x', pady=(12, 0))
        save = card(columns.right, '本机保存')
        self.remember = tk.BooleanVar(value=False)
        ttk.Checkbutton(save, text='在这台电脑记住 Key', variable=self.remember, command=self.save_keys).pack(anchor='w')
        label(save, 'Windows 账户加密', style='Badge.TLabel').pack(anchor='w', pady=(10, 14))
        label(save, '默认只在本次运行中保留 Key。勾选后使用 Windows DPAPI 加密保存。').pack(fill='x')
        ttk.Separator(save).pack(fill='x', pady=16)
        label(save, '任务已提交、网络超时或提交结果不明确时，不会自动重复生成。').pack(fill='x')

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
            self.prompt_hint.set('自动识别已关闭，使用下方手动选择的时长和比例。')
            return {'warnings': []}
        result = infer_prompt(self.prompt.get('1.0', 'end'))
        hints = []
        if result['duration'] is not None:
            self.duration.set(str(result['duration']))
            hints.append('%s 秒' % result['duration'])
        if result['ratio'] is not None:
            self.ratio.set(result['ratio'])
            hints.append(result['ratio'])
        if result['warnings']:
            self.prompt_hint.set('；'.join(result['warnings']))
        elif hints:
            # e.g. "已识别：16 秒 · 16:9" plus "时长根据分镜时间轴推断…" when inferred from shot ranges.
            self.prompt_hint.set('已识别：' + ' · '.join(hints) + ''.join('。' + n for n in result.get('notes', [])))
        else:
            self.prompt_hint.set('未识别到明确时长或比例，使用下方当前选择。')
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

    def update_cost(self):
        if not hasattr(self, 'cost'):
            return
        self.refresh_promo()  # Also catches the end date passing while the app stays open.
        try:
            resolution = self.resolution.get()
            value = estimate_cost(self.duration.get(), resolution)
            text = '按 PT %s 价格估算：约 $%.2f（$%.2f/秒' % (PRICE_CHECKED_DATE, value, current_prices()[resolution])
            regular = list_prices()
            if resolution in regular:
                text += '，原价 $%.2f/秒' % regular[resolution]
            self.cost.set(text + '）；实际扣费以平台账单为准。')
        except ValueError:
            self.cost.set('请填写整数时长（2–30 秒）')

    def open_official_keys(self):
        try:
            if not webbrowser.open(OFFICIAL_KEY_URL):
                messagebox.showinfo('PT 官网', OFFICIAL_KEY_URL)
        except OSError:
            messagebox.showinfo('PT 官网', OFFICIAL_KEY_URL)

    def choose_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.output.set(folder)

    def sync_keys(self):
        self.key_list.delete(0, 'end')
        for i, key in enumerate(self.keys):
            self.key_list.insert('end', '%d. ****%s' % (i + 1, key[-4:]))
        self.key_count.set('共 %d 个 Key' % len(self.keys))
        self.key_badge.set('已添加 %d 个 Key' % len(self.keys) if self.keys else '尚未添加 API Key')
        if self.keys:
            self.key_list.selection_set(0)

    def add_keys(self):
        try:
            keys = parse_keys(self.key_input.get())
        except ValueError as exc:
            messagebox.showerror('Key 格式', str(exc))
            return
        self.keys = list(dict.fromkeys(self.keys + keys))
        self.key_input.set('')
        self.status.set('已添加 Key，当前池中共 %d 个；可前往生成视频。' % len(self.keys))
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
            messagebox.showerror('保存失败', '无法保存或清除 Key 文件。当前 Key 仍可在本次运行使用；请检查数据目录权限。')

    def load_keys(self):
        path = DATA_DIR / 'keys.json'
        if path.exists():
            try:
                encrypted = base64.b64decode(json.loads(path.read_text(encoding='utf-8'))['dpapi'])
                self.keys = json.loads(protect(encrypted, decrypt=True))
                self.remember.set(True)
            except Exception:
                messagebox.showwarning('无法读取已保存 Key', '请在 API Key 页重新添加。加密 Key 只能由原 Windows 账户读取。')
        self.sync_keys()

    def selected_key(self):
        if not self.keys:
            raise TaskError('请先在 API Key 页添加 Key')
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
                raise TaskError('请先在 API Key 页添加 Key')
            recognized = self.apply_prompt()
            if recognized['warnings']:
                raise TaskError('；'.join(recognized['warnings']))
            request = payload(self.prompt.get('1.0', 'end'), self.duration.get(), self.resolution.get(),
                self.ratio.get(), [{'type': k, 'url': v.get().strip()} for k, v in self.media.items() if v.get().strip()], self.seed.get())
            output = self.new_output()
        except Exception as exc:
            messagebox.showerror('请检查输入', str(exc))
            return
        keys = list(self.keys)
        self.launch(lambda client: client.submit(keys, request, output))

    def resume_selected(self):
        if self.busy:
            return
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo('选择任务', '请先选中一个任务。')
            return
        record = self.records[selected[0]]
        try:
            if not record.get('task_id'):
                raise TaskError('该记录未获得任务 ID，请先查平台仪表盘；取得 ID 后可手动恢复。')
            key = next((k for k in self.keys if fingerprint(k) == record.get('key_hash')), None)
            if not key:
                raise TaskError('请先添加原任务使用的 Key：' + record.get('key_hint', ''))
            output = record['output']
            if Path(output).exists():
                output = self.new_output()
            self.launch(lambda client: client.resume(key, record['task_id'], output, record))
        except Exception as exc:
            messagebox.showerror('无法继续', str(exc))

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
            messagebox.showerror('请检查输入', str(exc))

    def resume_from_link(self):
        if self.busy:
            return
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo('选择任务', '请先选中需要下载的视频任务。')
            return
        try:
            record = self.records[selected[0]]
            key = next((k for k in self.keys if fingerprint(k) == record.get('key_hash')), None)
            if not key:
                raise TaskError('请先添加原任务使用的 Key：' + record.get('key_hint', ''))
            link = self.video_link.get().strip()
            if not link:
                raise TaskError('请粘贴视频的完整 HTTPS 下载链接。')
            output = record['output']
            if Path(output).exists():
                output = self.new_output()
            self.launch(lambda client: client.resume_download_url(key, record['task_id'], output, link, record))
        except Exception as exc:
            messagebox.showerror('无法从链接下载', str(exc))

    def launch(self, operation):
        self.busy = True
        self.stop.clear()
        for button in (self.generate_btn, self.resume_btn, self.manual_btn, self.link_btn):
            button.configure(state='disabled')
        self.stop_btn.configure(state='normal')
        self.batch.set_busy(True)
        self.bar.start(12)
        self.tabs.select(0)
        self.status.set('任务处理中…')
        def worker():
            client = Client(report=lambda text: self.events.put(('log', text)), stop=self.stop)
            try:
                operation(client)
                self.events.put(('done', '视频已下载完成'))
            except TaskError as exc:
                self.events.put(('done', str(exc)))
            except Exception:
                self.events.put(('done', '处理未完成，请检查网络和文件夹权限。已提交的任务请从记录继续查询，勿直接重复生成。'))
        threading.Thread(target=worker, daemon=True).start()

    def stop_wait(self):
        self.stop.set()
        self.status.set('正在停止等待，当前网络请求结束后生效；任务仍在云端继续，可在任务记录里找回。')

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
                if kind == 'done':
                    self.busy = False
                    self.batch.set_busy(False)
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
                self.tree.insert('', 'end', iid=path.stem, values=(row['created'], row.get('task_id') or '未获得 ID', row['state'], row.get('key_hint', '')))
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
            self.status.set('没有可复制的任务 ID；请先选中表格行。')
            return
        self.root.clipboard_clear()
        self.root.clipboard_append('\n'.join(task_ids))
        self.root.update_idletasks()
        self.status.set('已复制 %d 个任务 ID。' % len(task_ids))

    def open_folder(self):
        selected = self.tree.selection()
        folder = Path(self.records[selected[0]]['output']).parent if selected else Path(self.output.get())
        folder.mkdir(parents=True, exist_ok=True)
        if os.name == 'nt':
            os.startfile(str(folder))
        else:
            messagebox.showinfo('保存位置', str(folder))

    def close(self):
        if self.busy:
            self.stop_wait()
            messagebox.showinfo('正在停止', '请等待当前请求结束后再关闭。任务 ID 会保存在任务记录中；服务端任务仍可能继续。')
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

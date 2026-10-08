"""Shared Tk presentation components. No API or persistence logic lives here."""
from pathlib import Path
import sys
import tkinter as tk
from tkinter import ttk

from i18n import t
from wan_core import APP_VERSION, promo_active

APP_NAME = 'PowerTokens Video Studio'
VERSION = APP_VERSION


def subtitle():
    return t('subtitle')


def promotion_text(today=None):
    """Campaign copy (i18n key 'promo'); hidden automatically after wan_core.PROMO_END_DATE."""
    return t('promo') if promo_active(today) else ''


LOGO_FILE = 'assets/logo.png'
HEADER_LOGO_SIZES = (44, 55, 66, 88)  # Pre-rendered for 100/125/150/200% display scaling.
PAGE = '#f5f7fb'
WHITE = '#ffffff'
INK = '#1b293c'
MUTED = '#708194'
LINE = '#dfe7ee'
ACCENT = '#53b2a5'
PALE = '#eaf6f2'
TAB_PADDING = (20, 12)  # Same for every notebook tab state.
FONT = 'Microsoft YaHei UI' if sys.platform == 'win32' else 'PingFang SC'


def apply_theme(root):
    style = ttk.Style(root)
    style.theme_use('clam')
    scale = max(1., root.winfo_fpixels('1i') / 96.)
    root.configure(background=PAGE)
    style.configure('.', font=(FONT, 10), background=WHITE, foreground=INK)
    style.configure('TFrame', background=WHITE)
    style.configure('Page.TFrame', background=PAGE)
    style.configure('TLabel', background=WHITE, foreground=INK)
    style.configure('Muted.TLabel', foreground=MUTED)
    style.configure('Page.TLabel', background=PAGE, foreground=MUTED)
    style.configure('Brand.TLabel', font=(FONT, 21, 'bold'))
    style.configure('Heading.TLabel', background=PAGE, font=(FONT, 17, 'bold'))
    style.configure('CardTitle.TLabel', font=(FONT, 11, 'bold'))
    style.configure('Badge.TLabel', foreground='#328f80', background=PALE, padding=(12, 6))
    style.configure('TButton', background=WHITE, foreground='#4b6073',
                    bordercolor=LINE, lightcolor=WHITE, darkcolor=WHITE,
                    padding=(14, 8), relief='flat', focusthickness=1, focuscolor=ACCENT)
    style.map('TButton', background=[('disabled', '#f2f4f7'), ('pressed', '#deeee9'), ('active', '#edf7f4')],
              foreground=[('disabled', '#9aa8b6')], bordercolor=[('focus', ACCENT)])
    style.configure('Accent.TButton', background=ACCENT, foreground=WHITE,
                    bordercolor=ACCENT, padding=(18, 9))
    style.map('Accent.TButton', background=[('disabled', '#bddbd5'), ('pressed', '#348f83'), ('active', '#419f92')],
              foreground=[('disabled', '#f6faf9'), ('!disabled', WHITE)],
              bordercolor=[('!disabled', ACCENT)])
    style.configure('Link.TButton', foreground='#328f83', borderwidth=0, padding=(8, 6))
    # 中文 / English switch in the header: the active language is teal on light teal.
    style.configure('Lang.TButton', foreground=MUTED, background=WHITE, bordercolor=LINE,
                    lightcolor=WHITE, darkcolor=WHITE, padding=(10, 4))
    style.configure('LangActive.TButton', foreground='#268b7e', background=PALE, bordercolor=PALE,
                    lightcolor=PALE, darkcolor=PALE, padding=(10, 4))
    style.map('LangActive.TButton', background=[('active', PALE)], bordercolor=[('!disabled', PALE)])
    style.configure('TEntry', fieldbackground=WHITE, padding=(10, 7), bordercolor=LINE,
                    lightcolor=WHITE, darkcolor=WHITE, insertcolor=INK)
    style.configure('TCombobox', fieldbackground=WHITE, background=WHITE, padding=(9, 7),
                    bordercolor=LINE, arrowcolor=MUTED)
    style.map('TCombobox', fieldbackground=[('readonly', WHITE)], selectbackground=[('readonly', WHITE)],
              selectforeground=[('readonly', INK)])
    style.configure('TSpinbox', fieldbackground=WHITE, background=WHITE, padding=(9, 7),
                    bordercolor=LINE, arrowcolor=MUTED)
    for cls in ('TEntry', 'TCombobox', 'TSpinbox'):
        style.map(cls, bordercolor=[('focus', ACCENT)], lightcolor=[('focus', ACCENT)])
    style.configure('TCheckbutton', background=WHITE, padding=(0, 4))
    style.map('TCheckbutton', background=[('active', WHITE)], indicatorbackground=[('selected', ACCENT)])
    style.configure('TNotebook', background=PAGE, borderwidth=0, tabmargins=(0, 0, 0, 10))
    style.configure('TNotebook.Tab', padding=TAB_PADDING, font=(FONT, 10), background=WHITE,
                    foreground=MUTED, borderwidth=0, expand=(0, 0, 0, 0))
    # clam ships its own map of padding {6 4 6 2} for the selected tab, which beats the configured
    # padding and makes the active tab shrink and sit lower. Pin every state to the same geometry and
    # let colour alone mark the selected tab.
    style.map('TNotebook.Tab', padding=[('selected', TAB_PADDING)],
              expand=[('selected', (0, 0, 0, 0))],
              background=[('selected', PALE), ('active', '#f0f7f5')],
              foreground=[('selected', '#268b7e')])
    style.configure('Treeview', background=WHITE, fieldbackground=WHITE, foreground='#4f6275',
                    bordercolor=LINE, rowheight=int(38 * scale), borderwidth=0)
    style.configure('Treeview.Heading', background='#f3f6f9', foreground=MUTED,
                    padding=(10, 9), relief='flat', font=(FONT, 9))
    style.map('Treeview', background=[('selected', '#e3f3ed')], foreground=[('selected', '#236a5e')])
    style.map('Treeview.Heading', background=[('active', '#e9f2ef')])
    style.configure('Horizontal.TProgressbar', background=ACCENT, troughcolor='#e9eff3',
                    borderwidth=0, thickness=6)
    style.configure('TScrollbar', background='#d4e1e5', troughcolor=PAGE, borderwidth=0, arrowsize=12)
    return scale


def label(parent, text=None, variable=None, style='Muted.TLabel', **kwargs):
    """Wrap to the host width so long tips are not clipped at the card edge."""
    args = dict(style=style, justify='left', anchor='w', wraplength=280)
    args.update(kwargs)
    if text is not None:
        args['text'] = text
    if variable is not None:
        args['textvariable'] = variable
    item = ttk.Label(parent, **args)

    def sync_wrap(event=None, widget=item, host=parent):
        try:
            width = host.winfo_width()
            if width <= 1 and event is not None and getattr(event, 'widget', None) is widget:
                width = event.width
            # Ignore transient tiny widths during layout (they wrap one character per line).
            if width >= 160:
                widget.configure(wraplength=max(140, width - 8))
        except tk.TclError:
            pass

    item.bind('<Configure>', sync_wrap)
    parent.bind('<Configure>', sync_wrap, add='+')
    return item


def heading(parent, title, description):
    box = ttk.Frame(parent, style='Page.TFrame')
    box.pack(fill='x', pady=(4, 18))
    label(box, title, style='Heading.TLabel').pack(fill='x')
    label(box, description, style='Page.TLabel').pack(fill='x', pady=(6, 0))


class Card(tk.Canvas):
    """A rounded visual surface with a normal, keyboard-accessible ttk body."""
    def __init__(self, parent, title=None, description=None):
        super().__init__(parent, background=PAGE, highlightthickness=0, borderwidth=0, width=1, height=100)
        self.pad = 20
        self.body = ttk.Frame(self)
        self.window = self.create_window(self.pad, self.pad, window=self.body, anchor='nw')
        self.bind('<Configure>', self.resize)
        self.body.bind('<Configure>', self.fit_height)
        self.after_idle(self.fit_height)
        if title:
            label(self.body, title, style='CardTitle.TLabel').pack(fill='x', pady=(0, 8))
        if description:
            label(self.body, description).pack(fill='x', pady=(0, 14))

    def fit_height(self, event=None):
        try:
            height = self.body.winfo_reqheight() + 2 * self.pad
            if self.winfo_pixels(self.cget('height')) != height:
                self.configure(height=height)
        except tk.TclError:  # Destroyed while a callback was pending (e.g. language switch).
            pass

    def resize(self, event):
        self.itemconfigure(self.window, width=max(1, event.width - 2 * self.pad))
        w, h, r = event.width - 1, event.height - 1, 14
        self.delete('surface')
        self.create_polygon(r, 0, w-r, 0, w, 0, w, r, w, h-r, w, h, w-r, h,
                            r, h, 0, h, 0, h-r, 0, r, 0, 0,
                            smooth=True, fill=WHITE, outline=LINE, tags='surface')
        self.tag_lower('surface')


class Columns(ttk.Frame):
    """Stack cards on smaller windows / higher display scaling."""
    def __init__(self, parent, scale=1, weights=(2, 1)):
        super().__init__(parent, style='Page.TFrame')
        self.left = ttk.Frame(self, style='Page.TFrame')
        self.right = ttk.Frame(self, style='Page.TFrame')
        self.breakpoint = 1000 * scale
        self.weights = weights
        self.wide = None
        self.bind('<Configure>', self.reflow)

    def reflow(self, event):
        wide = event.width >= self.breakpoint
        if wide == self.wide:
            return
        self.wide = wide
        self.left.grid_forget()
        self.right.grid_forget()
        self.columnconfigure(0, weight=self.weights[0] if wide else 1, uniform='columns' if wide else '')
        self.columnconfigure(1, weight=self.weights[1] if wide else 0, uniform='columns' if wide else '')
        self.left.grid(row=0, column=0, sticky='new', padx=(0, 9 if wide else 0))
        self.right.grid(row=0 if wide else 1, column=1 if wide else 0,
                        sticky='new', padx=(9 if wide else 0, 0), pady=(0 if wide else 16, 0))


class Flow(ttk.Frame):
    """Keep all actions reachable when a window is narrowed."""
    def __init__(self, parent):
        super().__init__(parent)
        self.pending = None
        self.bind('<Configure>', self.schedule)

    def schedule(self, event=None):
        if self.pending is None:
            self.pending = self.after_idle(self.reflow)

    def reflow(self):
        self.pending = None
        try:
            width = self.winfo_width()
        except tk.TclError:  # Destroyed while a callback was pending (e.g. language switch).
            return
        used, row, col = 0, 0, 0
        for child in self.winfo_children():
            needed = child.winfo_reqwidth() + 8
            if used and used + needed > width:
                row, col, used = row + 1, 0, 0
            child.grid(row=row, column=col, sticky='w', padx=(0, 8), pady=4)
            used, col = used + needed, col + 1


class ScrollPage(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, style='Page.TFrame')
        self.canvas = tk.Canvas(self, background=PAGE, highlightthickness=0, width=1)
        bar = ttk.Scrollbar(self, command=self.canvas.yview)
        bar.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.canvas.configure(yscrollcommand=bar.set)
        self.body = ttk.Frame(self.canvas, padding=(24, 10, 24, 24), style='Page.TFrame')
        window = self.canvas.create_window(0, 0, window=self.body, anchor='nw')
        self.body.bind('<Configure>', lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfigure(window, width=e.width))
        for event in ('<MouseWheel>', '<Button-4>', '<Button-5>'):
            self.bind_all(event, self.wheel, add='+')

    def wheel(self, event):
        try:
            target = self.winfo_containing(event.x_root, event.y_root)
            if target is None or not str(target).startswith(str(self) + '.'):
                return
            # These widgets have their own native scrolling behavior.
            if target.winfo_class() in ('Text', 'Treeview', 'Listbox', 'TCombobox'):
                return
            if self.canvas.yview() == (0., 1.):
                return
            if getattr(event, 'num', None) in (4, 5):
                amount = -1 if event.num == 4 else 1
            else:
                delta = event.delta
                amount = -int(delta / 120) if abs(delta) >= 120 else (-1 if delta > 0 else 1)
            self.canvas.yview_scroll(amount, 'units')
        except tk.TclError:
            return


def text_area(parent, height=5, **kwargs):
    return tk.Text(parent, height=height, width=1, wrap='word', font=(FONT, 10),
                   background=WHITE, foreground='#354b5f', relief='flat',
                   highlightthickness=1, highlightbackground=LINE, highlightcolor=ACCENT,
                   insertbackground=INK, selectbackground='#d7eee7', selectforeground=INK,
                   padx=12, pady=10, **kwargs)


def card(parent, title, description=None):
    panel = Card(parent, title, description)
    panel.pack(fill='x', pady=(0, 16))
    return panel.body


def resource_path(relative):
    """Locate bundled files both from source and inside a PyInstaller build (sys._MEIPASS)."""
    base = getattr(sys, '_MEIPASS', None) or Path(__file__).resolve().parent
    return Path(base) / relative


def load_image(relative):
    """Return a PhotoImage, or None if the file is missing or unreadable. Never raises."""
    try:
        path = resource_path(relative)
        if not path.is_file():
            return None
        return tk.PhotoImage(file=str(path))
    except (tk.TclError, OSError, RuntimeError):
        return None


def header_logo_file(scale=1.0):
    size = min(HEADER_LOGO_SIZES, key=lambda value: abs(value - 44 * scale))
    return 'assets/logo-%d.png' % size


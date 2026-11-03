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
# Key traffic-light (match web .key-status-dot-* / .key-status-pill)
KEY_DOT_BAD = '#ff3b30'
KEY_DOT_WARN = '#f59e0b'
KEY_DOT_OK = '#22c55e'
KEY_PILL_SOFT_BG = '#f7fcfa'
KEY_PILL_SOFT_BORDER = '#c9e4dc'
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
    style.configure('Brand.TLabel', font=(FONT, 18, 'bold'))
    style.configure('Heading.TLabel', background=PAGE, font=(FONT, 17, 'bold'))
    style.configure('CardTitle.TLabel', font=(FONT, 11, 'bold'))
    style.configure('Badge.TLabel', foreground='#328f80', background=PALE, padding=(12, 6))
    # Official-console-like chips: pill name + credits (match web .user-chip / .credits-chip).
    style.configure('UserChip.TLabel', foreground='#2f4558', background='#f3f7f6', padding=(12, 6))
    style.configure('CreditsChip.TLabel', foreground='#0e8f85', background='#eafaf7', padding=(12, 6))
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
    """Stack cards on smaller windows / higher display scaling.

    Generate uses weights=(3, 2) so the right rail is not optically skinny.
    """
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



class KeyStatusPill(tk.Canvas):
    """Web-matching API Key traffic light: dim red/yellow/green dots, one lit + label.

    States mirror video-studio-web `.key-status-pill`:
      soft  — no key yet (sign-in cue; accent soft)
      ok    — key usable (green lit)
      warn  — verifying / needs check / quota (yellow lit)
      bad   — invalid or expired (red lit)
    """
    _PALETTE = {
        'soft': dict(bg='#f7fcfa', border='#c9e4dc', fg='#268b7e', lit='warn', lit_color=ACCENT),
        'ok': dict(bg='#ecfdf3', border='#86efac', fg='#15803d', lit='ok', lit_color=KEY_DOT_OK),
        'warn': dict(bg='#fff7ed', border='#fcd34d', fg='#b45309', lit='warn', lit_color=KEY_DOT_WARN),
        'bad': dict(bg='#ffe8e6', border='#ffc9c4', fg='#d70015', lit='bad', lit_color=KEY_DOT_BAD),
    }
    _DIM = {'bad': '#ffd0cc', 'warn': '#fde7b8', 'ok': '#c6f0d4'}
    _ORDER = ('bad', 'warn', 'ok')

    def __init__(self, parent, textvariable=None, chrome=None, **kwargs):
        self._chrome = chrome if chrome is not None else PAGE
        super().__init__(parent, height=26, highlightthickness=0, borderwidth=0,
                         background=self._chrome, **kwargs)
        self._state = 'soft'
        self._textvariable = textvariable
        self._text = ''
        self._tooltip = ''
        if textvariable is not None:
            self._text = str(textvariable.get() or '')
            textvariable.trace_add('write', lambda *_: self._on_var())
        self.bind('<Configure>', lambda e: self._redraw())
        self.after_idle(self._redraw)

    def _on_var(self):
        if self._textvariable is not None:
            self._text = str(self._textvariable.get() or '')
        self._redraw()

    def set_text(self, text):
        self._text = str(text or '')
        if self._textvariable is not None:
            if self._textvariable.get() != self._text:
                self._textvariable.set(self._text)
        self._redraw()

    def set_state(self, state, text=None, tooltip=None):
        if state not in self._PALETTE:
            state = 'soft'
        self._state = state
        if text is not None:
            self.set_text(text)
        if tooltip is not None:
            self._tooltip = tooltip
        self._redraw()

    def current_state(self):
        return self._state

    def _redraw(self, event=None):
        try:
            palette = self._PALETTE[self._state]
            text = self._text or ''
            # Measure label
            font = (FONT, 9, 'bold')
            self.delete('all')
            # Temp text to measure
            tid = self.create_text(0, 0, text=text, font=font, anchor='nw')
            bbox = self.bbox(tid) or (0, 0, 40, 12)
            self.delete(tid)
            tw = max(36, bbox[2] - bbox[0])
            th = max(12, bbox[3] - bbox[1])
            pad_x, gap, r = 8, 4, 4
            dots_w = 3 * (2 * r) + 2 * gap
            total_w = pad_x + dots_w + 8 + tw + pad_x
            total_h = max(24, th + 10)
            self.configure(width=total_w, height=total_h, background=self._chrome)
            # Full-radius stadium (match web .key-status-pill border-radius: 999px).
            _draw_stadium(self, total_w, total_h, fill=palette['bg'], outline=palette['border'], border=1)
            # Dots
            cx0 = pad_x + r
            cy = total_h // 2
            colors = {'bad': KEY_DOT_BAD, 'warn': KEY_DOT_WARN, 'ok': KEY_DOT_OK}
            lit = palette['lit']
            for i, name in enumerate(self._ORDER):
                cx = cx0 + i * (2 * r + gap)
                if name == lit:
                    self.create_oval(cx - r - 2, cy - r - 2, cx + r + 2, cy + r + 2,
                                     fill='', outline=palette['lit_color'], width=2)
                    self.create_oval(cx - r, cy - r, cx + r, cy + r,
                                     fill=palette['lit_color'], outline=palette['lit_color'])
                else:
                    self.create_oval(cx - r, cy - r, cx + r, cy + r,
                                     fill=self._DIM[name], outline='#e5ebe8')
            # Label
            lx = pad_x + dots_w + 8
            self.create_text(lx, cy, text=text, font=font, fill=palette['fg'], anchor='w')
        except tk.TclError:
            pass



def _draw_stadium(canvas, w, h, fill, outline, border=1):
    """True full-radius pill (semicircle ends) — polygon+smooth looks chamfered."""
    # Keep 1px inset so the outline stays inside the canvas bitmap.
    x0, y0, x1, y1 = 0, 0, max(2, w - 1), max(2, h - 1)
    r = max(1, (y1 - y0) // 2)
    # End caps
    canvas.create_oval(x0, y0, x0 + 2 * r, y1, fill=fill, outline=outline, width=border)
    canvas.create_oval(x1 - 2 * r, y0, x1, y1, fill=fill, outline=outline, width=border)
    # Body (covers seam between caps); stroke only top/bottom.
    canvas.create_rectangle(x0 + r, y0, x1 - r, y1, fill=fill, outline='')
    canvas.create_line(x0 + r, y0, x1 - r, y0, fill=outline, width=border)
    canvas.create_line(x0 + r, y1, x1 - r, y1, fill=outline, width=border)


class PillChip(tk.Canvas):
    """Web-matching header chip: full-radius .user-chip / .credits-chip.

    Variants:
      credits — teal top-up / balance pill (clickable)
      user    — name chip
    """
    _VARIANTS = {
        'credits': dict(bg='#eafaf7', border='#7dd3c8', fg='#0e8f85',
                        active_bg='#e2f7f3', active_border='#5bc4b6'),
        'user': dict(bg='#f3f7f6', border='#d5e4df', fg='#2f4558',
                     active_bg='#eef5f2', active_border='#c5d8d1'),
    }

    def __init__(self, parent, text='', textvariable=None, variant='credits',
                 command=None, chrome=None, **kwargs):
        self._chrome = chrome if chrome is not None else PAGE
        super().__init__(parent, height=28, highlightthickness=0, borderwidth=0,
                         background=self._chrome, cursor='hand2' if command else '', **kwargs)
        self._variant = variant if variant in self._VARIANTS else 'credits'
        self._textvariable = textvariable
        self._text = str(textvariable.get() if textvariable is not None else text or '')
        self._command = command
        self._pressed = False
        self._enabled = True
        if textvariable is not None:
            textvariable.trace_add('write', lambda *_: self._on_var())
        self.bind('<Configure>', lambda e: self._redraw())
        self.bind('<ButtonPress-1>', self._on_press)
        self.bind('<ButtonRelease-1>', self._on_release)
        self.bind('<Enter>', lambda e: self._redraw())
        self.bind('<Leave>', lambda e: self._leave())
        self.after_idle(self._redraw)

    def _on_var(self):
        if self._textvariable is not None:
            self._text = str(self._textvariable.get() or '')
        self._redraw()

    def set_text(self, text):
        self._text = str(text or '')
        if self._textvariable is not None and self._textvariable.get() != self._text:
            self._textvariable.set(self._text)
        self._redraw()

    def configure(self, cnf=None, **kwargs):
        if cnf and isinstance(cnf, dict):
            kwargs = {**cnf, **kwargs}
        if 'text' in kwargs:
            self.set_text(kwargs.pop('text'))
        if 'command' in kwargs:
            self._command = kwargs.pop('command')
            kwargs['cursor'] = 'hand2' if self._command else ''
        if kwargs:
            return super().configure(**kwargs)

    config = configure

    def _leave(self):
        self._pressed = False
        self._redraw()

    def _on_press(self, _event=None):
        if not self._enabled or not self._command:
            return
        self._pressed = True
        self._redraw()

    def _on_release(self, _event=None):
        was = self._pressed
        self._pressed = False
        self._redraw()
        if was and self._enabled and self._command:
            self._command()

    def _redraw(self, event=None):
        try:
            palette = self._VARIANTS[self._variant]
            text = self._text or ''
            font = (FONT, 9, 'bold')
            self.delete('all')
            tid = self.create_text(0, 0, text=text, font=font, anchor='nw')
            bbox = self.bbox(tid) or (0, 0, 48, 12)
            self.delete(tid)
            tw = max(28, bbox[2] - bbox[0])
            th = max(12, bbox[3] - bbox[1])
            pad_x, pad_y = 12, 5
            total_w = pad_x * 2 + tw
            total_h = max(26, th + pad_y * 2)
            super().configure(width=total_w, height=total_h, background=self._chrome)
            w, h = total_w - 1, total_h - 1
            bg = palette['active_bg'] if self._pressed else palette['bg']
            border = palette['active_border'] if self._pressed else palette['border']
            _draw_stadium(self, total_w, total_h, fill=bg, outline=border, border=1)
            self.create_text(total_w // 2, total_h // 2, text=text, font=font,
                             fill=palette['fg'], anchor='center')
        except tk.TclError:
            pass


class AccentPillButton(tk.Canvas):
    """Full-radius accent CTA matching web header Sign-in (btn-accent + stadium ends)."""

    def __init__(self, parent, text='', command=None, chrome=None, **kwargs):
        self._chrome = chrome if chrome is not None else PAGE
        super().__init__(parent, height=30, highlightthickness=0, borderwidth=0,
                         background=self._chrome, cursor='hand2', **kwargs)
        self._text = str(text or '')
        self._command = command
        self._state = 'normal'
        self._pressed = False
        self._hover = False
        self.bind('<Configure>', lambda e: self._redraw())
        self.bind('<ButtonPress-1>', self._on_press)
        self.bind('<ButtonRelease-1>', self._on_release)
        self.bind('<Enter>', self._on_enter)
        self.bind('<Leave>', self._on_leave)
        self.after_idle(self._redraw)

    def configure(self, cnf=None, **kwargs):
        if cnf and isinstance(cnf, dict):
            kwargs = {**cnf, **kwargs}
        redraw = False
        if 'text' in kwargs:
            self._text = str(kwargs.pop('text') or '')
            redraw = True
        if 'command' in kwargs:
            self._command = kwargs.pop('command')
        if 'state' in kwargs:
            state = kwargs.pop('state')
            self._state = 'disabled' if str(state) in ('disabled', '0') else 'normal'
            kwargs['cursor'] = '' if self._state == 'disabled' else 'hand2'
            redraw = True
        if kwargs:
            super().configure(**kwargs)
        if redraw:
            self._redraw()

    config = configure

    def cget(self, key):
        if key == 'text':
            return self._text
        if key == 'state':
            return self._state
        return super().cget(key)

    def _on_enter(self, _event=None):
        self._hover = True
        self._redraw()

    def _on_leave(self, _event=None):
        self._hover = False
        self._pressed = False
        self._redraw()

    def _on_press(self, _event=None):
        if self._state == 'disabled':
            return
        self._pressed = True
        self._redraw()

    def _on_release(self, _event=None):
        was = self._pressed
        self._pressed = False
        self._redraw()
        if was and self._state != 'disabled' and self._command:
            self._command()

    def _redraw(self, event=None):
        try:
            text = self._text or ''
            font = (FONT, 9, 'bold')
            self.delete('all')
            tid = self.create_text(0, 0, text=text, font=font, anchor='nw')
            bbox = self.bbox(tid) or (0, 0, 80, 12)
            self.delete(tid)
            tw = max(40, bbox[2] - bbox[0])
            th = max(12, bbox[3] - bbox[1])
            pad_x, pad_y = 14, 6
            total_w = pad_x * 2 + tw
            total_h = max(28, th + pad_y * 2)
            super().configure(width=total_w, height=total_h, background=self._chrome)
            w, h = total_w - 1, total_h - 1
            if self._state == 'disabled':
                bg, border, fg = '#bddbd5', '#bddbd5', '#f6faf9'
            elif self._pressed:
                bg, border, fg = '#348f83', '#348f83', WHITE
            elif self._hover:
                bg, border, fg = '#419f92', '#419f92', WHITE
            else:
                bg, border, fg = ACCENT, ACCENT, WHITE
            _draw_stadium(self, total_w, total_h, fill=bg, outline=border, border=1)
            self.create_text(total_w // 2, total_h // 2, text=text, font=font, fill=fg, anchor='center')
        except tk.TclError:
            pass



class LangPill(tk.Canvas):
    """Web-matching language select: stadium outline + caret, opens a tk Menu."""

    def __init__(self, parent, text='', command=None, chrome=None, values=None, **kwargs):
        self._chrome = chrome if chrome is not None else PAGE
        super().__init__(parent, height=28, highlightthickness=0, borderwidth=0,
                         background=self._chrome, cursor='hand2', **kwargs)
        self._text = str(text or '')
        self._command = command  # called with selected label
        self._values = list(values or [])
        self._hover = False
        self._menu = tk.Menu(self, tearoff=0)
        self.bind('<Configure>', lambda e: self._redraw())
        self.bind('<Button-1>', self._open_menu)
        self.bind('<Enter>', lambda e: self._set_hover(True))
        self.bind('<Leave>', lambda e: self._set_hover(False))
        self.after_idle(self._rebuild_menu)
        self.after_idle(self._redraw)

    def set_values(self, values):
        self._values = list(values or [])
        self._rebuild_menu()

    def set_text(self, text):
        self._text = str(text or '')
        self._redraw()

    def configure(self, cnf=None, **kwargs):
        if cnf and isinstance(cnf, dict):
            kwargs = {**cnf, **kwargs}
        redraw = False
        if 'text' in kwargs:
            self._text = str(kwargs.pop('text') or '')
            redraw = True
        if 'values' in kwargs:
            self.set_values(kwargs.pop('values'))
        if kwargs:
            super().configure(**kwargs)
        if redraw:
            self._redraw()

    config = configure

    def _set_hover(self, on):
        self._hover = on
        self._redraw()

    def _rebuild_menu(self):
        self._menu.delete(0, 'end')
        for label in self._values:
            self._menu.add_command(label=label, command=lambda v=label: self._pick(v))

    def _pick(self, label):
        self.set_text(label)
        if self._command:
            self._command(label)

    def _open_menu(self, event=None):
        try:
            self._menu.tk_popup(self.winfo_rootx(), self.winfo_rooty() + self.winfo_height())
        finally:
            self._menu.grab_release()

    def _redraw(self, event=None):
        try:
            text = self._text or ''
            font = (FONT, 9)
            self.delete('all')
            tid = self.create_text(0, 0, text=text, font=font, anchor='nw')
            bbox = self.bbox(tid) or (0, 0, 40, 12)
            self.delete(tid)
            tw = max(36, bbox[2] - bbox[0])
            th = max(12, bbox[3] - bbox[1])
            pad_x, caret_w = 10, 12
            total_w = pad_x + tw + 6 + caret_w + pad_x
            total_h = max(28, th + 10)
            super().configure(width=total_w, height=total_h, background=self._chrome)
            border = '#b9ddd4' if self._hover else LINE
            _draw_stadium(self, total_w, total_h, fill=WHITE, outline=border, border=1)
            cy = total_h // 2
            self.create_text(pad_x, cy, text=text, font=font, fill='#4b6073', anchor='w')
            # small caret
            cx = total_w - pad_x - 4
            self.create_polygon(cx - 4, cy - 2, cx + 4, cy - 2, cx, cy + 3,
                                fill=MUTED, outline=MUTED)
        except tk.TclError:
            pass


def _draw_round_rect(canvas, w, h, radius=8, fill=WHITE, outline=LINE, border=1):
    """Web-like 8px rounded field (`.cselect-trigger`), not a full stadium pill."""
    x0, y0, x1, y1 = 0, 0, max(2, w - 1), max(2, h - 1)
    r = max(1, min(int(radius), (x1 - x0) // 2, (y1 - y0) // 2))
    # Corner caps
    canvas.create_oval(x0, y0, x0 + 2 * r, y0 + 2 * r, fill=fill, outline=outline, width=border)
    canvas.create_oval(x1 - 2 * r, y0, x1, y0 + 2 * r, fill=fill, outline=outline, width=border)
    canvas.create_oval(x0, y1 - 2 * r, x0 + 2 * r, y1, fill=fill, outline=outline, width=border)
    canvas.create_oval(x1 - 2 * r, y1 - 2 * r, x1, y1, fill=fill, outline=outline, width=border)
    # Body fills (cover oval seams)
    canvas.create_rectangle(x0 + r, y0, x1 - r, y1, fill=fill, outline='')
    canvas.create_rectangle(x0, y0 + r, x1, y1 - r, fill=fill, outline='')
    # Outer strokes on straight edges
    canvas.create_line(x0 + r, y0, x1 - r, y0, fill=outline, width=border)
    canvas.create_line(x0 + r, y1, x1 - r, y1, fill=outline, width=border)
    canvas.create_line(x0, y0 + r, x0, y1 - r, fill=outline, width=border)
    canvas.create_line(x1, y0 + r, x1, y1 - r, fill=outline, width=border)


class WebSelect(ttk.Frame):
    """Web `.cselect` stand-in: 8px rounded white field + teal caret + popup menu.

    ttk.Combobox cannot draw a custom chevron or true border-radius; this Canvas
    trigger + Toplevel menu matches video-studio-web as closely as Tk allows.
    Drop-in enough for model pickers: textvariable, values, state, <<ComboboxSelected>>.
    """
    _HEIGHT = 40
    _RADIUS = 8
    _ACCENT_TEXT = '#268b7e'
    _HOVER_BORDER = '#b7d9d1'
    _HOVER_BG = '#fbfdfe'
    _OPEN_BG = '#f7fcfa'
    _MENU_BORDER = '#c5ddd6'
    _PALE = PALE

    def __init__(self, parent, textvariable=None, values=(), state='readonly',
                 command=None, width=None, **kwargs):
        super().__init__(parent, **kwargs)
        self._textvariable = textvariable if textvariable is not None else tk.StringVar()
        self._owns_var = textvariable is None
        self._values = [str(v) for v in (values or ())]
        self._state = 'disabled' if str(state) == 'disabled' else 'readonly'
        self._command = command
        self._listeners = []
        self._hover = False
        self._open = False
        self._popup = None
        self._chrome = WHITE
        self.canvas = tk.Canvas(self, height=self._HEIGHT, highlightthickness=0,
                                borderwidth=0, background=WHITE, cursor='hand2')
        self.canvas.pack(fill='x', expand=True)
        self.bind('<Configure>', self._on_resize)
        self.canvas.bind('<Button-1>', self._toggle)
        self.canvas.bind('<Enter>', lambda e: self._set_hover(True))
        self.canvas.bind('<Leave>', lambda e: self._set_hover(False))
        self.canvas.bind('<FocusIn>', lambda e: self._redraw())
        self.canvas.bind('<FocusOut>', lambda e: self._redraw())
        self._var_trace = self._textvariable.trace_add('write', lambda *_: self._redraw())
        self.after_idle(self._redraw)

    def configure(self, cnf=None, **kwargs):
        if cnf and isinstance(cnf, dict):
            kwargs = {**cnf, **kwargs}
        redraw = False
        if 'values' in kwargs:
            self._values = [str(v) for v in (kwargs.pop('values') or ())]
            redraw = True
        if 'state' in kwargs:
            state = kwargs.pop('state')
            self._state = 'disabled' if str(state) == 'disabled' else 'readonly'
            self.canvas.configure(cursor='' if self._state == 'disabled' else 'hand2')
            redraw = True
        if 'textvariable' in kwargs:
            if self._var_trace and self._textvariable is not None:
                try:
                    self._textvariable.trace_remove('write', self._var_trace)
                except tk.TclError:
                    pass
            self._textvariable = kwargs.pop('textvariable')
            self._var_trace = self._textvariable.trace_add('write', lambda *_: self._redraw())
            redraw = True
        if 'command' in kwargs:
            self._command = kwargs.pop('command')
        if kwargs:
            super().configure(**kwargs)
        if redraw:
            self._redraw()

    config = configure

    def cget(self, key):
        if key == 'values':
            return tuple(self._values)
        if key == 'state':
            return self._state
        if key == 'textvariable':
            return self._textvariable
        return super().cget(key)

    def bind(self, sequence=None, func=None, add=None):
        if sequence == '<<ComboboxSelected>>' and func is not None:
            self._listeners.append(func)
            return 'webselect-selected'
        return super().bind(sequence, func, add)

    def current(self, newindex=None):
        value = self._textvariable.get()
        if newindex is None:
            try:
                return self._values.index(value)
            except ValueError:
                return -1
        if 0 <= int(newindex) < len(self._values):
            self._pick(self._values[int(newindex)])

    def get(self):
        return self._textvariable.get()

    def set(self, value):
        self._textvariable.set(str(value or ''))
        self._redraw()

    def _set_hover(self, on):
        if self._state == 'disabled':
            return
        self._hover = bool(on)
        self._redraw()

    def _on_resize(self, event):
        if event.widget is self and event.width > 1:
            self.canvas.configure(width=max(40, event.width))
            self._redraw()

    def _toggle(self, _event=None):
        if self._state == 'disabled':
            return
        if self._open:
            self._close_popup()
        else:
            self._open_popup()

    def _pick(self, label):
        self._textvariable.set(label)
        self._close_popup()
        self._redraw()
        if self._command:
            try:
                self._command(label)
            except TypeError:
                self._command()
        for func in list(self._listeners):
            try:
                func()
            except TypeError:
                func(None)

    def _close_popup(self):
        self._open = False
        popup = self._popup
        self._popup = None
        if popup is not None:
            try:
                popup.grab_release()
            except tk.TclError:
                pass
            try:
                popup.destroy()
            except tk.TclError:
                pass
        self._redraw()

    def _open_popup(self):
        self._close_popup()
        if not self._values:
            return
        self._open = True
        self._redraw()
        popup = tk.Toplevel(self)
        popup.withdraw()
        popup.overrideredirect(True)
        popup.configure(background=self._MENU_BORDER)
        try:
            popup.attributes('-topmost', True)
        except tk.TclError:
            pass
        shell = tk.Frame(popup, background=WHITE, highlightthickness=1,
                         highlightbackground=self._MENU_BORDER, bd=0)
        shell.pack(fill='both', expand=True, padx=1, pady=1)
        canvas = tk.Canvas(shell, background=WHITE, highlightthickness=0, borderwidth=0)
        scroll = ttk.Scrollbar(shell, orient='vertical', command=canvas.yview)
        body = tk.Frame(canvas, background=WHITE)
        window = canvas.create_window(0, 0, window=body, anchor='nw')
        canvas.configure(yscrollcommand=scroll.set)

        def sync_scroll(_event=None):
            canvas.configure(scrollregion=canvas.bbox('all'))
            canvas.itemconfigure(window, width=max(1, canvas.winfo_width()))

        body.bind('<Configure>', sync_scroll)
        canvas.bind('<Configure>', sync_scroll)

        selected = self._textvariable.get()
        for label in self._values:
            row = tk.Frame(body, background=self._PALE if label == selected else WHITE)
            row.pack(fill='x', padx=6, pady=2)
            fg = self._ACCENT_TEXT if label == selected else INK
            font = (FONT, 10, 'bold') if label == selected else (FONT, 10)
            lab = tk.Label(row, text=label, anchor='w', justify='left',
                           background=row.cget('background'), foreground=fg,
                           font=font, padx=10, pady=8)
            lab.pack(fill='x')

            def enter(event, r=row, l=lab, sel=(label == selected)):
                if not sel:
                    r.configure(background='#edf7f4')
                    l.configure(background='#edf7f4', foreground=self._ACCENT_TEXT)

            def leave(event, r=row, l=lab, sel=(label == selected)):
                bg = self._PALE if sel else WHITE
                fg = self._ACCENT_TEXT if sel else INK
                r.configure(background=bg)
                l.configure(background=bg, foreground=fg)

            def click(_event=None, value=label):
                self._pick(value)

            for widget in (row, lab):
                widget.bind('<Enter>', enter)
                widget.bind('<Leave>', leave)
                widget.bind('<Button-1>', click)

        canvas.pack(side='left', fill='both', expand=True)
        self.update_idletasks()
        body.update_idletasks()
        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_height() + 4
        width = max(self.winfo_width(), 160)
        need = max(body.winfo_reqheight() + 8, 48)
        max_h = 280
        scrollable = need > max_h
        height = max_h if scrollable else need
        if scrollable:
            scroll.pack(side='right', fill='y')
        popup.geometry('%dx%d+%d+%d' % (width, height, x, y))
        canvas.configure(width=max(40, width - (18 if scrollable else 2)), height=max(40, height - 2))
        popup.deiconify()
        popup.lift()
        try:
            popup.grab_set()
        except tk.TclError:
            pass
        popup.bind('<Escape>', lambda e: self._close_popup())
        # With grab, presses outside land on the popup; close unless on trigger.
        popup.bind('<ButtonPress-1>', self._grab_press, add='+')
        self._popup = popup

    def _grab_press(self, event):
        if not self._open or self._popup is None:
            return
        px = self._popup.winfo_rootx()
        py = self._popup.winfo_rooty()
        pw = self._popup.winfo_width()
        ph = self._popup.winfo_height()
        inside_menu = px <= event.x_root <= px + pw and py <= event.y_root <= py + ph
        if inside_menu:
            return
        tx, ty = self.winfo_rootx(), self.winfo_rooty()
        tw, th = self.winfo_width(), self.winfo_height()
        on_trigger = tx <= event.x_root <= tx + tw and ty <= event.y_root <= ty + th
        self._close_popup()
        if on_trigger:
            # swallow re-open from the same click
            return 'break'

    def _redraw(self, event=None):
        try:
            canvas = self.canvas
            width = max(40, int(canvas.winfo_width() or self.winfo_width() or 200))
            height = self._HEIGHT
            canvas.configure(width=width, height=height, background=WHITE)
            canvas.delete('all')
            text = str(self._textvariable.get() or '')
            disabled = self._state == 'disabled'
            if disabled:
                bg, border, fg, caret = '#f3faf7', '#d5ebe5', '#7a9099', '#7a9099'
            elif self._open:
                bg, border, fg, caret = self._OPEN_BG, ACCENT, INK, ACCENT
            elif self._hover:
                bg, border, fg, caret = self._HOVER_BG, self._HOVER_BORDER, INK, self._ACCENT_TEXT
            else:
                bg, border, fg, caret = WHITE, LINE, INK, self._ACCENT_TEXT
            _draw_round_rect(canvas, width, height, radius=self._RADIUS,
                             fill=bg, outline=border, border=1)
            # Soft top hairline like web box-shadow: 0 1px 0 rgba(...)
            pad_x, caret_box = 12, 22
            font = (FONT, 10)
            # Measure + ellipsize
            max_text_w = max(20, width - pad_x - caret_box - 8)
            display = text
            tid = canvas.create_text(0, 0, text=display, font=font, anchor='nw')
            bbox = canvas.bbox(tid) or (0, 0, 10, 12)
            canvas.delete(tid)
            while display and (bbox[2] - bbox[0]) > max_text_w:
                display = display[:-1]
                tid = canvas.create_text(0, 0, text=display + '…', font=font, anchor='nw')
                bbox = canvas.bbox(tid) or (0, 0, 10, 12)
                canvas.delete(tid)
            if display != text and display:
                display = display + '…'
            cy = height // 2
            canvas.create_text(pad_x, cy, text=display, font=font, fill=fg, anchor='w')
            # Teal chevron (web .cselect-caret): two strokes forming >
            cx = width - 14
            if self._open:
                # point up
                canvas.create_line(cx - 5, cy + 2, cx, cy - 3, fill=caret, width=2, capstyle='round')
                canvas.create_line(cx, cy - 3, cx + 5, cy + 2, fill=caret, width=2, capstyle='round')
            else:
                canvas.create_line(cx - 5, cy - 2, cx, cy + 3, fill=caret, width=2, capstyle='round')
                canvas.create_line(cx, cy + 3, cx + 5, cy - 2, fill=caret, width=2, capstyle='round')
        except tk.TclError:
            pass

    def destroy(self):
        self._close_popup()
        if self._var_trace and self._textvariable is not None:
            try:
                self._textvariable.trace_remove('write', self._var_trace)
            except tk.TclError:
                pass
        super().destroy()




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


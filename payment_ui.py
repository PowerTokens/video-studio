"""Non-modal checkout; order survives closing the picker for this app session."""
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk
import webbrowser
import payment
import oauth_desktop
from payment_copy import COPY, RECONFIRM
from i18n import get_language
from studio_ui import PAGE, WHITE, INK, MUTED, LINE, ACCENT, PALE


# UI identities are distinct even when cards share an official backend package.
DEFAULT_SELECTION = 'fixed_5'
CHECKOUT_CARDS = (
    dict(key='fixed_5', package_id=35, amount=5, credits=5000, recommended=True),
    dict(key='fixed_20', package_id=22, amount=20, credits=20000, recommended=False),
    dict(key='fixed_50', package_id=23, amount=50, credits=50000, recommended=False),
    dict(key='custom', package_id=33, amount=None, credits=None, recommended=False),
)


def checkout_card(selection=DEFAULT_SELECTION):
    return next(card for card in CHECKOUT_CARDS if card['key'] == selection)


def custom_input_visible(selection=DEFAULT_SELECTION):
    return checkout_card(selection)['amount'] is None


def checkout_payload(selection=DEFAULT_SELECTION, custom_amount='5', channel='limeng'):
    card = checkout_card(selection)
    amount = custom_amount if custom_input_visible(selection) else card['amount']
    return payment.payload(card['package_id'], amount, channel)


def _draw_payment_mark(mark, channel):
    """32 x 24 vector marks, matching the web checkout SVGs."""
    if channel == 'limeng':
        # Smooth rounded outline, with a solid stripe, chip and number line.
        mark.create_polygon(4, 2, 28, 2, 31, 2, 31, 5, 31, 19,
                            31, 22, 28, 22, 4, 22, 1, 22, 1, 19,
                            1, 5, 1, 2, fill='', outline='#267d70',
                            width=2, smooth=True, splinesteps=24)
        mark.create_rectangle(2, 7, 30, 11, fill='#267d70', outline='')
        mark.create_rectangle(6, 14, 12, 19, fill='#267d70', outline='')
        mark.create_line(17, 18, 25, 18, fill='#267d70', width=2)
    else:
        def curve(start, control1, control2, end):
            points = []
            for step in range(1, 25):
                t = step / 24
                for axis in (0, 1):
                    points.append((1-t)**3 * start[axis] +
                                  3*(1-t)**2*t * control1[axis] +
                                  3*(1-t)*t*t * control2[axis] + t**3 * end[axis])
            return points
        # Cubic silhouettes sampled into Canvas polygons, never font glyphs.
        rear = [16, 6, 24, 6] + curve((24, 6), (31, 6), (29, 18), (21, 18)) + [18, 18, 17, 23, 11, 23]
        front = [10, 1, 19, 1] + curve((19, 1), (29, 1), (27, 14), (18, 14)) + [14, 14, 12, 21, 6, 21]
        mark.create_polygon(*rear, fill='#009CDE', outline='')
        mark.create_polygon(*front, fill='#003087', outline='')


def select_payment(variable, value, kind, explicit=False):
    variable.set(value)
    if explicit and kind == 'package':
        card = checkout_card(value)
        selection = {'fixed_5': 'preset-5', 'fixed_20': 'preset-20', 'fixed_50': 'preset-50', 'custom': 'custom'}[value]
        payment._notify('package_select', selection=selection, package_id=card['package_id'])
    elif explicit and kind == 'channel':
        payment._notify('channel_select', channel=value)


def close_payment_dialog(win, state):
    payment._notify('dialog_closed', pending_checkout=bool(state.get('order')))
    win.withdraw()


def cancel_pending_checkout(state):
    if state.get('order'):
        payment.track_paid_status(state, 2)
        state['order'] = None


def poll_checkout(token, order):
    # Profile refresh must never discard an official terminal status.
    try:
        status = payment.status(token, order)
    except Exception:
        status = None
    try:
        info = oauth_desktop.fetch_userinfo(token) if status != 2 else None
    except Exception:
        info = None
    return status, info


def apply_checkout_status(state, status):
    payment.track_paid_status(state, status)
    if status in (1, 2):
        state['order'] = None
        return True
    return False


INTENT_TTL = 15 * 60


def validated_preselection(preselect=None):
    data = preselect if isinstance(preselect, dict) else {}
    selection = data.get('selection', DEFAULT_SELECTION)
    if selection not in {card['key'] for card in CHECKOUT_CARDS}:
        selection = DEFAULT_SELECTION
    channel = data.get('channel', 'limeng')
    if channel not in ('limeng', 'paypal'):
        channel = 'limeng'
    amount = data.get('custom_amount', '5')
    try:
        checkout_payload('custom', amount, channel)
    except (ValueError, TypeError, OverflowError):
        amount = '5'
    return dict(selection=selection, channel=channel, custom_amount=str(amount))


def resume_topup(app):
    intent = getattr(app, '_topup_intent', None)
    app._topup_intent = None
    if intent and 0 <= time.monotonic() - intent['at'] < INTENT_TTL:
        show(app, preselect=intent)
        return True
    return False


def show(app, preselect=None, suggested_amount=None):
    old = getattr(app, '_payment_window', None)
    if old and old.winfo_exists() and preselect is not None:
        old.destroy()
        old = None
    if old and old.winfo_exists():
        old.deiconify()
        old.lift()
        return
    c = COPY.get(get_language(), COPY['en'])
    token = (app.oauth_session or {}).get('access_token', '')
    state = getattr(app, '_payment_state', None)
    if not state or state['token'] != token:
        state = dict(token=token, order=None, busy=False)
        app._payment_state = state
    win = tk.Toplevel(app.root)
    app._payment_window = win
    win.title('Video Studio · ' + c[0])
    win.geometry('560x660')
    win.configure(bg=PAGE)
    win.protocol('WM_DELETE_WINDOW', lambda: close_payment_dialog(win, state))
    body = tk.Frame(win, bg=PAGE, padx=24, pady=20)
    body.pack(fill='both', expand=True)
    tk.Label(body, text='Video Studio · ' + c[0], font=('', 18, 'bold'), bg=PAGE, fg=INK).pack(anchor='w', pady=(0, 18))
    if preselect is None and suggested_amount is not None:
        restored = dict(
            selection={20: 'fixed_20', 50: 'fixed_50'}.get(suggested_amount, DEFAULT_SELECTION),
            channel='limeng',
            custom_amount='5',
        )
    else:
        restored = validated_preselection(preselect)
    choice = tk.StringVar(value=restored['selection'])
    amount = tk.StringVar(value=restored['custom_amount'])
    channel = tk.StringVar(value=restored['channel'])
    grid = tk.Frame(body, bg=PAGE)
    grid.pack(fill='x')
    grid.columnconfigure((0, 1), weight=1, uniform='cards')
    cards = []
    def select(variable, value, explicit=False):
        select_payment(variable, value, 'package' if variable is choice else 'channel', explicit)
        for frame, var, item in cards:
            selected = var.get() == item
            bg = PALE if selected else WHITE
            frame.configure(bg=bg, highlightbackground=ACCENT if selected else LINE, highlightthickness=2 if selected else 1)
            for child in frame.winfo_children(): child.configure(bg=bg)
        if custom_input_visible(choice.get()): custom.pack(fill='x', pady=(10, 0), after=grid)
        else: custom.pack_forget()
    def bind_card(frame, variable, value):
        for widget in [frame] + frame.winfo_children():
            widget.bind('<Button-1>', lambda event: select(variable, value, explicit=True))
        frame.bind('<Return>', lambda event: select(variable, value, explicit=True))
        frame.bind('<space>', lambda event: select(variable, value, explicit=True))
    for index, card in enumerate(CHECKOUT_CARDS):
        frame = tk.Frame(grid, bg=WHITE, highlightbackground=LINE, highlightthickness=1, padx=16, pady=10, cursor='hand2', takefocus=1)
        row, column = divmod(index, 2)
        frame.grid(row=row, column=column, sticky='nsew',
                   padx=(0, 6) if column == 0 else (6, 0), pady=(0, 10))
        is_custom = custom_input_visible(card['key'])
        tk.Label(frame, text=c[1] if card['recommended'] else '', fg='#267d70', bg=WHITE, font=('', 10, 'bold')).pack(anchor='w')
        tk.Label(frame, text=c[16] if is_custom else f"${card['amount']}", fg=INK, bg=WHITE, font=('', 24, 'bold')).pack(anchor='w')
        tk.Label(frame, text=c[2] if is_custom else f"{card['credits']:,}", fg=INK, bg=WHITE, font=('', 15, 'bold')).pack(anchor='w')
        tk.Label(frame, text='' if is_custom else c[15], fg=MUTED, bg=WHITE, font=('', 11)).pack(anchor='w')
        cards.append((frame, choice, card['key']))
        bind_card(frame, choice, card['key'])
    custom = tk.Frame(body, bg=PAGE)
    tk.Label(custom, text=c[2], bg=PAGE, fg=MUTED).pack(side='left')
    ttk.Entry(custom, textvariable=amount, width=7).pack(side='left', padx=8)
    estimate = tk.StringVar()
    tk.Label(custom, textvariable=estimate, bg=PAGE, fg=INK).pack(side='left')
    def update_estimate(*args):
        try: estimate.set(f"${amount.get()} · {int(amount.get())*1000:,} {c[15]}")
        except ValueError: estimate.set(c[12])
    amount.trace_add('write', update_estimate)
    update_estimate()
    method_row = tk.Frame(body, bg=PAGE)
    method_row.pack(fill='x', pady=(8, 24))
    for value, text in [('limeng', c[3]), ('paypal', c[4])]:
        frame = tk.Frame(method_row, bg=PAGE, pady=6)
        frame.pack(side='left', fill='x', expand=True, padx=4)
        radio = tk.Radiobutton(frame, variable=channel, value=value, bg=PAGE, activebackground=PAGE, selectcolor=PAGE, fg=ACCENT, command=lambda: select(channel, channel.get(), explicit=True))
        radio.pack(side='left', padx=(0, 6))
        mark = tk.Canvas(frame, width=32, height=24, bg=PAGE, highlightthickness=0)
        mark.pack(side='left', padx=(0, 10))
        _draw_payment_mark(mark, value)
        label = tk.Label(frame, text=text, bg=PAGE, fg=INK, font=('', 13))
        label.pack(side='left')
        for widget in (mark, label):
            widget.bind('<Button-1>', lambda event, v=value: select(channel, v, explicit=True))
    select(choice, restored['selection'])
    message = tk.StringVar(value=c[6] if state['order'] else '')
    status_label = ttk.Label(body, textvariable=message, wraplength=400)
    def alive():
        return app._is_oauth_logged_in() and app.oauth_session['access_token'] == token
    def run(work, done):
        if state['busy']: return
        state['busy'] = True
        results = queue.Queue()
        def worker():
            try: results.put((work(), None))
            except Exception as exc: results.put((None, exc))
        def finish():
            try: result, error = results.get_nowait()
            except queue.Empty:
                app.root.after(100, finish)
                return
            state['busy'] = False
            if win.winfo_exists() and (alive() or error is not None): done(result, error)
        threading.Thread(target=worker, daemon=True).start()
        app.root.after(100, finish)
    def check():
        if not alive() or not state['order'] or time.monotonic() >= state.get('deadline', 0): return
        order = state['order']
        def done(result, error):
            if not error and state.get('order') == order:
                status, info = result
                terminal = apply_checkout_status(state, status)
                if info is not None:
                    app.oauth_session['profile'] = oauth_desktop.profile_from_userinfo(info)
                    app._refresh_oauth_header()
                if terminal:
                    if win.winfo_exists():
                        message.set(c[9] if status == 1 else c[10])
                        buy.configure(state='normal')
                    return
            if time.monotonic() < state.get('deadline', 0): app.root.after(4000, check)
        run(lambda: poll_checkout(token, order), done)
    def buy_now():
        if not app._is_oauth_logged_in():
            show_reconfirm()
            return
        if app.oauth_session['access_token'] != token:
            show(app, preselect=current_selection())
            return
        try: data = checkout_payload(choice.get(), amount.get(), channel.get())
        except (ValueError, StopIteration): message.set(c[12]); return
        buy.configure(state='disabled')
        def done(result, error):
            if error:
                if getattr(error, 'error_code', None) == 'AUTH' or getattr(error, 'code', None) in (401, 403):
                    show_reconfirm()
                else:
                    message.set(c[11])
                buy.configure(state='normal'); return
            state.update(payment_success_tracked=False, payment_cancelled_tracked=False, package_id=data['package_id'], channel=data['payment_channel'], order=result['orderNo'], deadline=time.monotonic()+300)
            message.set(c[6])
            webbrowser.open(result['redirectUrl'])
            app.root.after(2500, check)
        run(lambda: payment.create(token, data), done)
    def current_selection():
        return dict(selection=choice.get(), channel=channel.get(), custom_amount=amount.get())
    def confirm_login():
        payment._notify('reconfirm_continue')
        app._topup_intent = dict(current_selection(), at=time.monotonic())
        app.start_oauth_login('billing')
    notice = tk.Frame(body, bg=PALE, padx=12, pady=10)
    notice_text = tk.Label(notice, bg=PALE, fg=INK, wraplength=460, justify='left')
    notice_text.pack(fill='x', pady=(0, 8))
    confirm = ttk.Button(notice, command=confirm_login, style='Accent.TButton')
    confirm.pack(fill='x')
    def show_reconfirm():
        copy = RECONFIRM.get(get_language(), RECONFIRM['en'])
        offset = 0 if app.oauth_session or getattr(app, '_oauth_had_session', False) else 2
        payment._notify('reconfirm_shown', reason='resume' if offset == 0 else 'signin')
        notice_text.configure(text=copy[offset])
        confirm.configure(text=copy[offset + 1])
        message.set('')
        if buy.winfo_manager():
            notice.pack(fill='x', pady=(0, 12), before=buy)
            buy.pack_forget()
        win.update_idletasks()
        height = win.winfo_height()
        requested = body.winfo_reqheight() + 12
        target = min(max(660, requested), max(660, win.winfo_screenheight() - 60))
        if requested > height and target > height:
            win.geometry('%dx%d' % (win.winfo_width(), target))
    # Keep dialog controls accessible for keyboard/UI regression checks.
    app._payment_controls = dict(choice=choice, amount=amount, channel=channel,
                                 buy=buy_now, confirm=confirm_login, notice=notice)
    buy = ttk.Button(body, text=c[5], command=buy_now, style='Accent.TButton')
    buy.pack(fill='x', pady=(0, 12))
    app._payment_controls['buy_button'] = buy
    def update_status(*args):
        if message.get(): status_label.pack(fill='x', pady=(0, 12), before=buy)
        else: status_label.pack_forget()
    message.trace_add('write', update_status)
    update_status()
    ttk.Button(body, text=c[7], command=check, style='Link.TButton').pack(fill='x', pady=(0, 5))
    links = tk.Frame(body, bg=PAGE)
    links.pack()
    ttk.Button(links, text=c[13], style='Link.TButton', command=lambda: webbrowser.open('https://powertokens.ai/billing')).pack(side='left', padx=(0, 8))
    ttk.Button(links, text=c[14] + ': service@powertokens.ai', style='Link.TButton',
               command=lambda: webbrowser.open('mailto:service@powertokens.ai')).pack(side='left')
    if state['order']:
        buy.configure(state='disabled'); check()

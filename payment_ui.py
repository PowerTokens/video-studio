"""Non-modal checkout; order survives closing the picker for this app session."""
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk
import webbrowser
import payment
import oauth_desktop
from payment_copy import COPY
from i18n import get_language
from studio_ui import PAGE, WHITE, INK, MUTED, LINE, ACCENT, PALE


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


def _track_paid_status(state, status):
    payment.track_paid_status(state, status)


def show(app):
    if not app._is_oauth_logged_in():
        app.start_oauth_login()
        return
    old = getattr(app, '_payment_window', None)
    if old and old.winfo_exists():
        old.deiconify()
        old.lift()
        return
    c = COPY.get(get_language(), COPY['en'])
    token = app.oauth_session['access_token']
    state = getattr(app, '_payment_state', None)
    if not state or state['token'] != token:
        state = dict(token=token, order=None, baseline=app._oauth_profile().get('credits'), busy=False)
        app._payment_state = state
    win = tk.Toplevel(app.root)
    app._payment_window = win
    win.title('Video Studio · ' + c[0])
    win.geometry('560x660')
    win.configure(bg=PAGE)
    win.protocol('WM_DELETE_WINDOW', win.withdraw)
    body = tk.Frame(win, bg=PAGE, padx=24, pady=20)
    body.pack(fill='both', expand=True)
    tk.Label(body, text='Video Studio · ' + c[0], font=('', 18, 'bold'), bg=PAGE, fg=INK).pack(anchor='w', pady=(0, 18))
    choice = tk.IntVar(value=22)
    amount = tk.StringVar(value='5')
    channel = tk.StringVar(value='limeng')
    grid = tk.Frame(body, bg=PAGE)
    grid.pack(fill='x')
    grid.columnconfigure((0, 1), weight=1, uniform='cards')
    cards = []
    def select(variable, value):
        variable.set(value)
        for frame, var, item in cards:
            selected = var.get() == item
            bg = PALE if selected else WHITE
            frame.configure(bg=bg, highlightbackground=ACCENT if selected else LINE, highlightthickness=2 if selected else 1)
            for child in frame.winfo_children(): child.configure(bg=bg)
        if choice.get() == 33: custom.pack(fill='x', pady=(10, 0), after=grid)
        else: custom.pack_forget()
    def bind_card(frame, variable, value):
        for widget in [frame] + frame.winfo_children():
            widget.bind('<Button-1>', lambda event: select(variable, value))
        frame.bind('<Return>', lambda event: select(variable, value))
        frame.bind('<space>', lambda event: select(variable, value))
    for index, package in enumerate(payment.PACKAGES):
        frame = tk.Frame(grid, bg=WHITE, highlightbackground=LINE, highlightthickness=1, padx=16, pady=10, cursor='hand2', takefocus=1)
        frame.grid(row=index//2, column=index%2, sticky='nsew', padx=(0, 6) if index%2 == 0 else (6, 0), pady=(0, 10))
        tk.Label(frame, text=c[1] if package.get('is_recommended') else '', fg='#267d70', bg=WHITE, font=('', 10, 'bold')).pack(anchor='w')
        tk.Label(frame, text=c[16] if package['id']==33 else f"${package['sales_price']}", fg=INK, bg=WHITE, font=('', 24, 'bold')).pack(anchor='w')
        tk.Label(frame, text=c[2] if package['id']==33 else f"{package['credits_amount']:,}", fg=INK, bg=WHITE, font=('', 15, 'bold')).pack(anchor='w')
        tk.Label(frame, text='' if package['id']==33 else c[15], fg=MUTED, bg=WHITE, font=('', 11)).pack(anchor='w')
        cards.append((frame, choice, package['id']))
        bind_card(frame, choice, package['id'])
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
        radio = tk.Radiobutton(frame, variable=channel, value=value, bg=PAGE, activebackground=PAGE, selectcolor=PAGE, fg=ACCENT, command=lambda: select(channel, channel.get()))
        radio.pack(side='left', padx=(0, 6))
        mark = tk.Canvas(frame, width=32, height=24, bg=PAGE, highlightthickness=0)
        mark.pack(side='left', padx=(0, 10))
        _draw_payment_mark(mark, value)
        label = tk.Label(frame, text=text, bg=PAGE, fg=INK, font=('', 13))
        label.pack(side='left')
        for widget in (mark, label):
            widget.bind('<Button-1>', lambda event, v=value: select(channel, v))
    select(choice, 22)
    message = tk.StringVar(value=c[6] if state['order'] else '')
    status_label = ttk.Label(body, textvariable=message, wraplength=400)
    def alive():
        return win.winfo_exists() and app._is_oauth_logged_in() and app.oauth_session['access_token'] == token
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
            if alive(): done(result, error)
        threading.Thread(target=worker, daemon=True).start()
        app.root.after(100, finish)
    def check():
        if not alive() or not state['order']: return
        def work():
            try: status = payment.status(token, state['order'])
            except Exception: status = None
            info = oauth_desktop.fetch_userinfo(token) if status != 2 else None
            return status, info
        def done(result, error):
            if not error:
                status, info = result
                if status == 2:
                    state['order'] = None; message.set(c[10]); buy.configure(state='normal'); return
                credits = info.get('credits')
                _track_paid_status(state, status)
                if status == 1 or (isinstance(credits, (int, float)) and isinstance(state['baseline'], (int, float)) and credits > state['baseline']):
                    app.oauth_session['profile'] = oauth_desktop.profile_from_userinfo(info)
                    app._refresh_oauth_header()
                    state['order'] = None; message.set(c[9]); buy.configure(state='normal'); return
            if time.monotonic() < state.get('deadline', 0): win.after(4000, check)
        run(work, done)
    def buy_now():
        try: data = payment.payload(choice.get(), amount.get(), channel.get())
        except (ValueError, StopIteration): message.set(c[12]); return
        buy.configure(state='disabled')
        def done(result, error):
            if error:
                message.set(c[11]); buy.configure(state='normal'); return
            state.update(payment_success_tracked=False, package_id=data['package_id'], channel=data['payment_channel'], order=result['orderNo'], baseline=app._oauth_profile().get('credits'), deadline=time.monotonic()+300)
            message.set(c[6])
            webbrowser.open(result['redirectUrl'])
            win.after(2500, check)
        run(lambda: payment.create(token, data), done)
    buy = ttk.Button(body, text=c[5], command=buy_now, style='Accent.TButton')
    buy.pack(fill='x', pady=(0, 12))
    def update_status(*args):
        if message.get(): status_label.pack(fill='x', pady=(0, 12), before=buy)
        else: status_label.pack_forget()
    message.trace_add('write', update_status)
    update_status()
    ttk.Button(body, text=c[7], command=check, style='Link.TButton').pack(fill='x', pady=(0, 5))
    ttk.Button(body, text=c[13], style='Link.TButton', command=lambda: webbrowser.open('https://powertokens.ai/billing')).pack()
    if state['order']:
        buy.configure(state='disabled'); state['deadline'] = time.monotonic()+300; check()

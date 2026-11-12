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
    win.geometry('460x550')
    win.protocol('WM_DELETE_WINDOW', win.withdraw)
    body = ttk.Frame(win, padding=24)
    body.pack(fill='both', expand=True)
    ttk.Label(body, text='Video Studio · ' + c[0], font=('', 16, 'bold')).pack(anchor='w', pady=10)
    choice = tk.IntVar(value=22)
    amount = tk.StringVar(value='5')
    channel = tk.StringVar(value='limeng')
    for p in payment.PACKAGES:
        text = c[2] if p['amount_type'] == 2 else f"${p['sales_price']} USD · {p['credits_amount']:,} credits" + (' · ' + c[1] if p.get('is_recommended') else '')
        ttk.Radiobutton(body, text=text, variable=choice, value=p['id']).pack(anchor='w', pady=6)
    ttk.Entry(body, textvariable=amount, width=18).pack(anchor='w')
    for value, text in [('limeng', c[3]), ('paypal', c[4])]:
        ttk.Radiobutton(body, text=text, variable=channel, value=value).pack(anchor='w', pady=6)
    message = tk.StringVar(value=c[6] if state['order'] else '')
    ttk.Label(body, textvariable=message, wraplength=400).pack(fill='x', pady=12)
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
                message.set(c[11] + (' ' + str(error) if isinstance(error, ValueError) else '')); buy.configure(state='normal'); return
            state.update(order=result['orderNo'], baseline=app._oauth_profile().get('credits'), deadline=time.monotonic()+300)
            message.set(c[6])
            webbrowser.open(result['redirectUrl'])
            win.after(2500, check)
        run(lambda: payment.create(token, data), done)
    buy = ttk.Button(body, text=c[5], command=buy_now, style='Accent.TButton')
    buy.pack(fill='x', pady=5)
    ttk.Button(body, text=c[7], command=check).pack(fill='x', pady=5)
    ttk.Button(body, text=c[8], command=win.withdraw).pack(fill='x')
    ttk.Button(body, text=c[13], style='Link.TButton', command=lambda: webbrowser.open('https://powertokens.ai/billing')).pack()
    if state['order']:
        buy.configure(state='disabled'); state['deadline'] = time.monotonic()+300; check()

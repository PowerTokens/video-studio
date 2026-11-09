"""Desktop Chat tab: refine what to shoot, then Fill Generate or Split to Batch."""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from chat_helpers import (
    build_api_messages,
    has_usable_batch_content,
    has_usable_fill_content,
    parse_assistant_payload,
    script_outline_from_chat,
)
from i18n import get_language, t
from models import list_models
from storyboard import (
    ALLOWED_TEXT_MODELS,
    DEFAULT_TEXT_MODEL,
    chat_completions,
    is_free_text_model,
    resolve_text_model,
    storyboard_from_script,
    text_model_label,
)
from studio_ui import Flow, card, heading, label, text_area


def _catalog_hint():
    try:
        return ', '.join(spec.id for spec in list_models())
    except Exception:
        return 'wan3.0-video'


class ChatTab:
    def __init__(self, app, frame):
        self.app = app
        self.frame = frame
        self.messages = []  # API history without system: {role, content}
        self.last_plan = None
        self._busy = False
        self._pending = queue.Queue()
        self._poll_scheduled = False
        self._build()

    def _build(self):
        heading(self.frame, t('chat_title'), t('chat_desc'))

        thread_card = card(self.frame, t('chat_thread_card'), t('chat_thread_desc'))
        self.thread = text_area(thread_card, height=14)
        self.thread.pack(fill='both', expand=True)
        self.thread.configure(state='disabled')
        self._append_system_hint()

        composer = card(self.frame, t('chat_composer_card'))
        self.input = text_area(composer, height=4, undo=True)
        self.input.pack(fill='x', pady=(0, 8))
        self.input.bind('<Control-Return>', lambda *_: self.send())

        tools = ttk.Frame(composer)
        tools.pack(fill='x')
        ttk.Label(tools, text=t('chat_text_model'), style='Muted.TLabel').pack(anchor='w')
        self.text_model = tk.StringVar(value=DEFAULT_TEXT_MODEL)
        self.text_choices = {text_model_label(mid): mid for mid in ALLOWED_TEXT_MODELS}
        self.text_label = tk.StringVar(value=text_model_label(DEFAULT_TEXT_MODEL))
        self.text_combo = ttk.Combobox(
            tools, textvariable=self.text_label,
            values=list(self.text_choices.keys()), state='readonly')
        self.text_combo.pack(fill='x', pady=(4, 0))
        self.text_combo.bind('<<ComboboxSelected>>', lambda *_: self._sync_credit_note())
        self.credit_note = tk.StringVar()
        self.credit_label = label(tools, variable=self.credit_note)
        self._sync_credit_note()

        actions = Flow(composer)
        actions.pack(fill='x', pady=(10, 0))
        self.send_btn = ttk.Button(actions, text=t('chat_send'), style='Accent.TButton',
                                   command=self.send)
        self.clear_btn = ttk.Button(actions, text=t('chat_clear'), command=self.clear)
        actions.schedule()

        exits = card(self.frame, t('chat_exits_card'), t('chat_exits_desc'))
        exit_row = Flow(exits)
        exit_row.pack(fill='x')
        self.fill_btn = ttk.Button(exit_row, text=t('chat_fill_generate'),
                                   command=self.fill_generate)
        self.batch_btn = ttk.Button(exit_row, text=t('chat_split_batch'),
                                    command=self.split_to_batch)
        exit_row.schedule()
        self.status_var = tk.StringVar(value='')
        label(exits, variable=self.status_var).pack(fill='x', pady=(8, 0))
        self._sync_exit_buttons()

    def _sync_credit_note(self):
        mid = self.text_choices.get(self.text_label.get(), self.text_model.get())
        mid = resolve_text_model(mid)
        self.text_model.set(mid)
        if is_free_text_model(mid):
            self.credit_note.set('')
            self.credit_label.pack_forget()
        else:
            self.credit_note.set(t('chat_credit_note'))
            if not self.credit_label.winfo_manager():
                self.credit_label.pack(fill='x', pady=(6, 0))

    def _append_system_hint(self):
        self._append_bubble('assistant', t('chat_welcome'))

    def _append_bubble(self, role, text):
        text = str(text or '').strip()
        if not text:
            return
        prefix = t('chat_you') if role == 'user' else t('chat_assistant')
        self.thread.configure(state='normal')
        if self.thread.index('end-1c') != '1.0':
            self.thread.insert('end', '\n\n')
        self.thread.insert('end', '%s\n%s' % (prefix, text))
        self.thread.see('end')
        self.thread.configure(state='disabled')

    def _set_busy(self, busy):
        self._busy = bool(busy)
        try:
            self.send_btn.configure(state='disabled' if self._busy else 'normal')
            self.clear_btn.configure(state='disabled' if self._busy else 'normal')
            self.input.configure(state='disabled' if self._busy else 'normal')
            self.text_combo.configure(state='disabled' if self._busy else 'readonly')
        except tk.TclError:
            pass
        if self._busy:
            self.status_var.set(t('chat_thinking'))
        self._sync_exit_buttons()

    def _sync_exit_buttons(self):
        fill_ok = has_usable_fill_content(self.last_plan) and not self._busy
        batch_ok = has_usable_batch_content(self.messages, self.last_plan) and not self._busy
        try:
            self.fill_btn.configure(state='normal' if fill_ok else 'disabled')
            self.batch_btn.configure(state='normal' if batch_ok else 'disabled')
        except tk.TclError:
            pass

    def _selected_text_model(self):
        mid = self.text_choices.get(self.text_label.get(), self.text_model.get())
        return resolve_text_model(mid)

    def _oauth_logged_in(self):
        """True only for a live PowerTokens OAuth session. API keys do not count."""
        checker = getattr(self.app, '_is_oauth_logged_in', None)
        if not callable(checker):
            return False
        try:
            return bool(checker())
        except Exception:
            return False

    def _prompt_chat_login(self):
        """Empty pool and logged out: need-login tip, then desktop OAuth.

        Leaves the draft, transcript history, and any plan untouched.
        A saved key never reaches this path. If a login is already in
        progress, start_oauth_login keeps that flow.
        """
        tip = t('chat_need_login')
        try:
            self._append_bubble('assistant', tip)
        except tk.TclError:
            pass
        try:
            self.status_var.set(tip)
        except (tk.TclError, AttributeError):
            pass
        status = getattr(self.app, 'status', None)
        setter = getattr(status, 'set', None)
        if callable(setter):
            try:
                setter(tip)
            except tk.TclError:
                pass
        parent = None
        try:
            parent = self.frame.winfo_toplevel()
        except tk.TclError:
            parent = None
        try:
            messagebox.showinfo(t('chat_title'), tip, parent=parent)
        except tk.TclError:
            pass
        starter = getattr(self.app, 'start_oauth_login', None)
        if callable(starter):
            starter()

    def send(self, *_):
        if self._busy:
            return
        text = self.input.get('1.0', 'end').strip()
        if not text:
            return
        # A key in the pool is enough, with or without an OAuth session.
        # Empty pool: sign in if logged out (draft stays); otherwise Advanced.
        if not getattr(self.app, 'keys', None):
            if self._oauth_logged_in():
                self.app.ensure_submit_credentials()
            else:
                self._prompt_chat_login()
            return
        key = self.app.selected_key()
        self.input.delete('1.0', 'end')
        self._append_bubble('user', text)
        self.messages.append({'role': 'user', 'content': text})
        self.last_plan = None
        self._sync_exit_buttons()
        api_messages = build_api_messages(
            self.messages, lang=get_language(), catalog=_catalog_hint())
        text_model = self._selected_text_model()
        self._set_busy(True)
        self._schedule_poll()

        def worker():
            try:
                content, _ = chat_completions(key, api_messages, text_model=text_model)
                self._pending.put(('ok', content))
            except Exception as exc:
                self._pending.put(('err', str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _schedule_poll(self):
        if self._poll_scheduled:
            return
        self._poll_scheduled = True
        try:
            self.frame.after(80, self._poll)
        except tk.TclError:
            self._poll_scheduled = False

    def _poll(self):
        self._poll_scheduled = False
        try:
            while True:
                kind, payload = self._pending.get_nowait()
                if kind == 'ok':
                    self._on_reply(payload)
                elif kind == 'batch_ok':
                    jobs, _meta = payload
                    self.app.batch.load_jobs(jobs, source='chat', character_setting='')
                    try:
                        self.app.tabs.select(3)  # Batch after Generate / Chat / Compare
                    except tk.TclError:
                        pass
                    self.status_var.set(t('chat_split_ok', len(jobs)))
                    self.app.status.set(t('storyboard_ok', len(jobs)))
                    self._set_busy(False)
                else:
                    self._on_error(payload)
        except queue.Empty:
            pass
        if self._busy:
            self._schedule_poll()

    def _on_reply(self, content):
        parsed = parse_assistant_payload(content, fallback_message=t('chat_bad_json'))
        display = parsed['assistant_message'] or t('chat_bad_json')
        self._append_bubble('assistant', display)
        self.messages.append({'role': 'assistant', 'content': content})
        if parsed.get('ready') and parsed.get('plan'):
            self.last_plan = parsed['plan']
            self.status_var.set(t('chat_plan_ready'))
        else:
            self.last_plan = None
            self.status_var.set('')
        self._set_busy(False)

    def _on_error(self, detail):
        self._append_bubble('assistant', t('chat_error', detail))
        self.status_var.set('')
        self._set_busy(False)

    def clear(self):
        if self._busy:
            return
        self.messages = []
        self.last_plan = None
        self.thread.configure(state='normal')
        self.thread.delete('1.0', 'end')
        self.thread.configure(state='disabled')
        self._append_system_hint()
        self.status_var.set('')
        self._sync_exit_buttons()

    def fill_generate(self):
        if not has_usable_fill_content(self.last_plan):
            messagebox.showinfo(t('chat_title'), t('chat_need_plan'))
            return
        self.app.apply_chat_plan(self.last_plan)
        self.status_var.set(t('chat_filled'))
        self.app.status.set(t('chat_filled'))

    def split_to_batch(self):
        if self._busy:
            return
        outline = script_outline_from_chat(self.messages, self.last_plan)
        if not outline:
            messagebox.showinfo(t('chat_title'), t('chat_need_outline'))
            return
        if not self.app.ensure_submit_credentials():
            return
        try:
            key = self.app.selected_key()
        except Exception:
            messagebox.showerror(t('chat_title'), t('storyboard_need_key'))
            return
        text_model = self._selected_text_model()
        plan = self.last_plan or {}
        duration = int(plan.get('duration') or 8)
        ratio = str(plan.get('ratio') or '9:16')
        resolution = str(plan.get('resolution') or '720p')
        model_id = str(plan.get('model') or 'wan3.0-video')
        self._set_busy(True)
        self.status_var.set(t('chat_splitting'))
        self._schedule_poll()

        def worker():
            try:
                jobs, meta = storyboard_from_script(
                    outline, key, duration=duration, model_id=model_id,
                    text_model=text_model, ratio=ratio, resolution=resolution)
                self._pending.put(('batch_ok', (jobs, meta)))
            except Exception as exc:
                self._pending.put(('err', str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def export_state(self):
        draft = ''
        try:
            if not self._busy:
                draft = self.input.get('1.0', 'end-1c')
        except tk.TclError:
            pass
        return {
            'messages': list(self.messages),
            'last_plan': dict(self.last_plan) if self.last_plan else None,
            'text_model': self._selected_text_model(),
            'draft': draft,
        }

    def restore_state(self, state):
        if not state:
            return
        self.messages = list(state.get('messages') or [])
        self.last_plan = state.get('last_plan')
        mid = resolve_text_model(state.get('text_model') or DEFAULT_TEXT_MODEL)
        self.text_model.set(mid)
        label_text = text_model_label(mid)
        # Rebuild choices for current language
        self.text_choices = {text_model_label(m): m for m in ALLOWED_TEXT_MODELS}
        self.text_combo.configure(values=list(self.text_choices.keys()))
        if label_text in self.text_choices:
            self.text_label.set(label_text)
        self._sync_credit_note()
        self.thread.configure(state='normal')
        self.thread.delete('1.0', 'end')
        self.thread.configure(state='disabled')
        if not self.messages:
            self._append_system_hint()
        else:
            for item in self.messages:
                role = item.get('role')
                content = item.get('content') or ''
                if role == 'user':
                    self._append_bubble('user', content)
                elif role == 'assistant':
                    parsed = parse_assistant_payload(content)
                    self._append_bubble('assistant', parsed['assistant_message'] or content)
        draft = state.get('draft') or ''
        if draft:
            try:
                self.input.delete('1.0', 'end')
                self.input.insert('1.0', draft)
            except tk.TclError:
                pass
        self._sync_exit_buttons()

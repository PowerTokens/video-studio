"""PowerTokens Video Studio; run with Python 3.9+ / tkinter."""
import hashlib
import ctypes
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import uuid
import webbrowser

from i18n import (LANGUAGES, LANGUAGE_NAMES, get_language, is_zh, load_settings,
                  remember_badge_text, remember_note_text, save_language,
                  save_setting, set_language, t)
from studio_ui import (APP_NAME, VERSION, subtitle, promotion_text, FONT, WHITE, PAGE, INK, LINE, ACCENT, LOGO_FILE,
                       apply_theme, label, heading, card, Columns, Flow, ScrollPage, text_area,
                       load_image, header_logo_file, KeyStatusPill, PillChip, AccentPillButton, LangPill, WebSelect,
                       WebTabBar, window_title, bind_command_or_control)
from input_helpers import parse_keys, infer_prompt
from batch_ui import BatchTab
from chat_ui import ChatTab
from compare_ui import CompareTab
from models import (DEFAULT_MODEL_ID, list_models, get_model, resolve_model_id, snap_params,
                   format_adjust_notice, model_allows_first_frame, allowed_media_types)
from frame_util import encode_local_image_as_data_url
from wan_core import (Client, DATA_DIR, UTM,
                      TaskError, estimate_cost, fingerprint, payload, verify_key)
from platform_paths import default_output_dir, reveal_path
import secret_store
import oauth_desktop
import analytics
from difflib import SequenceMatcher
import update_check
import conversation_store
import time

OFFICIAL_KEY_URL = 'https://powertokens.ai/zh-Hans/api-keys?' + UTM
OFFICIAL_KEY_URL_EN = 'https://powertokens.ai/api-keys?' + UTM
KEY_QUOTA_TIP_URL = 'https://powertokens.ai/zh-Hans/api-keys?utm_source=videostudio&utm_medium=app&utm_campaign=video-studio'
KEY_QUOTA_TIP_URL_EN = 'https://powertokens.ai/api-keys?utm_source=videostudio&utm_medium=app&utm_campaign=video-studio'
BILLING_URL = 'https://powertokens.ai/zh-Hans/billing'
BILLING_URL_EN = 'https://powertokens.ai/billing'


def key_quota_tip_url():
    """Quota / expiry tip → API keys, with product-forced signup UTM."""
    url = KEY_QUOTA_TIP_URL if is_zh() else KEY_QUOTA_TIP_URL_EN
    try:
        import utm_attr
        return utm_attr.append_to_url(
            url,
            content=utm_attr.CONTENT_SIGNUP,
            force_product=True,
        )
    except Exception:
        return url


def official_key_url():
    """Register / get API key, with product-forced signup UTM."""
    url = OFFICIAL_KEY_URL if is_zh() else OFFICIAL_KEY_URL_EN
    try:
        import utm_attr
        return utm_attr.append_to_url(
            url,
            content=utm_attr.CONTENT_SIGNUP,
            force_product=True,
        )
    except Exception:
        return url


def onboard_signup_url():
    """Onboarding signup link, with product-forced signup UTM."""
    url = t('onboard_signup_url')
    try:
        import utm_attr
        return utm_attr.append_to_url(
            url,
            content=utm_attr.CONTENT_SIGNUP,
            force_product=True,
        )
    except Exception:
        return url


def billing_url():
    """Official console billing page with product-forced UTM (recharge → VS)."""
    url = BILLING_URL if is_zh() else BILLING_URL_EN
    try:
        import utm_attr
        return utm_attr.append_to_url(
            url,
            content=utm_attr.CONTENT_BILLING,
            force_product=True,
        )
    except Exception:
        return url + ('?' + UTM + '&utm_content=app.billing')


def protect(raw, decrypt=False):
    """Windows DPAPI. Stable entry point for tests; implementation is secret_store.dpapi_protect.

    On non-Windows this still raises. macOS remembered keys go through the login
    Keychain in secret_store.write_remembered_keys, not through this function.
    """
    return secret_store.dpapi_protect(raw, decrypt)


class App:
    def __init__(self, root):
        self.root = root
        if sys.platform == 'darwin':
            # Without this, a Tk app launched outside LaunchServices (or before
            # the bundle Info.plist is attached) shows "Python" in the macOS
            # menu bar, and Local Network prompts can be attributed to Python.
            try:
                root.tk.call('tk', 'appname', 'PowerTokens Video Studio')
            except tk.TclError:
                pass
        self.keys, self.records = [], {}
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.busy = False
        self.prompt_timer = None
        self.scale = apply_theme(root)
        root.title(window_title())
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
        min_w = 980 if sys.platform == 'darwin' else 860
        root.minsize(min(min_w, width), min(620, height))
        self.oauth_session = None
        self.oauth_busy = False
        self.key_verify_busy = False
        self.key_status_kind = 'soft'  # soft|ok|warn|bad — traffic-light
        self._update_offer = None
        self._update_dest = None
        self._update_stop = threading.Event()
        self._update_downloading = False
        self._update_phase = None
        self._update_progress_value = 0
        self._update_progress_text = ''
        self._update_fail_kind = None
        self._update_dismissed_session = False
        self.build_ui()
        self.load_keys()
        self._load_oauth_session()
        self._restore_generate_form()
        self.refresh_history()
        self._boot_analytics()
        root.after(150, self.drain)
        root.protocol('WM_DELETE_WINDOW', self.close)
        self._bind_analytics_focus()
        if not update_check.skip_update_check():
            root.after(800, self._start_update_check)

    def build_ui(self):
        """Build every widget in the current language; rebuilt in place by switch_language()."""
        root = self.root
        shell = self.shell = ttk.Frame(root, style='Page.TFrame')
        shell.pack(fill='both', expand=True)
        header = ttk.Frame(shell, padding=(24, 14, 24, 12))
        header.pack(fill='x')
        # One balanced bar: brand block (left) ↔ lang/tips + account cluster (right).
        bar = ttk.Frame(header)
        bar.pack(fill='x')
        brand_block = ttk.Frame(bar)
        brand_block.pack(side='left', fill='x', expand=True)
        brand = ttk.Frame(brand_block)
        brand.pack(fill='x')
        if self.logo_image is not None:
            self.logo_label = ttk.Label(brand, image=self.logo_image)
            self.logo_label.pack(side='left', padx=(0, 12))
        ttk.Label(brand, text=APP_NAME, style='Brand.TLabel').pack(side='left')
        ttk.Label(brand_block, text=subtitle(), style='Muted.TLabel').pack(anchor='w', pady=(4, 0))

        toolbar = ttk.Frame(bar)
        toolbar.pack(side='right')
        # Language pill (web .lang-switch select border-radius: 999px).
        self.lang_var = tk.StringVar(value=LANGUAGE_NAMES.get(get_language(), 'English'))
        self.lang_combo = LangPill(
            toolbar, text=self.lang_var.get(), chrome=WHITE,
            values=[LANGUAGE_NAMES[code] for code in LANGUAGES],
            command=self._on_lang_picked)
        self.lang_combo.pack(side='left')
        self.lang_buttons = {}  # kept for older tests / capture_state compatibility
        self.help_tips_btn = ttk.Button(toolbar, text=t('onboard_help'), style='Link.TButton',
                                        command=self.show_onboarding)
        self.help_tips_btn.pack(side='left', padx=(10, 14))

        self.key_badge = tk.StringVar(value=t('badge_no_key'))
        self.oauth_account_var = tk.StringVar(value='')
        self.oauth_credits_var = tk.StringVar(value=t('credits_btn'))
        # Account cluster packs right-to-left so visual L→R is name · credits · key-pill · login/out.
        # Canvas pills mirror web .credits-chip / .btn-accent stadium radius (ttk cannot round).
        self.header_oauth_btn = AccentPillButton(
            toolbar, text=t('oauth_login'), chrome=WHITE,
            command=lambda: self.start_oauth_login('header'))
        self.header_oauth_btn.pack(side='right')
        self.header_logout_btn = ttk.Button(toolbar, text=t('oauth_logout'), style='Link.TButton',
                                            command=self.logout_oauth)
        self.oauth_credits_label = PillChip(
            toolbar, textvariable=self.oauth_credits_var, variant='credits', chrome=WHITE,
            command=lambda: self.open_billing('credits_chip'))
        # Always visible like web billing-chip (Credits · Top up even when logged out).
        self.oauth_credits_label.pack(side='right', padx=(0, 8))
        self.oauth_account_label = PillChip(
            toolbar, textvariable=self.oauth_account_var, variant='user', chrome=WHITE)
        self.header_key_pill = KeyStatusPill(toolbar, chrome=WHITE)
        # Not packed yet: soft "Sign in" duplicates the OAuth button; shown after a real key state.
        self._header_toolbar = toolbar
        self._refresh_oauth_header()
        self._build_update_banner(shell)
        self._tab_keys = (
            'tab_generate', 'tab_chat', 'tab_compare', 'tab_batch', 'tab_history', 'tab_keys')
        self.tab_bar = WebTabBar(
            shell, labels=[t(key) for key in self._tab_keys], on_select=self._select_web_tab)
        self.tab_bar.pack(fill='x')
        tabs = ttk.Notebook(shell)
        tabs.pack(fill='both', expand=True)
        pages = [ScrollPage(tabs) for _ in range(6)]
        for page, name in zip(pages, self._tab_keys):
            tabs.add(page, text=t(name))
        self.tabs, self.pages = tabs, pages
        self.build_create(pages[0].body)
        self.chat = ChatTab(self, pages[1].body)
        self.compare = CompareTab(self, pages[2].body)
        self.batch = BatchTab(self, pages[3].body)
        self.build_history(pages[4].body)
        self.build_keys(pages[5].body)
        self.status = tk.StringVar(value=t('status_ready'))
        footer = ttk.Frame(shell, style='Page.TFrame')
        footer.pack(fill='x', padx=28, pady=(8, 12))
        ttk.Button(footer, text=t('feedback_email'), style='Link.TButton',
                   command=lambda: webbrowser.open('mailto:service@powertokens.ai')).pack(side='right', padx=(16, 0))
        # Give the status its own host so wrapping follows the space left by the link.
        status_host = ttk.Frame(footer, style='Page.TFrame')
        status_host.pack(side='left', fill='x', expand=True)
        label(status_host, variable=self.status, style='Page.TLabel').pack(fill='x', expand=True)
        self._bind_analytics_surfaces()
        if self._update_offer and not self._update_dismissed_session:
            self._show_update_offer(self._update_offer, restore=True)

    def _bind_analytics_surfaces(self):
        if not hasattr(self, 'tabs'):
            return
        surfaces = ('generate', 'chat', 'compare', 'batch', 'history', 'keys')

        def on_tab(_evt=None):
            try:
                idx = int(self.tabs.index('current'))
                if hasattr(self, 'tab_bar'):
                    self.tab_bar.select(idx, notify=False)
                surface = surfaces[idx] if 0 <= idx < len(surfaces) else ''
                if surface:
                    analytics.notify_surface_open(surface)
            except Exception:
                return

        self.tabs.bind('<<NotebookTabChanged>>', on_tab)
        on_tab()

    def _select_web_tab(self, index):
        try:
            if int(self.tabs.index('current')) != int(index):
                self.tabs.select(index)
        except tk.TclError:
            return

    def _bind_analytics_focus(self):
        def focused():
            try:
                if str(self.root.state()) == 'iconic':
                    return False
                return self.root.focus_displayof() is not None
            except Exception:
                return True

        try:
            analytics.get_client() and analytics.get_client().set_focused_hook(focused)
        except Exception:
            pass

        def on_map(_evt=None):
            try:
                analytics.handle_focus(True)
            except Exception:
                pass

        def on_unmap(_evt=None):
            try:
                if _evt is not None and getattr(_evt, 'widget', None) is not self.root:
                    return
                analytics.handle_focus(False)
            except Exception:
                pass

        try:
            self.root.bind('<Map>', on_map, add='+')
            self.root.bind('<Unmap>', on_unmap, add='+')
            self.root.bind('<FocusIn>', on_map, add='+')
            self.root.bind('<FocusOut>', lambda e: on_unmap(e) if e.widget is self.root else None, add='+')
        except tk.TclError:
            pass

    def _on_lang_picked(self, name=None, *_):
        if name is None:
            name = (self.lang_var.get() if hasattr(self, 'lang_var') else '') or ''
        if hasattr(self, 'lang_var'):
            self.lang_var.set(name)
        code = next((c for c, n in LANGUAGE_NAMES.items() if n == name), None)
        if code:
            self.switch_language(code)

    def switch_language(self, lang):
        """Switch the UI language now and remember it in settings.json."""
        if lang == get_language():
            return
        try:
            analytics.notify_language_set(lang)
        except Exception:
            pass
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
        # Rebuilt below; update_cost() must not touch the old widgets after shell.destroy().
        for name in ('promo_label', 'cost', 'generate_btn', 'credit_tip', 'credit_tip_text', 'credit_tip_suggest', 'credit_tip_button', 'credit_tip_title'):
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
            chat=self.chat.export_state() if hasattr(self, 'chat') else {},
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
            if kind in self.media:
                self.media[kind].set(value)
        ff = (state.get('media') or {}).get('first_frame', '')
        self._ff_local_name = None
        if ff.startswith('data:image/'):
            self._ff_syncing_url = True
            self._ff_url_var.set('')
            self._ff_syncing_url = False
        elif ff:
            self._ff_syncing_url = True
            self._ff_url_var.set(ff)
            self._ff_syncing_url = False
        self._refresh_first_frame_ui()
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
        if hasattr(self, 'chat'):
            self.chat.restore_state(state.get('chat') or {})
        if hasattr(self, 'compare'):
            self.compare.import_state(state.get('compare') or {})
        self.update_cost()
        self.refresh_history()
        self.status.set(t('status_idle') if self.keys else t('status_ready'))
        self.tabs.select(state['tab'])
        self._refresh_oauth_header()

    def _onboarding_pending(self):
        return not bool(load_settings().get('onboarding_dismissed'))

    def _build_onboarding(self, parent, force=False):
        if self.onboard_card is not None:
            try:
                self.onboard_card.destroy()
            except tk.TclError:
                pass
            self.onboard_card = None
        if not force and not self._onboarding_pending():
            return
        box = card(parent, t('onboard_title'))
        self.onboard_card = box
        for key in ('onboard_step1', 'onboard_step2', 'onboard_step3'):
            label(box, t(key)).pack(fill='x', pady=(0, 4))
        from payment_copy import COPY
        ttk.Button(box, text=COPY.get(get_language(), COPY['en'])[14] + ': service@powertokens.ai',
                   style='Link.TButton', command=lambda: webbrowser.open('mailto:service@powertokens.ai')).pack(anchor='w')
        actions = Flow(box)
        actions.pack(fill='x', pady=(8, 0))
        ttk.Button(actions, text=t('onboard_get_key'), style='Accent.TButton', command=lambda: self.start_oauth_login('dialog'))
        self.onboard_keys_btn = ttk.Button(actions, text=t('onboard_goto_keys'), style='Link.TButton',
                                           command=self._onboard_goto_keys)
        ttk.Button(actions, text=t('onboard_dismiss'), command=self.dismiss_onboarding)
        actions.schedule()

    def show_onboarding(self):
        host = getattr(self, '_create_frame', None)
        if host is None:
            return
        self._build_onboarding(host, force=True)
        card = self.onboard_card
        if card is None:
            return
        try:
            siblings = [c for c in host.winfo_children() if c is not card]
            card.pack_forget()
            if siblings:
                card.pack(fill='x', pady=(0, 12), before=siblings[0])
            else:
                card.pack(fill='x', pady=(0, 12))
        except tk.TclError:
            pass


    def dismiss_onboarding(self):
        save_setting('onboarding_dismissed', True)
        if self.onboard_card is not None:
            try:
                self.onboard_card.destroy()
            except tk.TclError:
                pass
            self.onboard_card = None

    def _onboard_goto_keys(self):
        """Quiet path for people who already have a key. Not the logged-out hero."""
        self._focus_advanced_keys()

    def _focus_advanced_keys(self):
        """Open API Key → Advanced (paste), the secondary credential path."""
        try:
            self.tabs.select(len(self.pages) - 1)
        except Exception:
            pass
        opener = getattr(self, 'keys_advanced_open', None)
        if opener is not None and not opener.get() and hasattr(self, 'keys_advanced_body'):
            try:
                self._toggle_keys_advanced()
            except tk.TclError:
                pass

    def ensure_submit_credentials(self, parent=None):
        """True when the key pool can submit a create/generate request.

        Empty pool and logged out: confirm before opening OAuth.
        Empty pool and logged in: advanced-paste tip, then API Key → Advanced.
        Pasted-key users can submit; OAuth users need known credits.
        """
        if self.keys and self._oauth_credits_unknown():
            self._show_credit_relogin()
            return False
        if self.keys:
            return True
        logged_in = False
        try:
            logged_in = bool(self._is_oauth_logged_in())
        except Exception:
            logged_in = False
        if parent is None:
            parent = getattr(self, 'root', None)
        if not logged_in:
            tip = t('need_key')
            try:
                self.status.set(tip)
            except Exception:
                pass
            confirmed = False
            try:
                if parent is not None:
                    confirmed = messagebox.askokcancel(t('oauth_login'), tip, parent=parent)
                else:
                    confirmed = messagebox.askokcancel(t('oauth_login'), tip)
            except tk.TclError:
                pass
            try:
                if confirmed:
                    self.start_oauth_login('generate')
            except Exception:
                pass
            return False
        tip = t('need_key_advanced')
        try:
            self.status.set(tip)
        except Exception:
            pass
        try:
            if parent is not None:
                messagebox.showinfo(t('keys_title'), tip, parent=parent)
            else:
                messagebox.showinfo(t('keys_title'), tip)
        except tk.TclError:
            pass
        self._focus_advanced_keys()
        return False

    def _onboard_open_signup(self):
        import webbrowser
        webbrowser.open(onboard_signup_url())


    def build_create(self, frame):
        self._create_frame = frame
        self.onboard_card = None
        self._build_onboarding(frame)
        heading(frame, t('gen_title'), t('gen_desc'))
        columns = Columns(frame, self.scale, weights=(3, 2))
        columns.pack(fill='x')
        prompt_box = card(columns.left, t('prompt_card'))
        self.prompt = text_area(prompt_box, height=2, undo=True)
        self.prompt.pack(fill='x', pady=(0, 4))
        ttk.Button(prompt_box, text=t('prompt_clear'), command=self.clear_generate_prompt).pack(anchor='e', pady=(0, 8))
        self.ai_plan_help_btn = ttk.Button(prompt_box, text=t('ai_plan_help'),
                                           style='Link.TButton', command=self.start_ai_plan)
        self.ai_plan_help_btn.pack(anchor='w', fill='x', pady=(0, 6))
        self._ai_highlights = set()
        self._ai_field_labels = {}
        self._ai_prompt_background = self.prompt.cget('background')

        self.auto_prompt = tk.BooleanVar(value=True)
        self.prompt_hint = tk.StringVar(value=t('prompt_hint_default'))
        ttk.Checkbutton(prompt_box, text=t('auto_detect'), variable=self.auto_prompt,
                        command=self.apply_prompt).pack(anchor='w')
        label(prompt_box, variable=self.prompt_hint).pack(fill='x', pady=(2, 0))
        self.prompt.bind('<<Modified>>', self.prompt_changed)
        self.prompt.edit_modified(False)
        self._build_first_frame_card(columns.left)
        params = card(columns.left, t('params_card'))
        self.model_id = tk.StringVar(value=DEFAULT_MODEL_ID)
        self.model_label = tk.StringVar()
        model_row = ttk.Frame(params)
        model_row.pack(fill='x', pady=(0, 4))
        model_title = ttk.Label(model_row, text=t('param_model'), style='Muted.TLabel')
        model_title.pack(anchor='w', pady=(0, 6))
        self._ai_field_labels['model'] = model_title
        self.model_id.trace_add('write', lambda *_: self._clear_ai_field('model'))
        self.model_combo = WebSelect(model_row, textvariable=self.model_label, state='readonly')
        self.model_combo.pack(fill='x')
        desc_row = ttk.Frame(model_row)
        desc_row.pack(fill='x', pady=(4, 0))
        self.model_desc = tk.StringVar()
        self.model_desc_full = tk.StringVar()
        self._model_details_open = False
        self.model_details_btn = ttk.Button(desc_row, text=t('model_details'), style='Link.TButton',
                                            command=self._toggle_model_details)
        self.model_details_btn.pack(side='right', padx=(8, 0))
        label(desc_row, variable=self.model_desc).pack(side='left', fill='x', expand=True)
        self.model_desc_full_label = label(model_row, variable=self.model_desc_full)
        self.model_desc_full_label.pack(fill='x', pady=(4, 0))
        self.model_desc_full_label.pack_forget()
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
        for index, (name, var, kind) in enumerate([
                (t('param_duration'), self.duration, 'duration'),
                (t('param_resolution'), self.resolution, 'resolution'),
                (t('param_ratio'), self.ratio, 'ratio')]):
            row.columnconfigure(index, weight=1, uniform='params')
            field = ttk.Frame(row)
            field.grid(row=0, column=index, sticky='ew', padx=(0, 12 if index < 2 else 0))
            title = ttk.Label(field, text=name, style='Muted.TLabel')
            title.pack(anchor='w', pady=(0, 6))
            self._ai_field_labels[kind] = title
            var.trace_add('write', lambda *_, field_name=kind: self._clear_ai_field(field_name))
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
        label(params, variable=self.cost).pack(fill='x', pady=(4, 0))
        self._sync_model_widgets()
        self.update_cost()
        self.ai_plan_notice = ttk.Label(params, text=t('ai_plan_ready'), style='Badge.TLabel', wraplength=300)
        params.bind('<Configure>', lambda event: self.ai_plan_notice.configure(wraplength=max(100, event.width - 32)), add='+')
        from studio_ui import FONT
        self.credit_tip = tk.Frame(params, bg='#fff8eb', padx=12, pady=12, highlightbackground='#d8b579', highlightthickness=1)
        self.credit_tip_title = tk.StringVar(value=t('credits_insufficient'))
        ttk.Label(self.credit_tip, textvariable=self.credit_tip_title, font=(FONT, 10, 'bold'), background='#fff8eb', foreground='#513d20').pack(anchor='w', pady=(0, 4))
        self.credit_tip_text = tk.StringVar()
        tip_label = ttk.Label(self.credit_tip, textvariable=self.credit_tip_text, wraplength=320, justify='left', background='#fff8eb', foreground='#513d20')
        tip_label.pack(fill='x')
        self.credit_tip_suggest = tk.StringVar()
        suggest_label = ttk.Label(self.credit_tip, textvariable=self.credit_tip_suggest, font=(FONT, 10, 'bold'), wraplength=320, justify='left', background='#fff8eb', foreground='#3f2e15')
        suggest_label.pack(fill='x', pady=(6, 0))
        params.bind('<Configure>', lambda e: (tip_label.configure(wraplength=max(100, e.width - 56)), suggest_label.configure(wraplength=max(100, e.width - 56))), add='+')
        self.credit_tip_button = ttk.Button(self.credit_tip, text=t('topup_btn'), style='Accent.TButton', command=self.open_generate_topup)
        self.credit_tip_button.pack(anchor='center', pady=(10, 0))
        actions = Flow(params)
        actions.pack(fill='x', pady=(6, 0))
        self.generate_btn = ttk.Button(actions, text=t('generate_btn'), style='Accent.TButton', command=self.generate)
        self.update_cost()
        for variable in (self.model_id, self.duration, self.resolution, self.ratio):
            variable.trace_add('write', lambda *args: self._save_generate_form())
        self.stop_btn = ttk.Button(actions, text=t('stop_btn'), command=self.stop_wait, state='disabled')
        actions.schedule()
        self.bar = ttk.Progressbar(params, mode='indeterminate')
        # Right column: current config + save folder + collapsed advanced
        current = card(columns.right, t('current_card'))
        self.current_model_badge = tk.StringVar()
        label(current, variable=self.current_model_badge, style='Badge.TLabel').pack(fill='x')
        label(current, t('current_saved')).pack(fill='x', pady=(10, 0))
        self._update_current_model_badge()
        self.advanced_open = tk.BooleanVar(value=False)
        adv_card = card(columns.right, t('media_card'))
        self.advanced_toggle_btn = ttk.Button(adv_card, text=t('advanced_show'), style='Link.TButton',
                                              command=self._toggle_advanced)
        self.advanced_toggle_btn.pack(anchor='w')
        self.advanced_body = ttk.Frame(adv_card)
        label(self.advanced_body, t('media_card_desc')).pack(fill='x', pady=(0, 8))
        if not hasattr(self, 'media') or 'first_frame' not in getattr(self, 'media', {}):
            # first_frame StringVar is created in _build_first_frame_card (left column).
            self.media = getattr(self, 'media', {})
            self.media.setdefault('first_frame', tk.StringVar())
        for kind in ('last_frame', 'reference_image', 'reference_video', 'reference_audio'):
            ttk.Label(self.advanced_body, text=t('media_' + kind), style='Muted.TLabel').pack(anchor='w', pady=(0, 4))
            var = tk.StringVar()
            ttk.Entry(self.advanced_body, textvariable=var, width=12).pack(fill='x', pady=(0, 8))
            self.media[kind] = var
        ttk.Label(self.advanced_body, text=t('seed_label'), style='Muted.TLabel').pack(anchor='w', pady=(0, 4))
        self.seed = tk.StringVar()
        ttk.Entry(self.advanced_body, textvariable=self.seed, width=12).pack(fill='x')
        # Collapsed by default so Generate stays on the first screen.

        save_box = card(columns.right, t('save_card'))
        ttk.Label(save_box, text=t('output_folder'), style='Muted.TLabel').pack(anchor='w', pady=(0, 6))
        row = ttk.Frame(save_box)
        row.pack(fill='x')
        self.output = tk.StringVar(value=str(default_output_dir()))
        ttk.Entry(row, textvariable=self.output, width=12).pack(side='left', fill='x', expand=True)
        ttk.Button(row, text=t('browse'), command=self.choose_folder).pack(side='left', padx=(10, 0))
        label(save_box, t('cloud_note')).pack(fill='x', pady=(6, 0))

        logs = card(frame, t('progress_card'))
        self.log = text_area(logs, height=2, state='disabled')
        self.log.pack(fill='x')

    def _toggle_advanced(self):
        if self.advanced_open.get():
            self.advanced_body.pack_forget()
            self.advanced_open.set(False)
            self.advanced_toggle_btn.configure(text=t('advanced_show'))
        else:
            self.advanced_body.pack(fill='x', pady=(8, 0))
            self.advanced_open.set(True)
            self.advanced_toggle_btn.configure(text=t('advanced_hide'))

    def _build_first_frame_card(self, parent):
        """Primary I2V control under the prompt (local upload first, URL secondary)."""
        self.ff_card = card(parent, t('first_frame_label'))
        self.ff_tip = label(self.ff_card, t('first_frame_tip'))
        self.ff_tip.pack(fill='x', pady=(0, 8))
        self.ff_unsupported = label(self.ff_card, t('first_frame_unsupported'))
        self.ff_unsupported.pack(fill='x', pady=(0, 4))
        self.ff_unsupported.pack_forget()
        self.ff_controls = ttk.Frame(self.ff_card)
        self.ff_controls.pack(fill='x')
        row = ttk.Frame(self.ff_controls)
        row.pack(fill='x')
        self.ff_pick_btn = ttk.Button(row, text=t('first_frame_pick'), command=self._choose_first_frame)
        self.ff_pick_btn.pack(side='left')
        self.ff_clear_btn = ttk.Button(row, text=t('first_frame_remove'), style='Link.TButton',
                                       command=self._clear_first_frame)
        self.ff_clear_btn.pack(side='left', padx=(8, 0))
        self.ff_info = tk.StringVar(value='')
        self.ff_info_label = label(self.ff_controls, variable=self.ff_info)
        self.ff_info_label.pack(fill='x', pady=(6, 0))
        self.ff_info_label.pack_forget()
        self.ff_url_open = tk.BooleanVar(value=False)
        self.ff_url_toggle = ttk.Button(self.ff_controls, text=t('first_frame_url_summary'),
                                        style='Link.TButton', command=self._toggle_ff_url)
        self.ff_url_toggle.pack(anchor='w', pady=(8, 0))
        self.ff_url_body = ttk.Frame(self.ff_controls)
        self.media = getattr(self, 'media', {})
        self.media['first_frame'] = tk.StringVar()
        self._ff_local_name = None
        self._ff_url_var = tk.StringVar()
        self._ff_syncing_url = False
        ttk.Entry(self.ff_url_body, textvariable=self._ff_url_var, width=12).pack(fill='x')
        self._ff_url_var.trace_add('write', lambda *_: self._on_ff_url_typed())
        self.ff_wan_note = label(self.ff_controls, t('first_frame_wan_note'), style='Badge.TLabel')
        self.ff_wan_note.pack(fill='x', pady=(8, 0))
        self.ff_wan_note.pack_forget()
        self._refresh_first_frame_ui()

    def _toggle_ff_url(self):
        if self.ff_url_open.get():
            self.ff_url_body.pack_forget()
            self.ff_url_open.set(False)
            self.ff_url_toggle.configure(text=t('first_frame_url_summary'))
        else:
            self.ff_url_body.pack(fill='x', pady=(4, 0), after=self.ff_url_toggle)
            self.ff_url_open.set(True)
            self.ff_url_toggle.configure(text=t('first_frame_url_hide'))

    def _on_ff_url_typed(self):
        if self._ff_syncing_url:
            return
        url = self._ff_url_var.get().strip()
        self._ff_local_name = None
        self.media['first_frame'].set(url)
        self._refresh_first_frame_ui()

    def _choose_first_frame(self):
        path = filedialog.askopenfilename(
            title=t('first_frame_pick'),
            filetypes=[
                (t('media_first_frame'), '*.jpg *.jpeg *.png *.webp *.gif *.bmp'),
                ('*', '*.*'),
            ])
        if not path:
            return
        try:
            data_url = encode_local_image_as_data_url(path)
        except TaskError as exc:
            messagebox.showerror(t('check_input'), str(exc))
            return
        except Exception as exc:
            messagebox.showerror(t('check_input'), str(exc))
            return
        self._ff_local_name = Path(path).name
        self._ff_syncing_url = True
        self._ff_url_var.set('')
        self._ff_syncing_url = False
        self.media['first_frame'].set(data_url)
        self._refresh_first_frame_ui()

    def _clear_first_frame(self):
        self._ff_local_name = None
        self._ff_syncing_url = True
        self._ff_url_var.set('')
        self._ff_syncing_url = False
        self.media['first_frame'].set('')
        self._refresh_first_frame_ui()

    def _refresh_first_frame_ui(self):
        if not hasattr(self, 'ff_controls'):
            return
        try:
            spec = get_model(self.model_id.get())
        except Exception:
            return
        ok = model_allows_first_frame(spec)
        value = (self.media.get('first_frame').get() if self.media.get('first_frame') else '').strip()
        if ok:
            self.ff_unsupported.pack_forget()
            if not self.ff_tip.winfo_manager():
                self.ff_tip.pack(fill='x', pady=(0, 8), before=self.ff_controls)
            if not self.ff_controls.winfo_manager():
                self.ff_controls.pack(fill='x')
        else:
            self.ff_tip.pack_forget()
            self.ff_controls.pack_forget()
            if not self.ff_unsupported.winfo_manager():
                self.ff_unsupported.pack(fill='x', pady=(0, 4))
        if value.startswith('data:image/'):
            info = t('first_frame_local_fmt', self._ff_local_name) if self._ff_local_name else t('first_frame_embedded')
            self.ff_info.set(info)
            if not self.ff_info_label.winfo_manager():
                self.ff_info_label.pack(fill='x', pady=(6, 0), after=self.ff_pick_btn.master)
            self.ff_clear_btn.configure(state='normal')
        elif value:
            self.ff_info.set(value if len(value) < 64 else value[:61] + '…')
            if not self.ff_info_label.winfo_manager():
                self.ff_info_label.pack(fill='x', pady=(6, 0), after=self.ff_pick_btn.master)
            self.ff_clear_btn.configure(state='normal')
            # Keep URL details open when a non-data URL is present.
            if not value.startswith('data:') and not self.ff_url_open.get():
                self._toggle_ff_url()
            if not self._ff_url_var.get().strip() and not value.startswith('data:'):
                self._ff_syncing_url = True
                self._ff_url_var.set(value)
                self._ff_syncing_url = False
        else:
            self.ff_info.set('')
            self.ff_info_label.pack_forget()
            self.ff_clear_btn.configure(state='disabled')
        show_wan = ok and value.startswith('data:image/') and spec.family == 'wan'
        if show_wan:
            if not self.ff_wan_note.winfo_manager():
                self.ff_wan_note.pack(fill='x', pady=(8, 0))
        else:
            self.ff_wan_note.pack_forget()

    def _collect_generate_media(self):
        """Build media list for the current model; skip types the family rejects."""
        spec = get_model(self.model_id.get())
        allowed = set(allowed_media_types(spec.family))
        items = []
        for kind, var in self.media.items():
            url = var.get().strip()
            if not url or kind not in allowed:
                continue
            items.append({'type': kind, 'url': url})
        return items

    def _toggle_model_details(self):
        self._model_details_open = not getattr(self, '_model_details_open', False)
        if self._model_details_open:
            self.model_desc_full_label.pack(fill='x', pady=(4, 0))
            self.model_details_btn.configure(text=t('model_details_hide'))
        else:
            self.model_desc_full_label.pack_forget()
            self.model_details_btn.configure(text=t('model_details'))

    def build_history(self, frame):
        heading(frame, t('history_title'), t('history_desc'))
        table_card = card(frame, t('history_card'), t('history_card_desc'))
        self.history_table_card = table_card.master
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
        bind_command_or_control(self.tree, 'c', self.copy_selected_task_ids)
        bind_command_or_control(self.tree, 'a', self.select_all_task_rows)
        row = Flow(table_card)
        row.pack(fill='x', pady=(10, 0))
        self.resume_btn = ttk.Button(row, text=t('resume_selected'), style='Accent.TButton', command=self.resume_selected)
        ttk.Button(row, text=t('refresh'), command=self.refresh_history)
        ttk.Button(row, text=t('copy_selected_ids'), command=self.copy_selected_task_ids)
        ttk.Button(row, text=t('copy_all_ids'), command=self.copy_all_task_ids)
        ttk.Button(row, text=t('open_folder'), command=self.open_folder)
        row.schedule()
        self.history_credit_card = tk.Frame(frame, bg='#fff8eb', padx=12, pady=12, highlightbackground='#d8b579', highlightthickness=1)
        from studio_ui import FONT
        self.history_credit_text = tk.StringVar()
        self.history_credit_suggest = tk.StringVar()
        ttk.Label(self.history_credit_card, text=t('credits_insufficient'), font=(FONT, 10, 'bold'), foreground='#513d20', background='#fff8eb').pack(anchor='w')
        ttk.Label(self.history_credit_card, textvariable=self.history_credit_text, background='#fff8eb', wraplength=700).pack(fill='x', pady=6)
        ttk.Label(self.history_credit_card, textvariable=self.history_credit_suggest, font=(FONT, 10, 'bold'), background='#fff8eb', foreground='#3f2e15').pack(fill='x', pady=(0, 10))
        ttk.Button(self.history_credit_card, text=t('topup_btn'), style='Accent.TButton', command=lambda: self.open_generate_topup(self._history_credit_shortfall)).pack()
        self.tree.bind('<<TreeviewSelect>>', self._show_history_credit)
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
        # Traffic-light status (same states as web API Key tab pill).
        self.keys_key_pill = KeyStatusPill(frame, chrome=PAGE)
        self.keys_key_pill.pack(anchor='w', pady=(0, 12))
        columns = Columns(frame, self.scale)
        columns.pack(fill='x')

        oauth = card(columns.left, t('oauth_card'), t('oauth_card_desc'))
        label(oauth, t('oauth_for_beginners'), style='Badge.TLabel').pack(anchor='w', pady=(0, 10))
        self.keys_oauth_status = tk.StringVar(value='')
        self.keys_oauth_status_label = label(oauth, variable=self.keys_oauth_status)
        self.keys_oauth_status_label.pack(fill='x', pady=(0, 8))
        self.keys_oauth_hint = label(oauth, t('oauth_use_header'), style='Muted.TLabel')
        self.keys_oauth_hint.pack(anchor='w', pady=(0, 4))
        self.keys_oauth_login_btn = ttk.Button(oauth, text=t('oauth_login'), style='Accent.TButton',
                                               command=lambda: self.start_oauth_login('account'))
        # Not packed: header owns the primary Sign-in CTA (dedupe).
        self.keys_oauth_logout_btn = ttk.Button(oauth, text=t('oauth_logout'), style='Link.TButton',
                                                command=self.logout_oauth)
        # Shown when signed in; packed by _refresh_oauth_header.

        self.keys_advanced_open = tk.BooleanVar(value=False)
        self.keys_advanced_toggle = ttk.Button(columns.left, text=t('oauth_advanced_show'), style='Link.TButton',
                                               command=self._toggle_keys_advanced)
        self.keys_advanced_toggle.pack(anchor='w', pady=(12, 0))
        self.keys_advanced_body = ttk.Frame(columns.left)

        add = card(self.keys_advanced_body, t('add_card'), t('add_card_desc'))
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
        add_actions = ttk.Frame(add)
        add_actions.pack(fill='x')
        self.add_keys_btn = ttk.Button(add_actions, text=t('add_btn'), style='Accent.TButton', command=self.add_keys)
        self.add_keys_btn.pack(side='left')
        ttk.Button(add_actions, text=t('get_btn'), style='Link.TButton',
                   command=self.open_official_keys).pack(side='left', padx=(12, 0))
        self.keys_verify_pill = KeyStatusPill(add, textvariable=None, chrome=WHITE)
        self.keys_verify_pill.pack(anchor='w', pady=(12, 0))
        self.keys_verify_pill.set_state('soft', text=t('key_status_signin'), tooltip=t('key_chip_none'))
        pool = card(self.keys_advanced_body, t('pool_card'), t('pool_card_desc'))
        self.key_count = tk.StringVar(value=t('key_count', 0, count=0))
        label(pool, variable=self.key_count, style='Badge.TLabel').pack(anchor='w', pady=(0, 8))
        # Tip sits under the pool header so it wraps inside the card and stays visible.
        self.key_quota_tip = label(pool, t('key_quota_tip'), style='Badge.TLabel')
        self.key_quota_tip.pack(fill='x', pady=(0, 4))
        self.key_quota_tip.bind('<Button-1>', lambda *_: self.open_key_quota_tip())
        try:
            self.key_quota_tip.configure(cursor='hand2')
        except tk.TclError:
            pass
        self.key_quota_btn = ttk.Button(pool, text=t('key_quota_tip_btn'), style='Link.TButton',
                                        command=self.open_key_quota_tip)
        self.key_quota_btn.pack(anchor='w', pady=(0, 12))
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

        # Site key management is a secondary link (same as web Advanced), not a competing CTA.
        get = card(columns.right, t('get_card'), t('get_card_desc'))
        self.promo_label = label(get, promotion_text(), style='Badge.TLabel')
        self.get_key_btn = ttk.Button(get, text=t('get_btn'), style='Link.TButton', command=self.open_official_keys)
        self.get_key_btn.pack(anchor='w')
        self.refresh_promo()
        label(get, t('get_note')).pack(fill='x', pady=(12, 0))
        save = card(columns.right, t('local_card'))
        self.remember = tk.BooleanVar(value=False)
        ttk.Checkbutton(save, text=t('remember'), variable=self.remember, command=self.save_keys).pack(anchor='w')
        label(save, remember_badge_text(), style='Badge.TLabel').pack(anchor='w', pady=(10, 14))
        label(save, remember_note_text()).pack(fill='x')
        ttk.Separator(save).pack(fill='x', pady=16)
        label(save, t('no_repeat_note')).pack(fill='x')
        self._refresh_oauth_header()

    def prompt_changed(self, event=None):
        if not self.prompt.edit_modified():
            return
        self.prompt.edit_modified(False)
        self._clear_ai_field('prompt')
        self._save_generate_form()
        current_prompt = self.prompt.get('1.0', 'end-1c').strip()
        ai_prompt = getattr(self, '_ai_filled_prompt', '')
        if not current_prompt or (ai_prompt and SequenceMatcher(None, ai_prompt, current_prompt).ratio() < 0.25):
            self._ai_plan_source = False
            self._clear_ai_review()
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
        # Clean labels + parallel discount % / ids (web cselect data-discount style).
        specs = list(list_models())
        labels = [spec.label() for spec in specs]
        discounts = [int(spec.discount_percent() or 0) for spec in specs]
        ids = [spec.id for spec in specs]
        self._model_labels = {lab: mid for lab, mid in zip(labels, ids)}
        self.model_combo.configure(values=labels, discounts=discounts, ids=ids)
        current = get_model(self.model_id.get())
        # Prefer matching by id so a promo/label change does not desync the combo.
        current_label = current.label()
        if current_label not in self._model_labels:
            for lab, mid in self._model_labels.items():
                if mid == current.id:
                    current_label = lab
                    break
        self.model_label.set(current_label)
        self._update_model_description()

    def _on_model_picked(self):
        mid = ''
        if hasattr(self.model_combo, 'current_id'):
            mid = self.model_combo.current_id() or ''
        if not mid:
            mid = self._model_labels.get(self.model_label.get(), '')
        if not mid:
            mid = DEFAULT_MODEL_ID
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
        self._refresh_first_frame_ui()

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
        self.model_desc.set(spec.summary())
        if hasattr(self, 'model_desc_full'):
            self.model_desc_full.set(spec.description())

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
            credits = conversation_store.estimate_credits(value)
            self.cost.set(t('cost_estimate', f'{credits:,}'))
            self.sync_generate_credits(credits)
        except (ValueError, KeyError, TaskError):
            self.cost.set(t('cost_invalid'))
            self.sync_generate_credits(None)


    def _oauth_credits_unknown(self):
        session = getattr(self, 'oauth_session', None)
        if not (session or getattr(self, '_oauth_had_session', False) or getattr(self, '_login_only_keys', set())):
            return False
        credits = self._oauth_profile().get('credits')
        return (not self._is_oauth_logged_in() or isinstance(credits, bool)
                or not isinstance(credits, (int, float)))

    def _show_credit_relogin(self):
        conversation_store.write('intended_generate', dict(at=time.time(), form=self._generate_form()))
        self._credit_relogin_requested = True
        self.update_cost()
        self.status.set(t('credits_relogin'))
        # A valid token can still query userinfo; never auto-submit its result.
        if self._is_oauth_logged_in() and not getattr(self, '_credit_profile_busy', False):
            self._credit_profile_busy = True
            token = self.oauth_session['access_token']
            def refresh():
                try:
                    profile = oauth_desktop.profile_from_userinfo(oauth_desktop.fetch_userinfo(token))
                except Exception:
                    profile = None
                self.events.put(('credit_profile', (token, profile)))
            threading.Thread(target=refresh, daemon=True).start()

    def _generate_shortfall(self, need=None):
        if not getattr(self, 'oauth_session', None) or not self._is_oauth_logged_in():
            return None
        have = self._oauth_profile().get('credits')
        if isinstance(have, bool) or not isinstance(have, (int, float)):
            return None
        try:
            if need is None:
                need = conversation_store.estimate_credits(estimate_cost(self.duration.get(), self.resolution.get(), model_id=self.model_id.get()))
            if need > have:
                return need, have
        except (ValueError, KeyError, TaskError, TypeError):
            pass
        return None

    def sync_generate_credits(self, need):
        if not hasattr(self, 'credit_tip') or not hasattr(self, 'generate_btn'):
            return
        try:
            if not self.generate_btn.winfo_exists() or not self.credit_tip.winfo_exists():
                return
        except tk.TclError:
            return
        short = self._generate_shortfall(need) if need is not None else None
        self.generate_btn.configure(state='disabled' if self.busy or short else 'normal')
        if getattr(self, '_credit_relogin_requested', False) and self._oauth_credits_unknown():
            self.credit_tip_title.set(t('oauth_login'))
            self.credit_tip_text.set(t('credits_relogin'))
            self.credit_tip_suggest.set('')
            self.credit_tip_button.configure(text=t('oauth_login'), command=lambda: self.start_oauth_login('generate'))
            self.credit_tip.pack(fill='x', pady=(8, 6), before=self.generate_btn.master)
            return
        if hasattr(self, 'credit_tip_button'):
            self.credit_tip_button.configure(text=t('topup_btn'), command=self.open_generate_topup)
        if hasattr(self, 'credit_tip_title'):
            self.credit_tip_title.set(t('credits_insufficient'))
        if short:
            required, have = short
            amount = conversation_store.suggested_amount(required, have)
            self.credit_tip_text.set(t('credits_insufficient_tip', f'{have:,}', f'{required:,}'))
            if hasattr(self, 'credit_tip_suggest'):
                self.credit_tip_suggest.set(t('credits_insufficient_suggest', amount, f'{amount * 1000:,}'))
            self.credit_tip.pack(fill='x', pady=(8, 6), before=self.generate_btn.master)
            if not getattr(self, '_credit_gate_visible', False):
                analytics.notify_ui_event('credit_gate_shown', surface='generate', selection={5: 'preset-5', 20: 'preset-20', 50: 'preset-50'}.get(amount))
            self._credit_gate_visible = True
        else:
            self.credit_tip.pack_forget()
            self._credit_gate_visible = False

    def _generate_form(self):
        return dict(prompt=self.prompt.get('1.0', 'end-1c'), model=self.model_id.get(), duration=self.duration.get(), resolution=self.resolution.get(), ratio=self.ratio.get())

    def _save_generate_form(self):
        form = self._generate_form()
        conversation_store.write('generate_form', form)
        if conversation_store.intended() is not None:
            conversation_store.write('intended_generate', dict(at=time.time(), form=form))

    def _restore_generate_form(self):
        form = conversation_store.intended() or conversation_store.read('generate_form')
        if not isinstance(form, dict):
            return
        self.prompt.delete('1.0', 'end')
        self.prompt.insert('1.0', form.get('prompt', ''))
        for field in ('model', 'duration', 'resolution', 'ratio'):
            if field in form:
                getattr(self, 'model_id' if field == 'model' else field).set(form[field])
        self._sync_model_widgets()
        self.update_cost()

    def clear_generate_prompt(self):
        analytics.notify_ui_event('prompt_clear', surface='generate', reason='button')
        self.prompt.delete('1.0', 'end')
        self._clear_ai_review()
        self._save_generate_form()

    def open_generate_topup(self, shortfall=None):
        conversation_store.write('intended_generate', dict(at=time.time(), form=self._generate_form()))
        if shortfall is None:
            self.open_billing('generate_tip')
        else:
            self.open_billing('generate_tip', shortfall=shortfall)

    def _toggle_keys_advanced(self):
        if not hasattr(self, 'keys_advanced_body'):
            return
        if self.keys_advanced_open.get():
            self.keys_advanced_body.pack_forget()
            self.keys_advanced_open.set(False)
            self.keys_advanced_toggle.configure(text=t('oauth_advanced_show'))
        else:
            self.keys_advanced_body.pack(fill='x', pady=(8, 0))
            self.keys_advanced_open.set(True)
            self.keys_advanced_toggle.configure(text=t('oauth_advanced_hide'))

    def _load_oauth_session(self):
        """Restore a non-expired OAuth session (token + display profile, no API key)."""
        self._oauth_had_session = oauth_desktop.session_path().is_file()
        self.oauth_session = oauth_desktop.load_session()
        self._oauth_had_session = self._oauth_had_session or bool(self.oauth_session)
        self._refresh_oauth_header()

    def _oauth_profile(self):
        session = self.oauth_session or {}
        profile = session.get('profile') if isinstance(session, dict) else None
        return profile if isinstance(profile, dict) else {}

    def _is_oauth_logged_in(self):
        session = getattr(self, 'oauth_session', None)
        if not session or not session.get('access_token'):
            return False
        expires_at = session.get('expires_at')
        if expires_at is not None:
            try:
                import time as _time
                if _time.time() >= float(expires_at):
                    return False
            except (TypeError, ValueError):
                return False
        return True

    def _refresh_oauth_header(self):
        root = getattr(self, 'root', None)
        if root is not None:
            timer = getattr(self, '_oauth_expiry_timer', None)
            if timer:
                root.after_cancel(timer)
                self._oauth_expiry_timer = None
            if not getattr(self, '_oauth_focus_bound', False):
                root.bind('<FocusIn>', lambda event: self._refresh_oauth_header(), add='+')
                self._oauth_focus_bound = True
            expiry = (self.oauth_session or {}).get('expires_at')
            if self._is_oauth_logged_in() and expiry is not None:
                delay = max(1, min(2147483647, int((float(expiry) - time.time()) * 1000) + 1))
                self._oauth_expiry_timer = root.after(delay, self._refresh_oauth_header)
        logged_in = self._is_oauth_logged_in()
        profile = self._oauth_profile() if logged_in else {}
        name = (profile.get('name') or profile.get('email') or '').strip()
        credits = profile.get('credits')
        if hasattr(self, 'oauth_account_var'):
            # Name-only chip; credits live on the credits chip (official console pattern).
            if logged_in:
                self.oauth_account_var.set(name or 'PowerTokens')
            else:
                self.oauth_account_var.set('')
        if hasattr(self, 'oauth_credits_var'):
            # Match web renderCredits: balance → "Credits N · Top up"; else "Credits · Top up".
            if logged_in and credits is not None:
                try:
                    shown = t('credits_balance', '{:,}'.format(int(credits)))
                except (TypeError, ValueError):
                    shown = t('credits_balance', credits)
                self.oauth_credits_var.set(shown)
            else:
                self.oauth_credits_var.set(t('credits_btn'))
        # Header account cluster — forget + re-pack right-to-left so order stays
        # name · credits · key-pill · login/out (matches web .account-cluster).
        try:
            for w in (getattr(self, 'header_oauth_btn', None),
                      getattr(self, 'header_logout_btn', None),
                      getattr(self, 'header_key_pill', None),
                      getattr(self, 'oauth_credits_label', None),
                      getattr(self, 'oauth_account_label', None)):
                if w is not None:
                    try:
                        w.pack_forget()
                    except tk.TclError:
                        pass
            # 1) rightmost: Sign-in pill or Sign out link
            if logged_in:
                if hasattr(self, 'header_logout_btn'):
                    self.header_logout_btn.pack(side='right')
            elif hasattr(self, 'header_oauth_btn'):
                if self.oauth_busy:
                    self.header_oauth_btn.configure(text=t('oauth_busy'), state='disabled')
                else:
                    self.header_oauth_btn.configure(text=t('oauth_login'), state='normal')
                self.header_oauth_btn.pack(side='right')
            # 2) key status pill — only when not soft (PR #13 / web hide soft)
            # packed later by _refresh_key_status after this refresh; keep forgotten here if soft
            # 3) credits chip always (web billing-chip)
            if hasattr(self, 'oauth_credits_label'):
                self.oauth_credits_label.pack(side='right', padx=(0, 8))
            # 4) user name chip when signed in
            if logged_in and name and hasattr(self, 'oauth_account_label'):
                self.oauth_account_label.pack(side='right', padx=(0, 8))
        except tk.TclError:
            pass
        # Keys tab OAuth card
        if hasattr(self, 'keys_oauth_status'):
            if self.oauth_busy:
                self.keys_oauth_status.set(t('oauth_busy'))
            elif logged_in:
                parts = [t('oauth_signed_in', name or 'PowerTokens')]
                if credits is not None:
                    parts.append(t('oauth_credits', credits))
                self.keys_oauth_status.set(' · '.join(parts))
            else:
                self.keys_oauth_status.set('')
        if hasattr(self, 'keys_oauth_login_btn'):
            # Deduped: primary Sign in lives in the header account cluster.
            try:
                self.keys_oauth_login_btn.pack_forget()
            except tk.TclError:
                pass
        if hasattr(self, 'keys_oauth_hint'):
            try:
                if logged_in or self.oauth_busy:
                    self.keys_oauth_hint.pack_forget()
                else:
                    self.keys_oauth_hint.configure(text=t('oauth_use_header'))
                    if not self.keys_oauth_hint.winfo_manager():
                        self.keys_oauth_hint.pack(anchor='w', pady=(0, 4))
            except tk.TclError:
                pass
        if hasattr(self, 'keys_oauth_logout_btn'):
            try:
                if logged_in:
                    if not self.keys_oauth_logout_btn.winfo_manager():
                        self.keys_oauth_logout_btn.pack(anchor='w', pady=(8, 0))
                else:
                    self.keys_oauth_logout_btn.pack_forget()
            except tk.TclError:
                pass
        # Re-apply key-pill pack (soft stays hidden) after account-cluster re-pack.
        if hasattr(self, '_refresh_key_status'):
            self._refresh_key_status()

        self.update_cost()

    def _boot_analytics(self):
        """Start the anonymous session once per process. Never on tab switches."""
        if getattr(self, '_analytics_booted', False):
            return
        self._analytics_booted = True
        try:
            analytics.boot(app_version=VERSION)
        except Exception:
            return

    def _map_login_error(self, code):
        text = str(code or '').strip().lower().replace('-', '_').replace(' ', '_')
        if text in ('network', 'http_error'):
            return 'NETWORK'
        if text == 'timeout':
            return 'TIMEOUT'
        if text == 'access_denied':
            return 'CANCELLED'
        if text in (
            'state_mismatch', 'missing_code', 'invalid_grant', 'invalid_request',
            'unauthorized_client', 'userinfo_failed', 'token_exchange_failed',
        ) or 'auth' in text:
            return 'AUTH'
        return 'UNKNOWN'

    def start_oauth_login(self, source='other'):
        if self.oauth_busy:
            return
        try:
            analytics.notify_login_start(source)
        except Exception:
            pass
        self.oauth_busy = True
        self._refresh_oauth_header()
        self.status.set(t('oauth_busy'))

        def worker():
            try:
                result = oauth_desktop.login()
                self.events.put(('oauth_ok', result))
            except oauth_desktop.OAuthError as exc:
                self.events.put(('oauth_fail', '%s|%s' % (exc.code, str(exc))))
            except Exception as exc:
                self.events.put(('oauth_fail', 'error|%s' % exc))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_oauth_result(self, result):
        self.oauth_busy = False
        oauth_desktop.save_session(result)
        self.oauth_session = oauth_desktop.load_session() or {
            'access_token': result.get('access_token'),
            'expires_at': result.get('expires_at'),
            'profile': oauth_desktop.profile_from_userinfo(result.get('userinfo') or {}),
        }
        key = result.get('key') or oauth_desktop.normalize_api_key((result.get('userinfo') or {}).get('key'))
        key_applied = False
        if key:
            # OAuth userinfo key is already authenticated — apply without GET /v1/models
            # (matches web: no extra validate friction on the Sign-in path).
            issued = getattr(self, '_login_only_keys', set())
            # Untagged legacy keys may be pasted; never infer their source.
            if key not in self.keys:
                issued.add(hashlib.sha256(key.encode()).hexdigest())
            self._login_only_keys = issued
            self.keys = list(dict.fromkeys([key] + list(self.keys)))
            # Beginner path: persist when this OS can store secrets
            # (DPAPI on Windows, login Keychain on macOS).
            if secret_store.storage_kind() and hasattr(self, 'remember') and not self.remember.get():
                self.remember.set(True)
            self.sync_keys()
            self.save_keys()
            key_applied = True
        self._refresh_oauth_header()
        try:
            self.tabs.select(0)  # Generate tab
        except tk.TclError:
            pass
        try:
            analytics.notify_login_success(key_applied=key_applied)
        except Exception:
            pass
        self._oauth_had_session = True
        import payment_ui
        if payment_ui.resume_topup(self):
            self.status.set(t('oauth_ok'))
            return
        if key_applied:
            self.status.set(t('oauth_ok'))
            messagebox.showinfo(t('oauth_login'), t('oauth_ok'))
        else:
            self.status.set(t('oauth_ok_nokey'))
            messagebox.showinfo(t('oauth_login'), t('oauth_ok_nokey'))
            try:
                self.tabs.select(5)
                if hasattr(self, 'keys_advanced_open') and not self.keys_advanced_open.get():
                    self._toggle_keys_advanced()
            except tk.TclError:
                pass

    def _oauth_fail(self, payload):
        self.oauth_busy = False
        self._refresh_oauth_header()
        code, _, detail = str(payload).partition('|')
        try:
            analytics.notify_login_failed(self._map_login_error(code), auth_reason=code)
        except Exception:
            pass
        msg = detail or code
        self.status.set(t('oauth_fail', msg))
        messagebox.showerror(t('oauth_login'), t('oauth_fail', msg))

    def logout_oauth(self):
        if self.oauth_busy:
            return
        try:
            analytics.notify_logout()
        except Exception:
            pass
        issued = getattr(self, '_login_only_keys', set())
        self.keys = [k for k in self.keys if hashlib.sha256(k.encode()).hexdigest() not in issued]
        self._login_only_keys = set()
        self.sync_keys()
        self.save_keys()
        oauth_desktop.logout()
        self.oauth_session = None
        self._oauth_had_session = False
        try:
            analytics.clear_account()
        except Exception:
            pass
        self._refresh_oauth_header()
        self.status.set(t('oauth_logged_out'))

    def open_key_quota_tip(self):
        self.open_billing('quota_tip')

    def open_official_keys(self):
        try:
            if not webbrowser.open(official_key_url()):
                messagebox.showinfo(t('official_site'), official_key_url())
        except OSError:
            messagebox.showinfo(t('official_site'), official_key_url())

    def open_billing(self, source='credits_chip', shortfall=None):
        """Open the in-app plan picker."""
        try:
            analytics.notify_billing_click(source)
        except Exception:
            pass
        import payment_ui
        short = shortfall or (self._generate_shortfall() if source == 'generate_tip' else None)
        amount = conversation_store.suggested_amount(*short) if short else 5
        payment_ui.show(self, suggested_amount=amount)

    def choose_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.output.set(folder)

    def _refresh_key_status(self, kind=None):
        """Drive header + Keys-tab traffic lights (soft/ok/warn/bad), matching web."""
        if kind is None:
            if getattr(self, 'key_verify_busy', False):
                kind = 'warn'
            elif not self.keys:
                kind = 'soft'
            elif self.key_status_kind == 'bad':
                kind = 'bad'
            else:
                kind = 'ok'
        self.key_status_kind = kind
        if kind == 'ok':
            short = t('key_status_ok')
            tip = t('key_chip_ok')
            # StringVar keeps the count badge for language-switch / pool tests.
            self.key_badge.set(t('badge_keys', len(self.keys), count=len(self.keys)) if self.keys else short)
            pill_text = short
        elif kind == 'warn':
            short = t('key_status_verifying') if getattr(self, 'key_verify_busy', False) else t('key_status_check')
            tip = t('key_chip_verifying') if getattr(self, 'key_verify_busy', False) else t('key_chip_auth')
            self.key_badge.set(short)
            pill_text = short
        elif kind == 'bad':
            short = t('key_status_check')
            tip = t('key_chip_auth')
            self.key_badge.set(short)
            pill_text = short
        else:
            short = t('key_status_signin')
            tip = t('key_chip_none')
            self.key_badge.set(t('badge_no_key'))
            pill_text = short
        for pill in (getattr(self, 'header_key_pill', None), getattr(self, 'keys_key_pill', None)):
            if pill is None:
                continue
            try:
                pill.set_state(kind, text=pill_text, tooltip=tip)
            except tk.TclError:
                pass
        # Soft "Sign in" pill duplicates header OAuth CTA — hide until there is a real key state.
        header_pill = getattr(self, 'header_key_pill', None)
        if header_pill is not None:
            try:
                if kind == 'soft':
                    header_pill.pack_forget()
                elif not header_pill.winfo_manager():
                    # side=right: first packed is rightmost; place pill just left of login/out.
                    anchor = None
                    for cand in (getattr(self, 'header_oauth_btn', None),
                                 getattr(self, 'header_logout_btn', None)):
                        if cand is not None and cand.winfo_manager():
                            anchor = cand
                            break
                    if anchor is not None:
                        header_pill.pack(side='right', padx=(0, 10), after=anchor)
                    else:
                        header_pill.pack(side='right', padx=(0, 10))
            except tk.TclError:
                pass
        verify_pill = getattr(self, 'keys_verify_pill', None)
        if verify_pill is not None:
            try:
                verify_pill.set_state(kind, text=pill_text, tooltip=tip)
            except tk.TclError:
                pass

    def sync_keys(self):
        self.key_list.delete(0, 'end')
        for i, key in enumerate(self.keys):
            self.key_list.insert('end', '%d. ****%s' % (i + 1, key[-4:]))
        self.key_count.set(t('key_count', len(self.keys), count=len(self.keys)))
        if self.key_status_kind == 'bad':
            # Keep red after a failed verify until a successful save clears it
            # (even when the pool is still empty — invalid paste must stay red).
            pass
        elif self.keys:
            self.key_status_kind = 'ok'
        else:
            self.key_status_kind = 'soft'
        self._refresh_key_status(self.key_status_kind)
        if self.keys:
            self.key_list.selection_set(0)

    def _set_key_verify_busy(self, busy):
        self.key_verify_busy = bool(busy)
        if hasattr(self, 'add_keys_btn'):
            if busy:
                self.add_keys_btn.configure(text=t('key_verifying'), state='disabled')
            else:
                self.add_keys_btn.configure(text=t('add_btn'), state='normal')
        if busy:
            self.key_status_kind = 'warn'
            self._refresh_key_status('warn')
        # When clearing busy, _keys_verify_done (or caller) sets the final kind.

    def _verify_fail_message(self, result):
        kind = (result or {}).get('kind')
        if kind == 'NETWORK':
            return t('key_verify_fail_network')
        if kind == 'QUOTA':
            return t('key_verify_fail_quota')
        if kind == 'AUTH':
            return t('key_verify_fail_auth')
        detail = str((result or {}).get('detail') or '').strip()
        if detail:
            return t('key_verify_fail_detail', detail)
        return t('key_verify_fail')

    def add_keys(self):
        """Advanced paste: live-verify (GET /v1/models) before writing the pool — same as web."""
        if getattr(self, 'key_verify_busy', False):
            return
        try:
            keys = parse_keys(self.key_input.get())
        except ValueError as exc:
            try:
                analytics.notify_key_verify_failed('VALIDATION')
            except Exception:
                pass
            messagebox.showerror(t('key_format_title'), str(exc))
            return
        # A duplicate paste is still explicit manual ownership.
        issued = getattr(self, '_login_only_keys', set())
        self._login_only_keys = issued - {hashlib.sha256(k.encode()).hexdigest() for k in keys if k in self.keys}
        self.save_keys()
        pending = [k for k in keys if k not in self.keys]
        if not pending:
            self.key_input.set('')
            self.status.set(t('keys_added_status', len(self.keys), count=len(self.keys)))
            return
        self._set_key_verify_busy(True)
        self.status.set(t('key_verifying'))

        def worker():
            ok_keys = []
            fails = []
            for key in pending:
                try:
                    result = verify_key(key)
                except Exception as exc:
                    result = {'ok': False, 'kind': 'NETWORK', 'detail': str(exc), 'http': None}
                if result.get('ok'):
                    ok_keys.append(key)
                else:
                    fails.append({'key': key, 'result': result})
            self.events.put(('keys_verify_done', {'ok': ok_keys, 'fails': fails}))

        threading.Thread(target=worker, daemon=True).start()

    def _keys_verify_done(self, payload):
        self._set_key_verify_busy(False)
        ok_keys = list((payload or {}).get('ok') or [])
        fails = list((payload or {}).get('fails') or [])
        try:
            if ok_keys:
                analytics.notify_key_verified()
            if fails:
                kind = ((fails[0] or {}).get('result') or {}).get('kind') or 'UNKNOWN'
                analytics.notify_key_verify_failed(kind)
        except Exception:
            pass
        if ok_keys:
            self._login_only_keys = getattr(self, '_login_only_keys', set()) - {hashlib.sha256(k.encode()).hexdigest() for k in ok_keys}
            self.keys = list(dict.fromkeys(list(self.keys) + ok_keys))
            self.key_input.set('')
            self.key_status_kind = 'ok'
            self.sync_keys()
            self.save_keys()
            hints = ', '.join('…' + k[-4:] for k in ok_keys)
            self.status.set(t('key_verify_ok', hints) + ' · ' + t('keys_added_status', len(self.keys), count=len(self.keys)))
            self._refresh_key_status('ok')
        if fails:
            # Reject invalid keys — never write them. Surface the first failure clearly.
            first = fails[0]
            msg = self._verify_fail_message(first.get('result'))
            if len(fails) > 1:
                msg = t('key_verify_fail_multi', len(fails), msg)
            self.status.set(msg)
            # Red light even when some other keys already saved — highlight the failure.
            if not ok_keys:
                self.key_status_kind = 'bad'
                self._refresh_key_status('bad')
            messagebox.showerror(t('key_verify_title'), msg)
        elif not ok_keys:
            self.key_status_kind = 'bad'
            self._refresh_key_status('bad')
            self.status.set(t('key_verify_fail'))

    def remove_key(self):
        selected = self.key_list.curselection()
        if selected:
            self.keys.pop(selected[0])
            self.sync_keys()
            self.save_keys()

    def save_keys(self):
        path = DATA_DIR / 'keys.json'
        try:
            # Only fingerprints are stored beside the OS-protected key pool.
            live = {hashlib.sha256(k.encode()).hexdigest() for k in self.keys}
            self._login_only_keys = getattr(self, '_login_only_keys', set()) & live
            (DATA_DIR / 'login-only-keys.json').write_text(json.dumps(sorted(self._login_only_keys)))
            if self.remember.get():
                secret_store.write_remembered_keys(path, self.keys)
            else:
                secret_store.clear_remembered_keys(path)
        except Exception:
            self.remember.set(False)
            messagebox.showerror(t('save_failed_title'), t('save_failed_body'))

    def load_keys(self):
        try:
            self._login_only_keys = set(json.loads((DATA_DIR / 'login-only-keys.json').read_text()))
        except (OSError, ValueError, TypeError):
            self._login_only_keys = set()
        path = DATA_DIR / 'keys.json'
        if path.exists():
            try:
                self.keys = secret_store.read_remembered_keys(path)
                self.remember.set(True)
            except Exception:
                messagebox.showwarning(t('load_failed_title'), t('load_failed_body'))
        self.sync_keys()

    def start_ai_plan(self):
        self._ai_plan_visit = True
        analytics.notify_ai_plan_click()
        self.chat.start_from_generate(self.prompt.get('1.0', 'end-1c'))

    def _ai_snapshot(self):
        return dict(prompt=self.prompt.get('1.0', 'end-1c'), model=self.model_id.get(),
                    duration=self.duration.get(), resolution=self.resolution.get(), ratio=self.ratio.get())

    def _clear_ai_field(self, field):
        if getattr(self, '_applying_ai_plan', False):
            return
        getattr(self, '_ai_highlights', set()).discard(field)
        if field == 'prompt' and hasattr(self, '_ai_prompt_background'):
            self.prompt.configure(background=self._ai_prompt_background)
        title = getattr(self, '_ai_field_labels', {}).get(field)
        if title is not None:
            title.configure(style='Muted.TLabel')

    def _clear_ai_review(self):
        for field in list(getattr(self, '_ai_highlights', set())):
            self._clear_ai_field(field)
        if hasattr(self, 'ai_plan_notice'):
            self.ai_plan_notice.pack_forget()

    def apply_chat_plan(self, plan):
        """Fill Generate form from a Chat plan and switch to the Generate tab."""
        if not plan or not str(plan.get('prompt') or '').strip():
            return
        if self.prompt_timer:
            self.root.after_cancel(self.prompt_timer)
            self.prompt_timer = None
        before = self._ai_snapshot()
        self._clear_ai_review()
        self._applying_ai_plan = True
        try:
            self.prompt.delete('1.0', 'end')
            self.prompt.insert('1.0', str(plan['prompt']).strip())
            self.prompt.edit_modified(False)
            model = plan.get('model')
            if model and hasattr(self, 'model_id'):
                try:
                    mid = resolve_model_id(model)
                except KeyError:
                    mid = None
                if mid:
                    self.model_id.set(mid)
                    self._sync_model_widgets(preserve=True)
            if plan.get('duration') is not None:
                self.duration.set(str(plan['duration']))
            if plan.get('resolution'):
                self.resolution.set(str(plan['resolution']))
            if plan.get('ratio'):
                self.ratio.set(str(plan['ratio']))
            # Snap to the selected model's allowed params.
            if hasattr(self, '_sync_model_widgets'):
                self._sync_model_widgets(preserve=True)
            self.update_cost()
            if self.auto_prompt.get():
                self.apply_prompt()
        finally:
            self._applying_ai_plan = False
        self._ai_highlights = {field for field, value in self._ai_snapshot().items() if before[field] != value}
        for field in self._ai_highlights:
            if field == 'prompt' and hasattr(self, '_ai_prompt_background'):
                self.prompt.configure(background='#fff1c2')
            title = getattr(self, '_ai_field_labels', {}).get(field)
            if title is not None:
                title.configure(style='AIPlan.TLabel')
        self._ai_filled_prompt = self.prompt.get('1.0', 'end-1c').strip()
        self._ai_plan_source = bool(getattr(self, '_ai_plan_visit', False))
        analytics.notify_ai_plan_applied('ai_plan' if self._ai_plan_source else 'chat')
        if hasattr(self, 'ai_plan_notice'):
            self.ai_plan_notice.pack(fill='x', pady=(6, 4), before=self.generate_btn.master)
        try:
            self.tabs.select(0)
        except tk.TclError:
            pass

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
        self._clear_ai_review()
        if self._generate_shortfall():
            analytics.notify_ui_event('credit_gate_blocked', surface='generate')
            self.update_cost()
            return
        if self.busy:
            return
        if not self.ensure_submit_credentials():
            return
        try:
            recognized = self.apply_prompt()
            if self._generate_shortfall():
                analytics.notify_ui_event('credit_gate_blocked', surface='generate')
                self.update_cost()
                return
            if recognized['warnings']:
                raise TaskError(t('warning_sep').join(recognized['warnings']))
            media = self._collect_generate_media()
            request = payload(self.prompt.get('1.0', 'end'), self.duration.get(), self.resolution.get(),
                self.ratio.get(), media,
                self.seed.get(), model=self.model_id.get())
            has_ff = any(isinstance(item, dict) and item.get('type') == 'first_frame' and item.get('url') for item in (media or []))
            request['_vs_input_mode'] = 'i2v' if has_ff else 't2v'
            if getattr(self, '_ai_plan_source', False):
                request['_vs_source'] = 'ai_plan'
            output = self.new_output()
        except Exception as exc:
            messagebox.showerror(t('check_input'), str(exc))
            return
        conversation_store.clear('intended_generate')
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
        if not self.ensure_submit_credentials():
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
        if not self.bar.winfo_manager():
            self.bar.pack(fill='x', pady=(8, 0))
        self.bar.start(12)
        tab_map = {'generate': 0, 'chat': 1, 'compare': 2, 'batch': 3, 'history': 4, 'keys': 5}
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


    def _start_update_check(self):
        """Background GitHub check, or a fixture release for previews (`VS_UPDATE_FIXTURE=1`)."""
        if self._update_dismissed_session:
            return
        data_dir = DATA_DIR
        current_version = VERSION
        platform = sys.platform

        def worker():
            info = None
            try:
                if update_check.use_update_fixture():
                    info = update_check.fixture_release(platform=platform)
                else:
                    state = update_check.load_state(data_dir)
                    if update_check.should_check(state):
                        info = update_check.fetch_latest(current_version, platform)
                        update_check.mark_checked(state, data_dir=data_dir, release=info)
                    else:
                        info = update_check.cached_release(state)
                if info is None or not update_check.is_newer(info.tag, current_version):
                    return
                state = update_check.load_state(data_dir)
                if not update_check.should_prompt(state, info.tag):
                    return
                self.events.put(('update_available', info))
            except Exception:
                return

        threading.Thread(target=worker, daemon=True).start()

    def _build_update_banner(self, parent):
        from studio_ui import Card
        surface = Card(parent)
        box = surface.body
        style = ttk.Style()
        style.configure('Update.TFrame', background='#eaf7f3')
        style.configure('Update.TLabel', background='#eaf7f3', foreground=INK)
        box.configure(style='Update.TFrame')
        surface.bind('<Configure>', lambda event: surface.itemconfigure('surface', fill='#eaf7f3'), add='+')
        self.update_banner = surface
        header = ttk.Frame(box, style='Update.TFrame')
        header.pack(fill='x')
        label(header, '↑  Video Studio', style='Update.TLabel').pack(side='left', padx=(0, 16))
        self.update_message = tk.StringVar(value='')
        label(header, variable=self.update_message, style='Update.TLabel').pack(side='left', fill='x', expand=True, padx=(0, 12))
        actions = ttk.Frame(header, style='Update.TFrame')
        actions.pack(side='right')
        self.update_progress_row = ttk.Frame(box, style='Update.TFrame')
        self.update_progress_bar = ttk.Progressbar(
            self.update_progress_row, length=100, maximum=100,
            style='Horizontal.TProgressbar', value=getattr(self, '_update_progress_value', 0))
        self.update_progress_bar.pack(fill='x')
        self.update_download_btn = ttk.Button(actions, text=t('update_download'), style='Accent.TButton',
                                              command=self._download_update)
        self.update_later_btn = ttk.Button(actions, text=t('update_later'), style='Link.TButton',
                                           command=self._dismiss_update)
        self.update_retry_btn = ttk.Button(actions, text=t('update_retry'), style='Accent.TButton',
                                           command=self._download_update)
        self.update_page_btn = ttk.Button(actions, text=t('update_open_page'), style='Link.TButton',
                                          command=self._open_update_page)
        self.update_quit_btn = ttk.Button(actions, text=t('update_quit_and_open'), style='Accent.TButton',
                                          command=self._quit_and_run_update)
        self.update_open_btn = ttk.Button(actions, text=t('update_open_installer'), style='Accent.TButton',
                                          command=self._open_downloaded_package)
        self.update_reveal_btn = ttk.Button(actions, text=t('update_reveal'), style='Link.TButton',
                                            command=self._reveal_download)
        self._update_action_buttons = (
            self.update_download_btn, self.update_later_btn, self.update_retry_btn,
            self.update_page_btn, self.update_quit_btn, self.update_open_btn, self.update_reveal_btn,
        )

    def _update_banner_visible(self):
        banner = getattr(self, 'update_banner', None)
        if banner is None:
            return False
        try:
            return bool(banner.winfo_manager())
        except tk.TclError:
            return False

    def _pack_update_banner(self):
        banner = getattr(self, 'update_banner', None)
        if banner is None:
            return
        try:
            if hasattr(self, 'tabs') and self.tabs.winfo_manager():
                banner.pack(fill='x', padx=28, pady=(0, 8), before=self.tabs)
            elif not banner.winfo_manager():
                banner.pack(fill='x', padx=28, pady=(0, 8))
        except tk.TclError:
            return

    def _pack_update_actions(self, *buttons):
        for button in getattr(self, '_update_action_buttons', ()):
            try:
                button.pack_forget()
            except tk.TclError:
                pass
        first = True
        for button in buttons:
            try:
                button.configure(state='normal')
                if first:
                    button.pack(side='left')
                    first = False
                else:
                    button.pack(side='left', padx=(12, 0))
            except tk.TclError:
                continue

    def _refresh_update_banner_copy(self):
        offer = self._update_offer
        if offer is None or not hasattr(self, 'update_message'):
            return
        phase = self._update_phase or 'offer'
        if hasattr(self, 'update_progress_bar'):
            if phase == 'downloading':
                self.update_progress_row.pack(fill='x', pady=(8, 0))
            else:
                self.update_progress_row.pack_forget()
        version = offer.version or offer.tag
        current = VERSION
        if phase == 'downloading':
            name = offer.asset_name or version
            text = t('update_downloading', name)
            if self._update_progress_text:
                text = text + '  ' + self._update_progress_text
            self.update_message.set(text)
            self._pack_update_actions(self.update_later_btn)
            try:
                self.update_later_btn.configure(state='disabled')
            except tk.TclError:
                pass
            return
        if phase == 'ready':
            if sys.platform == 'darwin':
                self.update_message.set(t('update_ready_mac'))
                self._pack_update_actions(self.update_open_btn, self.update_reveal_btn, self.update_later_btn)
            else:
                self.update_message.set(t('update_ready_win'))
                self._pack_update_actions(self.update_quit_btn, self.update_reveal_btn, self.update_later_btn)
            return
        if phase == 'fail':
            if self._update_fail_kind == 'hash':
                self.update_message.set(t('update_hash_fail'))
            else:
                self.update_message.set(t('update_fail'))
            buttons = [self.update_retry_btn, self.update_page_btn, self.update_later_btn]
            if not offer.asset_url:
                buttons = [self.update_page_btn, self.update_later_btn]
            self._pack_update_actions(*buttons)
            return
        self.update_message.set(t('update_available', version, current))
        if offer.asset_url:
            self._pack_update_actions(self.update_download_btn, self.update_later_btn)
        else:
            self._pack_update_actions(self.update_download_btn, self.update_later_btn)

    def _show_update_offer(self, info, restore=False):
        if info is None or self._update_dismissed_session:
            return
        if not restore or self._update_offer is None:
            self._update_offer = info
        if not restore or not self._update_phase:
            self._update_phase = 'offer'
        if not hasattr(self, 'update_banner'):
            return
        self._refresh_update_banner_copy()
        self._pack_update_banner()

    def _dismiss_update(self):
        self._update_dismissed_session = True
        offer = self._update_offer
        if offer is not None:
            try:
                update_check.mark_dismissed(offer.tag, data_dir=DATA_DIR)
            except Exception:
                pass
        banner = getattr(self, 'update_banner', None)
        if banner is not None:
            try:
                banner.pack_forget()
            except tk.TclError:
                pass

    def _open_update_page(self):
        offer = self._update_offer
        url = update_check.offer_page_url(offer)
        if offer is not None and not offer.asset_url and offer.html_url:
            # Linux / unknown: official public page first; release page is already a fine fallback
            # if the public page is unavailable. Prefer the documented public download page.
            url = update_check.PUBLIC_DOWNLOAD_PAGE
        try:
            webbrowser.open(url)
        except Exception:
            pass

    def _download_update(self):
        offer = self._update_offer
        if offer is None:
            return
        if not offer.asset_url:
            self._open_update_page()
            return
        if self._update_downloading:
            return
        self._update_stop.clear()
        self._update_downloading = True
        self._update_phase = 'downloading'
        self._update_progress_value = 0
        self._update_progress_text = ''
        self._update_fail_kind = None
        self._refresh_update_banner_copy()
        dest = update_check.destination_path(
            update_check.downloads_dir(data_dir=DATA_DIR),
            offer.asset_name or 'PowerTokensVideoStudio.bin',
            offer.version or offer.tag,
        )

        def report(received, total):
            self.events.put(('update_progress', (received, total)))

        def worker():
            try:
                path = update_check.download_asset(
                    offer.asset_url, dest, sha256=offer.sha256,
                    timeout=update_check.DEFAULT_TIMEOUT, progress=report,
                    stop=self._update_stop,
                )
                self.events.put(('update_ready', str(path)))
            except update_check.UpdateError as exc:
                self.events.put(('update_fail', exc.code))
            except Exception:
                self.events.put(('update_fail', 'network'))
            finally:
                self._update_downloading = False

        threading.Thread(target=worker, daemon=True).start()

    def _update_progress(self, payload):
        received, total = payload if isinstance(payload, (tuple, list)) and len(payload) == 2 else (payload, None)
        try:
            received = int(received or 0)
        except (TypeError, ValueError):
            received = 0
        self._update_progress_text = update_check.format_progress(received, total)
        if hasattr(self, 'update_progress_bar') and total:
            self._update_progress_value = min(100, 100 * received / total)
            self.update_progress_bar.configure(value=self._update_progress_value)
        if self._update_phase != 'downloading':
            self._update_phase = 'downloading'
        self._refresh_update_banner_copy()

    def _update_ready(self, path):
        self._update_downloading = False
        self._update_dest = Path(path) if path else self._update_dest
        self._update_phase = 'ready'
        self._refresh_update_banner_copy()
        self._pack_update_banner()

    def _update_fail(self, kind):
        self._update_downloading = False
        if kind == 'cancelled':
            return
        self._update_fail_kind = str(kind or 'network')
        self._update_phase = 'fail'
        self._refresh_update_banner_copy()
        self._pack_update_banner()

    def _quit_and_run_update(self):
        path = self._update_dest
        if not path:
            self._update_fail('launch')
            return
        try:
            analytics.end_session()
        except Exception:
            pass
        try:
            update_check.launch_package(path, sys.platform)
        except Exception:
            self._update_fail('launch')
            return
        try:
            self._save_generate_form()
            self.chat.persist_state()
            self.root.destroy()
        except tk.TclError:
            pass

    def _open_downloaded_package(self):
        path = self._update_dest
        if not path:
            self._update_fail('launch')
            return
        try:
            update_check.launch_package(path, sys.platform)
        except Exception:
            self._update_fail('launch')

    def _reveal_download(self):
        path = self._update_dest
        if not path:
            return
        folder = Path(path).parent
        folder.mkdir(parents=True, exist_ok=True)
        try:
            if os.name == 'nt':
                os.startfile(str(folder))
            elif sys.platform == 'darwin':
                import subprocess
                subprocess.Popen(['open', '-R', str(path)])
            else:
                import subprocess
                subprocess.Popen(['xdg-open', str(folder)])
        except Exception:
            try:
                messagebox.showinfo(t('save_location'), str(folder))
            except Exception:
                pass

    def drain(self):
        try:
            while True:
                kind, text = self.events.get_nowait()
                if kind == 'update_available':
                    self._show_update_offer(text)
                    continue
                if kind == 'update_progress':
                    self._update_progress(text)
                    continue
                if kind == 'update_ready':
                    self._update_ready(text)
                    continue
                if kind == 'update_fail':
                    self._update_fail(text)
                    continue
                if isinstance(kind, str) and kind.startswith('update_'):
                    continue
                if kind == 'credit_profile':
                    self._credit_profile_busy = False
                    token, profile = text
                    if profile and (self.oauth_session or {}).get('access_token') == token:
                        self.oauth_session['profile'] = profile
                    self._refresh_oauth_header()
                    continue
                if kind == 'oauth_ok':
                    self._apply_oauth_result(text)
                    continue
                if kind == 'oauth_fail':
                    self._oauth_fail(text)
                    continue
                if kind == 'keys_verify_done':
                    self._keys_verify_done(text)
                    continue
                if kind == 'batch_update':
                    self.batch.update_job(text)
                    continue
                if 'pre-consume quota' in str(text).lower():
                    text = t('credits_insufficient')
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
                    self.bar.pack_forget()
                    for button in (self.generate_btn, self.resume_btn, self.manual_btn, self.link_btn):
                        button.configure(state='normal')
                    self.update_cost()
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
                self.tree.insert('', 'end', iid=path.stem, values=(row['created'], task_label, t('credits_insufficient') if row.get('credit_rejected') or 'QUOTA' in row['state'] or 'pre-consume quota' in row['state'].lower() else row['state'], row.get('key_hint', '')))
            except (OSError, ValueError, KeyError):
                continue
        retained = [item for item in selection if item in self.records]
        if retained:
            self.tree.selection_set(*retained)

    def _show_history_credit(self, event=None):
        selected = self.tree.selection()
        row = self.records.get(selected[0], {}) if selected else {}
        if row.get('credit_rejected') or 'QUOTA' in row.get('state', '') or 'pre-consume quota' in row.get('state', '').lower():
            cost = row.get('cost')
            need = conversation_store.estimate_credits(cost) if isinstance(cost, (int, float)) else None
            have = self._oauth_profile().get('credits') if self._is_oauth_logged_in() else None
            have_text = f'{have:,}' if isinstance(have, (int, float)) else t('credits_unknown')
            need_text = f'{need:,}' if need is not None else t('credits_unknown')
            self._history_credit_shortfall = (need or 0, have or 0)
            amount = conversation_store.suggested_amount(*self._history_credit_shortfall)
            self.history_credit_text.set(t('credits_insufficient_tip', have_text, need_text))
            self.history_credit_suggest.set(t('credits_insufficient_suggest', amount, f'{amount * 1000:,}'))
            self.history_credit_card.pack(fill='x', pady=8, before=self.history_table_card)
        else:
            self.history_credit_card.pack_forget()

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
        try:
            reveal_path(folder)
        except OSError:
            messagebox.showinfo(t('save_location'), str(folder))

    def close(self):
        if self.busy:
            self.stop_wait()
            messagebox.showinfo(t('closing_title'), t('closing_body'))
            return
        stop = getattr(self, '_update_stop', None)
        if stop is not None:
            stop.set()
        try:
            analytics.end_session()
        except Exception:
            pass
        self._save_generate_form()
        self.chat.persist_state()
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

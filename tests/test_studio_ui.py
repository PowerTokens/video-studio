"""Native widget smoke tests; run on Windows, or opt in with PT_GUI_TESTS=1."""
import i18n
i18n.set_language('zh')  # Existing tests check the original Chinese wording.

import os
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

import datetime
import re

import app
import batch_ui
import wan_core
from studio_ui import APP_NAME, VERSION, Columns, Card


@unittest.skipUnless(os.name == 'nt' or os.environ.get('PT_GUI_TESTS') == '1',
                     'Native Tk tests require Windows or PT_GUI_TESTS=1')
class StudioUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.patches = [patch.object(app, 'DATA_DIR', Path(cls.folder.name)),
                       patch.object(batch_ui, 'CHARACTER_SETTING_PATH', Path(cls.folder.name) / 'characters.json'),
                       patch.object(wan_core, 'local_today', return_value=datetime.date(2026, 10, 1))]
        for item in cls.patches:
            item.start()
        cls.root = tk.Tk()
        cls.errors = []
        cls.root.report_callback_exception = lambda *args: cls.errors.append(args)
        cls.gui = app.App(cls.root)
        cls.root.update()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()
        for item in cls.patches:
            item.stop()
        cls.folder.cleanup()

    def widgets(self, parent):
        for child in parent.winfo_children():
            yield child
            yield from self.widgets(child)

    def test_promo_badge_and_list_price_follow_end_date(self):
        self.gui.duration.set('10')
        self.gui.resolution.set('720p')
        self.gui.update_cost()
        self.assertTrue(self.gui.promo_label.winfo_manager())
        self.assertIn('$0.04/秒，原价 $0.10/秒', self.gui.cost.get())
        with patch.object(wan_core, 'local_today', return_value=datetime.date(2026, 10, 8)):
            self.gui.update_cost()
            self.assertEqual(self.gui.promo_label.winfo_manager(), '')
            self.assertIn('约 $1.00（$0.10/秒）', self.gui.cost.get())
            self.assertNotIn('原价', self.gui.cost.get())
        self.gui.update_cost()
        self.assertTrue(self.gui.promo_label.winfo_manager())

    def test_pages_reflow_without_callback_errors_or_clipped_cards(self):
        for width in (1280, 860):
            self.root.geometry('%dx800' % width)
            for index in range(5):
                self.gui.tabs.select(index)
                self.root.update()
                for widget in self.widgets(self.gui.pages[index]):
                    if isinstance(widget, Card):
                        self.assertGreater(widget.winfo_width(), 150)
                        self.assertGreaterEqual(widget.winfo_height(), widget.body.winfo_reqheight() + 39,
                                                (width, index, str(widget), widget.cget('height'),
                                                 widget.winfo_reqheight(), widget.body.winfo_height()))
                    if isinstance(widget, Columns):
                        self.assertEqual(widget.wide, widget.winfo_width() >= widget.breakpoint)
                self.assertFalse(self.errors, self.errors)

    def test_selected_tab_keeps_same_size_and_only_changes_colour(self):
        style = ttk.Style(self.root)

        def tab(option, state):
            value = style.lookup('TNotebook.Tab', option, state)
            return [str(item) for item in self.root.tk.splitlist(value)] if value != '' else []

        for option in ('padding', 'font', 'expand'):
            self.assertEqual(tab(option, ['selected']), tab(option, ['!selected']), option)
        self.assertEqual(tab('padding', ['selected']), ['20', '12'])
        self.assertFalse(any(int(item) for item in tab('expand', ['selected'])))
        for option in ('background', 'foreground'):
            self.assertNotEqual(tab(option, ['selected']), tab(option, ['!selected']), option)

    def test_requested_copy_and_key_pool_work(self):
        self.assertEqual(self.root.title(), '%s · v%s' % (APP_NAME, VERSION))
        self.assertEqual(self.gui.stop_btn.cget('text'), '停止等待')
        texts = []
        for widget in self.widgets(self.root):
            if isinstance(widget, (ttk.Label, ttk.Button)):
                texts.append(str(widget.cget('text')))
        self.assertIn('任务仍在云端继续，可在任务记录里找回', texts)
        self.assertIn('填写网络可访问的图片 / 视频 / 音频链接，暂不支持本地上传；不需要可留空', texts)
        self.assertIn('直接粘贴从 PowerTokens 官网复制的 Key 即可', texts)
        self.assertIn('还没有 Key？去 PowerTokens 注册即可使用。支持 Wan、Seedance、Kling 等多种视频模型。', texts)
        self.assertIn('Wan 3.0 限时折扣至 10 月 7 日', texts)
        self.assertFalse(any('界面设计预览' in text for text in texts))
        self.gui.key_input.set('sk-test-only-one, sk-test-only-two')
        self.gui.add_keys()
        self.assertEqual(len(self.gui.keys), 2)
        self.assertEqual(self.gui.key_input.get(), '')
        self.assertEqual(self.gui.key_count.get(), '共 2 个 Key')
        self.assertNotIn('sk-test', ' '.join(self.gui.key_list.get(0, 'end')))
        self.gui.key_list.selection_clear(0, 'end')
        self.gui.key_list.selection_set(1)
        self.assertEqual(self.gui.selected_key(), 'sk-test-only-two')
        self.gui.remove_key()
        self.assertEqual(self.gui.keys, ['sk-test-only-one'])
        self.assertEqual(self.gui.key_count.get(), '共 1 个 Key')


class ResourceTests(unittest.TestCase):
    """No window needed: bundled-asset lookup for source runs and PyInstaller builds."""
    def test_logo_assets_exist(self):
        import studio_ui
        self.assertTrue(studio_ui.resource_path(studio_ui.LOGO_FILE).is_file())
        for size in studio_ui.HEADER_LOGO_SIZES:
            self.assertTrue(studio_ui.resource_path('assets/logo-%d.png' % size).is_file())
        self.assertTrue(studio_ui.resource_path('assets/logo.ico').is_file())

    def test_frozen_build_uses_meipass(self):
        import sys
        import studio_ui
        with patch.object(sys, '_MEIPASS', '/frozen/bundle', create=True):
            self.assertEqual(studio_ui.resource_path('assets/logo.png'), Path('/frozen/bundle') / 'assets/logo.png')

    def test_header_logo_follows_display_scale(self):
        import studio_ui
        self.assertEqual(studio_ui.header_logo_file(1.0), 'assets/logo-44.png')
        self.assertEqual(studio_ui.header_logo_file(1.5), 'assets/logo-66.png')
        self.assertEqual(studio_ui.header_logo_file(2.0), 'assets/logo-88.png')

    def test_missing_logo_returns_none(self):
        import studio_ui
        self.assertIsNone(studio_ui.load_image('assets/does-not-exist.png'))


@unittest.skipUnless(os.name == 'nt' or os.environ.get('PT_GUI_TESTS') == '1',
                     'Native Tk tests require Windows or PT_GUI_TESTS=1')
class LogoWindowTests(unittest.TestCase):
    def build(self, missing=False):
        import studio_ui
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        patches = [patch.object(app, 'DATA_DIR', Path(folder.name)),
                   patch.object(batch_ui, 'CHARACTER_SETTING_PATH', Path(folder.name) / 'characters.json')]
        if missing:
            patches.append(patch.object(studio_ui, 'resource_path', lambda name: Path(folder.name) / 'missing' / name))
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        root = tk.Tk()
        self.addCleanup(root.destroy)
        gui = app.App(root)
        # Cancel pending Tk callbacks before the window is destroyed.
        self.addCleanup(lambda: [root.after_cancel(item) for item in root.tk.splitlist(root.tk.call('after', 'info'))])
        root.update()
        return gui

    def test_header_shows_official_logo(self):
        gui = self.build()
        self.assertIsNotNone(gui.logo_image)
        self.assertIsNotNone(gui.icon_image)
        self.assertTrue(40 <= gui.logo_image.height() <= 90)
        self.assertIn(str(gui.logo_image), str(gui.logo_label.cget('image')))

    def test_app_starts_without_logo_files(self):
        gui = self.build(missing=True)
        self.assertIsNone(gui.logo_image)
        self.assertIsNone(gui.icon_image)
        self.assertFalse(hasattr(gui, 'logo_label'))
        self.assertEqual(gui.stop_btn.cget('text'), '停止等待')


@unittest.skipUnless(os.name == 'nt' or os.environ.get('PT_GUI_TESTS') == '1',
                     'Native Tk tests require Windows or PT_GUI_TESTS=1')
class LanguageSwitchTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.settings = Path(folder.name) / 'settings.json'
        patches = [patch.object(app, 'DATA_DIR', Path(folder.name)),
                   patch.object(batch_ui, 'CHARACTER_SETTING_PATH', Path(folder.name) / 'characters.json'),
                   patch.object(i18n, 'SETTINGS_PATH', self.settings),
                   patch.object(wan_core, 'local_today', return_value=datetime.date(2026, 10, 1))]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        i18n.set_language('zh')
        self.addCleanup(i18n.set_language, 'zh')
        self.root = tk.Tk()
        self.addCleanup(self.root.destroy)
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.addCleanup(lambda: [self.root.after_cancel(item) for item in self.root.tk.splitlist(self.root.tk.call('after', 'info'))])
        self.root.geometry('1280x900')
        self.gui = app.App(self.root)
        self.root.update()

    def widgets(self, parent):
        for child in parent.winfo_children():
            yield child
            yield from self.widgets(child)

    def texts(self):
        return [str(w.cget('text')) for w in self.widgets(self.root)
                if isinstance(w, (ttk.Label, ttk.Button, ttk.Checkbutton)) and str(w.cget('text'))]

    def tab_names(self):
        return [self.gui.tabs.tab(i, 'text') for i in range(5)]

    def test_switch_to_english_and_back_keeps_everything(self):
        self.gui.key_input.set('sk-test-only-one')
        self.gui.add_keys()
        self.gui.prompt.insert('1.0', 'Total length 12s, 9:16 vertical. 0-4s: hook; 4-12s: chase')
        self.gui.apply_prompt()
        self.gui.seed.set('42')
        self.gui.tabs.select(4)
        self.gui.switch_language('en')
        self.root.update()
        self.assertEqual(i18n.get_language(), 'en')
        self.assertEqual(i18n.load_settings()['language'], 'en')
        self.assertEqual(self.tab_names(), ['Generate video', 'Compare', 'Batch import', 'History / Resume', 'API Key'])
        self.assertEqual(self.gui.tabs.index('current'), 4)
        self.assertEqual(self.gui.keys, ['sk-test-only-one'])
        self.assertEqual(self.gui.key_count.get(), '1 key')
        self.assertEqual(self.gui.key_badge.get(), '1 key added')
        self.assertIn('Total length 12s', self.gui.prompt.get('1.0', 'end'))
        self.assertEqual((self.gui.duration.get(), self.gui.ratio.get(), self.gui.seed.get()), ('12', '9:16', '42'))
        self.assertEqual(self.gui.prompt_hint.get(), 'Detected: 12 s · 9:16')
        self.assertIn('$0.04/s, regular $0.10/s', self.gui.cost.get())
        texts = self.texts()
        self.assertIn('Multi-model AI video generation', texts)
        self.assertIn('Wan 3.0 limited-time discount until Oct 7', texts)
        self.assertIn('Stop waiting', texts)
        self.assertIn('Save short-drama sample…', texts)
        chinese = [text for text in texts if re.search('[\u4e00-\u9fff]', text) and text != '中文']
        self.assertEqual(chinese, [])
        self.gui.switch_language('zh')
        self.root.update()
        self.assertEqual(self.tab_names(), ['生成视频', '模型对比', '批量导入', '任务记录 / 恢复', 'API Key'])
        self.assertIn('任务仍在云端继续，可在任务记录里找回', self.texts())
        self.assertEqual(self.gui.key_count.get(), '共 1 个 Key')
        self.assertEqual(self.gui.prompt_hint.get(), '已识别：12 秒 · 9:16')
        self.assertEqual(i18n.load_settings()['language'], 'zh')
        self.assertFalse(self.errors, self.errors)

    def test_busy_switch_is_saved_for_next_start(self):
        self.gui.busy = True
        with patch.object(app.messagebox, 'showinfo') as info:
            self.gui.switch_language('en')
        info.assert_called_once()
        self.assertEqual(i18n.get_language(), 'zh')
        self.assertEqual(i18n.load_settings()['language'], 'en')
        self.assertEqual(self.tab_names()[0], '生成视频')
        self.gui.busy = False

    def test_no_button_or_label_is_clipped(self):
        for lang in ('en', 'zh'):
            self.gui.switch_language(lang)
            for width in (1280, 1000, 860):
                self.root.geometry('%dx800' % width)
                for index in range(5):
                    self.gui.tabs.select(index)
                    self.root.update()
                    for widget in self.widgets(self.root):
                        if not isinstance(widget, (ttk.Button, ttk.Checkbutton, ttk.Label)) or not widget.winfo_viewable():
                            continue
                        if isinstance(widget, ttk.Label) and str(widget.cget('wraplength')) not in ('', '0'):
                            continue  # Wrapping labels reflow to the available width.
                        self.assertGreaterEqual(widget.winfo_width(), widget.winfo_reqwidth(),
                                                (lang, width, index, str(widget.cget('text'))))
                        # Fully inside the window horizontally.
                        self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(),
                                             self.root.winfo_rootx() + self.root.winfo_width(),
                                             (lang, width, index, str(widget.cget('text'))))
            batch_tree = self.gui.batch.tree
            import tkinter.font as tkfont
            font = tkfont.Font(font=ttk.Style(self.root).lookup('Treeview.Heading', 'font'))
            for column in batch_tree['columns']:
                self.assertGreaterEqual(int(batch_tree.column(column, 'width')),
                                        font.measure(batch_tree.heading(column, 'text')) + 20, (lang, column))
        self.assertFalse(self.errors, self.errors)

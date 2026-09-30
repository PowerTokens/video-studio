"""Launch the app with a throw-away data folder and screenshot each tab in each language.

Used by the "UI screenshots" CI job on the Windows runner (and handy locally):
    python tools/capture_screenshots.py --out screenshots --lang en zh
No real API key or Task ID is used: a dummy key and the bundled sample spreadsheet only.
Requires Pillow (ImageGrab). Writes <out>/<lang>-<tab>.png plus <out>/layout-<lang>.txt,
which lists any button / label whose visible width is smaller than it needs (clipping).
"""
import argparse
import ctypes
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tkinter as tk  # noqa: E402
from tkinter import ttk  # noqa: E402

import i18n  # noqa: E402

TABS = ('generate', 'batch', 'history', 'apikey')
DUMMY_KEY = 'sk-demo-screenshot-A1b2'  # Not a real key; shown masked as ****A1b2.
PROMPTS = {
    'en': ('16s, 16:9 landscape. Realistic coming-of-age drama on a quiet college-town street at night: streetlights '
           'glow on damp asphalt. She wears a denim jacket, he wears a hoodie; they walk side by side.\n'
           '0-5s: tracking two-shot from behind, street ambience and a light breeze. He says: "You didn\'t have to '
           'make me the punchline."\n5-10s: they slow down near the crosswalk as the light turns red.\n'
           '10-16s: they face each other under a streetlight; freeze on the last second.'),
    'zh': ('16秒，16:9横屏。写实美式青春剧，夜街余波。美国大学城夜晚街道：路灯映在微湿柏油路上，稀疏停靠车辆。'
           '女主穿夹克，男主穿连帽衫，两人并肩走。\n0-5秒：侧后方跟拍双人镜头，街道环境音与轻风。'
           '男主说：“你没必要拿我当笑点。”\n5-10秒：走到斑马线附近放慢。红灯禁止通行。中近景交替。\n'
           '10-16秒：两人在路灯下对视，最后1秒定格。'),
}
CHARACTERS = {
    'en': ('[Characters]\n@ning: early 20s, short black hair, red raincoat; calm and observant.\n'
           '@double: looks exactly like Ning, but her raincoat is perfectly dry.'),
    'zh': '【人物】\n@阿宁：20岁出头，黑色短发，红色雨衣；冷静、观察力强。\n@另一个阿宁：外貌与阿宁完全相同，但雨衣是干的。',
}


def window_bbox(root):
    """Outer window rectangle including the title bar (Windows), else the Tk client area."""
    root.update()
    if os.name == 'nt':
        from ctypes import wintypes
        hwnd = int(root.wm_frame(), 16)
        rect = wintypes.RECT()
        # DWMWA_EXTENDED_FRAME_BOUNDS = 9: the visible frame without the invisible resize border.
        if ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)) == 0:
            return rect.left, rect.top, rect.right, rect.bottom
    x, y = root.winfo_rootx(), root.winfo_rooty()
    return x, y, x + root.winfo_width(), y + root.winfo_height()


def widgets(parent):
    for child in parent.winfo_children():
        yield child
        yield from widgets(child)


def layout_problems(root, tab):
    problems = []
    right = root.winfo_rootx() + root.winfo_width()
    for widget in widgets(root):
        if not isinstance(widget, (ttk.Button, ttk.Checkbutton, ttk.Label)) or not widget.winfo_viewable():
            continue
        text = str(widget.cget('text'))
        if isinstance(widget, ttk.Label) and str(widget.cget('wraplength')) not in ('', '0'):
            continue
        if widget.winfo_width() < widget.winfo_reqwidth():
            problems.append('%s: clipped %r (%d < %d px)' % (tab, text, widget.winfo_width(), widget.winfo_reqwidth()))
        if widget.winfo_rootx() + widget.winfo_width() > right:
            problems.append('%s: outside window %r' % (tab, text))
    return problems


def shoot(root, gui, lang, name, index, out, suffix=''):
    from PIL import ImageGrab
    gui.tabs.select(index)
    for _ in range(5):
        root.update()
        time.sleep(.15)
    image = ImageGrab.grab(bbox=window_bbox(root), all_screens=True)
    path = out / ('%s-%s%s.png' % (lang, name, suffix))
    image.save(path)
    print('saved', path, image.size)
    return layout_problems(root, name + suffix)


def capture(lang, out, width, height, narrow):
    import app
    import batch_ui
    folder = tempfile.TemporaryDirectory()
    data = Path(folder.name)
    patches = [patch.object(app, 'DATA_DIR', data), patch.object(batch_ui, 'CHARACTER_SETTING_PATH', data / 'c.json'),
               patch.object(i18n, 'SETTINGS_PATH', data / 'settings.json'),
               patch('batch_engine.DATA_DIR', data), patch('wan_core.DATA_DIR', data)]
    for item in patches:
        item.start()
    i18n.set_language(lang)
    root = tk.Tk()
    gui = app.App(root)
    root.geometry('%dx%d+0+0' % (width, height))
    root.attributes('-topmost', True)
    root.lift()
    root.focus_force()
    # Generate tab: a storyboard prompt, so duration / ratio detection is visible.
    gui.prompt.insert('1.0', PROMPTS[lang])
    gui.apply_prompt()
    # API Key tab: one dummy key.
    gui.key_input.set(DUMMY_KEY)
    gui.add_keys()
    # Batch tab: import the sample spreadsheet for this language with a character setting.
    batch = gui.batch
    batch.character_setting.insert('1.0', CHARACTERS[lang])
    template = ROOT / batch_ui.template_file()
    with patch.object(batch_ui.filedialog, 'askopenfilename', return_value=str(template)):
        batch.import_file()
    gui.status.set(i18n.t('status_idle'))
    problems = []
    for index, name in enumerate(TABS):
        problems += shoot(root, gui, lang, name, index, out)
    # The lower half of every page, then the whole set again in a narrow window (stacked layout).
    for index, name in enumerate(TABS):
        gui.tabs.select(index)
        root.update()
        gui.pages[index].canvas.yview_moveto(1)
        problems += shoot(root, gui, lang, name, index, out, '-bottom')
        gui.pages[index].canvas.yview_moveto(0)
    root.geometry('%dx%d+0+0' % (narrow, height))
    for index, name in enumerate(TABS):
        problems += shoot(root, gui, lang, name, index, out, '-narrow')
        gui.pages[index].canvas.yview_moveto(1)
        problems += shoot(root, gui, lang, name, index, out, '-narrow-bottom')
        gui.pages[index].canvas.yview_moveto(0)
    (out / ('layout-%s.txt' % lang)).write_text('\n'.join(problems) or 'OK: nothing clipped', encoding='utf-8')
    print('\n'.join(problems) or 'layout OK')
    for pending in root.tk.splitlist(root.tk.call('after', 'info')):
        root.after_cancel(pending)
    root.destroy()
    for item in patches:
        item.stop()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default='screenshots')
    parser.add_argument('--lang', nargs='+', default=['en', 'zh'])
    # 1278 x 918 client area = 1280 x 950 including the window frame, like the README images.
    parser.add_argument('--width', type=int, default=1278)
    parser.add_argument('--height', type=int, default=918)
    parser.add_argument('--narrow', type=int, default=880)
    args = parser.parse_args()
    if os.name == 'nt':
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for lang in args.lang:
        capture(lang, out, args.width, args.height, args.narrow)


if __name__ == '__main__':
    main()

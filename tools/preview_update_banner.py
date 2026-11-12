"""Capture live Tk previews of the in-app update banner, then paint readable type.

This box's Tk 9.0 build has no fontconfig, so CJK (and some Latin) glyphs are
missing or wrong. We still launch the real app, screenshot the live window, and
redraw widget text with Noto Sans CJK SC at the real widget boxes.

    VS_SKIP_UPDATE_CHECK=1 python tools/preview_update_banner.py --out /path
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ['VS_SKIP_UPDATE_CHECK'] = '1'

import tkinter as tk  # noqa: E402
from tkinter import ttk  # noqa: E402

import i18n  # noqa: E402
import studio_ui  # noqa: E402
import update_check  # noqa: E402

PAGE = studio_ui.PAGE
WHITE = studio_ui.WHITE
INK = studio_ui.INK
MUTED = studio_ui.MUTED
ACCENT = studio_ui.ACCENT
PALE = studio_ui.PALE
LINK = '#328f83'

FONT_REG = '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'
FONT_BOLD = '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc'
FONT_INDEX = 2  # Noto Sans CJK SC


def _bbox(root):
    root.update_idletasks()
    root.update()
    x, y = root.winfo_rootx(), root.winfo_rooty()
    return x, y, x + root.winfo_width(), y + root.winfo_height()


def _grab(path, bbox):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    left, top, right, bottom = bbox
    width, height = right - left, bottom - top
    try:
        import mss
        from PIL import Image
        with mss.MSS() as sct:
            raw = sct.grab({'left': left, 'top': top, 'width': width, 'height': height})
            image = Image.frombytes('RGB', raw.size, raw.bgra, 'raw', 'BGRX')
    except Exception:
        from PIL import ImageGrab
        image = ImageGrab.grab(bbox=bbox)
        if image.mode != 'RGB':
            image = image.convert('RGB')
    image.save(path)
    print('saved', path, image.size)
    return image


def _style_of(widget):
    try:
        return str(widget.cget('style') or '')
    except tk.TclError:
        return ''


def _text_of(widget):
    try:
        return str(widget.cget('text') or '')
    except tk.TclError:
        return ''


def collect_widgets(root):
    origin_x, origin_y = root.winfo_rootx(), root.winfo_rooty()
    items = []

    def add(widget, kind, text, style='', fill=None, color=None, size=13, bold=False,
            align='left', disabled=False):
        text = (text or '').strip()
        if not text:
            return
        try:
            if not widget.winfo_viewable():
                return
            x = widget.winfo_rootx() - origin_x
            y = widget.winfo_rooty() - origin_y
            w, h = widget.winfo_width(), widget.winfo_height()
        except tk.TclError:
            return
        if w < 4 or h < 4:
            return
        items.append({
            'kind': kind, 'text': text, 'style': style,
            'x': x, 'y': y, 'w': w, 'h': h,
            'fill': fill, 'color': color, 'size': size, 'bold': bold,
            'align': align, 'disabled': disabled,
        })

    def walk(widget):
        for child in widget.winfo_children():
            walk(child)
        style = _style_of(widget)
        text = _text_of(widget)
        disabled = False
        try:
            disabled = str(widget.cget('state')) == 'disabled'
        except tk.TclError:
            pass
        if isinstance(widget, ttk.Button):
            if style == 'Accent.TButton':
                add(widget, 'button', text, style, fill=ACCENT, color=WHITE, size=13, bold=False, align='center', disabled=disabled)
            elif style == 'Link.TButton':
                fill = PAGE
                # Header toolbar sits on a white header.
                try:
                    parent = widget.nametowidget(widget.winfo_parent())
                    # Walk up a few frames: if any ancestor is the header (white), use WHITE.
                    bg = PAGE
                    cur = parent
                    for _ in range(6):
                        try:
                            st = str(cur.cget('style') or '')
                        except tk.TclError:
                            st = ''
                        name = str(cur)
                        if st in ('TFrame', '') and 'header' in name:
                            bg = WHITE
                            break
                        try:
                            cur = cur.nametowidget(cur.winfo_parent())
                        except Exception:
                            break
                    # Link buttons in the header toolbar (Tips) are on white.
                    if widget.winfo_rooty() < widget.winfo_toplevel().winfo_rooty() + 90:
                        bg = WHITE
                    fill = bg
                except Exception:
                    fill = PAGE
                color = '#9aa8b6' if disabled else LINK
                add(widget, 'button', text, style, fill=fill, color=color, size=13, align='center', disabled=disabled)
            else:
                add(widget, 'button', text, style, fill=WHITE, color='#4b6073', size=13, align='center', disabled=disabled)
        elif isinstance(widget, ttk.Label):
            if style == 'Brand.TLabel':
                add(widget, 'label', text, style, fill=WHITE, color=INK, size=22, bold=True)
            elif style == 'Heading.TLabel':
                add(widget, 'label', text, style, fill=PAGE, color=INK, size=22, bold=True)
            elif style == 'CardTitle.TLabel':
                add(widget, 'label', text, style, fill=WHITE, color=INK, size=14, bold=True)
            elif style == 'Page.TLabel':
                add(widget, 'label', text, style, fill=PAGE, color=MUTED, size=13)
            elif style == 'Muted.TLabel':
                # May sit on white cards or page.
                fill = WHITE
                try:
                    parent = widget.nametowidget(widget.winfo_parent())
                    if str(parent.cget('style') or '') == 'Page.TFrame':
                        fill = PAGE
                except Exception:
                    pass
                add(widget, 'label', text, style, fill=fill, color=MUTED, size=13)
            elif style in ('Badge.TLabel',):
                add(widget, 'label', text, style, fill=PALE, color='#328f80', size=13)
            else:
                add(widget, 'label', text, style, fill=WHITE, color=INK, size=13)
        elif isinstance(widget, ttk.Checkbutton):
            add(widget, 'check', text, style, fill=WHITE, color=INK, size=13)
        elif isinstance(widget, ttk.Combobox):
            try:
                text = str(widget.get() or '')
            except tk.TclError:
                text = ''
            add(widget, 'combo', text, 'TCombobox', fill=WHITE, color=INK, size=13)
        elif isinstance(widget, ttk.Notebook):
            try:
                count = int(widget.index('end'))
            except tk.TclError:
                count = 0
            nx = widget.winfo_rootx() - origin_x
            ny = widget.winfo_rooty() - origin_y
            cursor = nx
            for i in range(count):
                try:
                    tab_text = str(widget.tab(i, 'text') or '')
                except tk.TclError:
                    continue
                bx = by = bw = bh = 0
                try:
                    box = widget.bbox(widget.tabs()[i]) if widget.tabs() else None
                    if box:
                        bx, by, bw, bh = box
                except tk.TclError:
                    pass
                if bw < 8 or bh < 8:
                    # Tk 9 sometimes returns an empty bbox; lay out tabs from text width.
                    from PIL import ImageFont
                    font = ImageFont.truetype(FONT_REG, 13, index=FONT_INDEX)
                    bw = int(font.getlength(tab_text)) + 40
                    bh = 36
                    bx, by = cursor - nx, 0
                selected = False
                try:
                    selected = int(widget.index('current')) == i
                except tk.TclError:
                    pass
                items.append({
                    'kind': 'tab', 'text': tab_text, 'style': 'TNotebook.Tab',
                    'x': nx + bx, 'y': ny + by, 'w': bw, 'h': bh,
                    'fill': PALE if selected else WHITE,
                    'color': '#268b7e' if selected else INK,
                    'size': 13, 'bold': False, 'align': 'center', 'disabled': False,
                })
                cursor = nx + bx + bw

    walk(root)
    return items


def _font(size, bold=False):
    from PIL import ImageFont
    path = FONT_BOLD if bold else FONT_REG
    return ImageFont.truetype(path, size, index=FONT_INDEX)


def _wrap_text(text, font, max_width):
    if max_width <= 0:
        return [text]
    words = text.split(' ')
    # For Chinese, wrap by characters when there are few spaces.
    if text.count(' ') < max(1, len(text) // 12):
        lines, current = [], ''
        for ch in text:
            trial = current + ch
            if font.getlength(trial) <= max_width or not current:
                current = trial
            else:
                lines.append(current)
                current = ch
        if current:
            lines.append(current)
        return lines or [text]
    lines, current = [], ''
    for word in words:
        trial = word if not current else current + ' ' + word
        if font.getlength(trial) <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [text]


def paint(image, items, out_path):
    from PIL import Image, ImageDraw
    canvas = image.copy()
    draw = ImageDraw.Draw(canvas)
    # Draw tabs first (under other chrome if overlapping), then labels, then buttons.
    order = {'tab': 0, 'combo': 1, 'check': 2, 'label': 3, 'button': 4}
    for item in sorted(items, key=lambda it: order.get(it['kind'], 9)):
        x, y, w, h = item['x'], item['y'], item['w'], item['h']
        fill, color = item['fill'] or PAGE, item['color'] or INK
        # Body UI in this app is 10pt; 13px matches 96dpi. Brand uses a larger face.
        size = item['size']
        if item['style'] == 'Brand.TLabel':
            size = 18
        elif item['style'] == 'Heading.TLabel':
            size = 20
        elif item['style'] == 'CardTitle.TLabel':
            size = 13
        else:
            size = 13 if size >= 13 else size
        font = _font(size, item['bold'] or item['style'] in ('Brand.TLabel', 'Heading.TLabel', 'CardTitle.TLabel'))
        text = item['text']
        box = font.getbbox(text)
        tw, th = box[2] - box[0], box[3] - box[1]
        if item['kind'] == 'button':
            pad_x, pad_y = (18, 8) if item['style'] == 'Accent.TButton' else (10, 6)
            w = max(w, int(tw + pad_x * 2))
            h = max(h, int(th + pad_y * 2))
            draw.rounded_rectangle((x, y, x + w - 1, y + h - 1), radius=5, fill=fill)
            if item['style'] not in ('Accent.TButton', 'Link.TButton'):
                draw.rounded_rectangle((x, y, x + w - 1, y + h - 1), radius=5, outline='#dfe7ee')
            tx = x + (w - tw) / 2 - box[0]
            ty = y + (h - th) / 2 - box[1]
            draw.text((tx, ty), text, font=font, fill=color)
        elif item['kind'] == 'tab':
            draw.rectangle((x, y, x + w, y + h), fill=fill)
            box = font.getbbox(text)
            tw, th = box[2] - box[0], box[3] - box[1]
            tx = x + (w - tw) / 2 - box[0]
            ty = y + (h - th) / 2 - box[1]
            draw.text((tx, ty), text, font=font, fill=color)
        elif item['kind'] == 'combo':
            # Fill the value area, keep the dropdown arrow on the right (~22px).
            arrow = 22
            draw.rectangle((x + 2, y + 2, x + w - arrow, y + h - 2), fill=fill)
            box = font.getbbox(text)
            tw, th = box[2] - box[0], box[3] - box[1]
            tx = x + 10 - box[0]
            ty = y + (h - th) / 2 - box[1]
            draw.text((tx, ty), text, font=font, fill=color)
        elif item['kind'] == 'check':
            # Leave the checkbox control when there is room; otherwise paint the whole box.
            indent = 24 if w > 40 else 0
            left = x + indent
            if left < x + w and h > 0:
                draw.rectangle((left, y, x + w, y + h), fill=fill)
            box = font.getbbox(text)
            th = box[3] - box[1]
            ty = y + (h - th) / 2 - box[1]
            draw.text((left, ty), text, font=font, fill=color)
        else:
            # One-line labels (height <= 28, or brand/heading): expand to the text
            # instead of wrapping, so missing CJK glyph boxes are not used as a clip.
            one_line = h <= 28 or item['style'] in ('Brand.TLabel', 'Heading.TLabel', 'CardTitle.TLabel')
            # Banner / page descriptions that already have a wide box may wrap.
            may_wrap = (not one_line) or (item['style'] == 'Page.TLabel' and w >= 600 and tw > w - 4)
            if may_wrap:
                lines = _wrap_text(text, font, max(8, w - 4))
            else:
                lines = [text]
                w = max(w, int(tw + 4))
                h = max(h, int(th + 6))
            if w > 1 and h > 1:
                draw.rectangle((x, y, x + w, y + min(h, 8 + 20 * len(lines))), fill=fill)
            ty = y + 2
            for line in lines:
                lb = font.getbbox(line)
                draw.text((x + 1 - lb[0], ty - lb[1]), line, font=font, fill=color)
                ty += max(lb[3] - lb[1] + 4, 18)
    canvas.save(out_path)
    print('painted', out_path, canvas.size)
    return canvas


def _crop_banner(image, out):
    w, h = image.size
    crop = image.crop((0, 0, w, min(h, 300)))
    crop.save(out)
    print('saved', out, crop.size)


def _cancel_after(root):
    try:
        for item in root.tk.splitlist(root.tk.call('after', 'info')):
            root.after_cancel(item)
    except tk.TclError:
        pass


def capture(lang, out, width, height, states):
    import analytics
    import app as appmod
    import batch_ui
    import wan_core

    folder = tempfile.TemporaryDirectory()
    data = Path(folder.name)
    patches = [
        patch.object(appmod, 'DATA_DIR', data),
        patch.object(batch_ui, 'CHARACTER_SETTING_PATH', data / 'c.json'),
        patch.object(i18n, 'SETTINGS_PATH', data / 'settings.json'),
        patch.object(wan_core, 'DATA_DIR', data),
        patch.object(update_check, 'DATA_DIR', data),
    ]
    for item in patches:
        item.start()
    i18n.set_language(lang)
    root = tk.Tk()
    gui = appmod.App(root)
    root.geometry('%dx%d+40+40' % (width, height))
    root.attributes('-topmost', True)
    root.lift()
    root.focus_force()
    try:
        analytics.set_consent(False)
        gui._refresh_analytics_ui()
    except Exception:
        pass
    gui.status.set(i18n.t('status_idle'))
    offer = update_check.fixture_release(platform='win32', version='1.18.0')
    gui._update_dismissed_session = False
    gui._update_dest = None
    gui._update_phase = None
    gui._show_update_offer(offer)
    root.update()
    time.sleep(0.4)
    root.update()

    def shoot(name):
        root.update_idletasks()
        root.update()
        time.sleep(0.2)
        raw_path = out / ('_raw-%s-%s.png' % (lang, name))
        final_path = out / ('%s-%s.png' % (lang, name))
        image = _grab(raw_path, _bbox(root))
        items = collect_widgets(root)
        (out / ('%s-%s-layout.json' % (lang, name))).write_text(
            json.dumps(items, ensure_ascii=False, indent=2), encoding='utf-8')
        painted = paint(image, items, final_path)
        _crop_banner(painted, out / ('%s-%s-crop.png' % (lang, name)))

    if 'offer' in states:
        shoot('offer')
    if 'downloading' in states:
        gui._update_phase = 'downloading'
        gui._update_progress_text = '12 MB / 45 MB'
        gui._refresh_update_banner_copy()
        shoot('downloading')
    if 'ready-win' in states:
        dest = data / 'Downloads' / 'PowerTokensVideoStudio-1.18.0.exe'
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b'preview')
        with patch.object(appmod.sys, 'platform', 'win32'):
            gui._update_ready(str(dest))
            shoot('ready-win')
    if 'ready-mac' in states:
        dest = data / 'Downloads' / 'PowerTokensVideoStudio-macOS-arm64.dmg'
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b'preview')
        with patch.object(appmod.sys, 'platform', 'darwin'):
            gui._update_ready(str(dest))
            shoot('ready-mac')

    _cancel_after(root)
    root.destroy()
    for item in patches:
        item.stop()
    folder.cleanup()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default='/workspace/video-studio-update-check-preview')
    parser.add_argument('--lang', nargs='+', default=['zh', 'en'])
    parser.add_argument('--width', type=int, default=1280)
    parser.add_argument('--height', type=int, default=860)
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    states = ('offer', 'downloading', 'ready-win', 'ready-mac')
    for lang in args.lang:
        print('capturing', lang)
        capture(lang, out, args.width, args.height, states)


if __name__ == '__main__':
    main()

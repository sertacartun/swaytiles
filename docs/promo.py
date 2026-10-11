"""The swaytiles promo and the README's pictures: drawn windows on the
trees swaytiles builds, timed to a soundtrack made here as well.

    python3 docs/promo.py assets           # the logo, the layouts, hero.webp and social.png in docs/
    python3 docs/promo.py video FILE.mp4   # the promo with its sound
    python3 docs/promo.py still 2.3 9.4    # single frames as PNG, for looking

It needs pycairo, numpy, scipy, ffmpeg and the Poppins, Fira Sans and
JetBrains Mono Nerd Font fonts.
"""

import bisect
import math
import os
import random
import re
import subprocess
import sys
import wave
from multiprocessing import Pool
from pathlib import Path

import cairo
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import swaytiles  # noqa: E402

W, H, FPS = 1920, 1080, 60
BPM = 120
BEAT = 60 / BPM
BAR = 4 * BEAT
LENGTH = 13 * BAR
DROP = 2 * BAR

UI = "Fira Sans"
DISPLAY = "Poppins"
MONO = "JetBrainsMono Nerd Font"

VIOLET = (0.49, 0.36, 1.0)
CYAN = (0.0, 0.83, 1.0)
PINK = (1.0, 0.36, 0.65)
AMBER = (1.0, 0.72, 0.30)
GREEN = (0.36, 0.90, 0.55)
INK = (0.93, 0.95, 1.0)
DIM = (0.55, 0.60, 0.75)

SCREEN = (200, 175, 1520, 855)
BAR_H = 34
GAP = 12
PROMPT = "\u276f"


# ---------------------------------------------------------------- easing

def clamp(value, low=0.0, high=1.0):
    return max(low, min(high, value))


def linear(p):
    return p


def out_cubic(p):
    return 1 - (1 - p) ** 3


def in_out_cubic(p):
    return 4 * p ** 3 if p < 0.5 else 1 - (-2 * p + 2) ** 3 / 2


def out_expo(p):
    return 1 if p >= 1 else 1 - 2 ** (-10 * p)


def out_back(p, s=1.70158):
    p -= 1
    return 1 + (s + 1) * p ** 3 + s * p ** 2


def mix(a, b, p):
    if isinstance(a, tuple):
        return tuple(x + (y - x) * p for x, y in zip(a, b, strict=True))
    return a + (b - a) * p


def window_of(t, start, length):
    return clamp((t - start) / length)


class Track:
    """A value that moves to each new target from wherever it is then."""

    def __init__(self, value):
        self.keys = [(-1e9, value, 0.0, linear)]

    def to(self, t, value, length=0.45, ease=out_expo):
        bisect.insort(self.keys, (t, value, length, ease), key=lambda key: key[0])
        return self

    def __call__(self, t):
        value = self.keys[0][1]
        for start, target, length, ease in self.keys[1:]:
            if t < start:
                break
            value = mix(value, target, ease(clamp((t - start) / length)) if length > 0 else 1)
        return value


# ---------------------------------------------------------------- drawing helpers

def rounded(ctx, x, y, w, h, r):
    r = max(0, min(r, w / 2, h / 2))
    ctx.new_sub_path()
    ctx.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    ctx.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    ctx.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    ctx.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    ctx.close_path()


def gradient(x0, y0, x1, y1, *stops):
    pattern = cairo.LinearGradient(x0, y0, x1, y1)
    for offset, color, *alpha in stops:
        pattern.add_color_stop_rgba(offset, *color, alpha[0] if alpha else 1)
    return pattern


def font(ctx, family, size, bold=False):
    ctx.select_font_face(family, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
    ctx.set_font_size(size)


def text(ctx, x, y, string, color, alpha=1.0, anchor="left"):
    extents = ctx.text_extents(string)
    if anchor == "center":
        x -= extents.x_advance / 2
    elif anchor == "right":
        x -= extents.x_advance
    ctx.set_source_rgba(*color, alpha)
    ctx.move_to(x, y)
    ctx.show_text(string)
    return extents.x_advance


def glow(ctx, x, y, w, h, r, color, strength):
    for step in range(10, 0, -1):
        spread = step * 2.6
        rounded(ctx, x - spread, y - spread, w + 2 * spread, h + 2 * spread, r + spread)
        ctx.set_source_rgba(*color, strength * 0.05 * (1 - step / 11))
        ctx.set_line_width(2.6)
        ctx.stroke()


def shadow(ctx, x, y, w, h, r, strength):
    for step in range(8, 0, -1):
        spread = step * 3.5
        rounded(ctx, x - spread * 0.6, y - spread * 0.2 + 10, w + spread * 1.2, h + spread * 1.2, r + spread)
        ctx.set_source_rgba(0, 0, 0, strength * 0.035)
        ctx.fill()


# ---------------------------------------------------------------- layouts, from swaytiles itself

TITLE_H = 28


def arrange(name, ids, area, tree=None, active=None):
    """Each window's rectangle and whether it shows, the tab and stacked
    title bars, and the drawing order, for the tree swaytiles builds (or
    the tree given, as sway would draw it with `active` focused)."""
    tree = tree or swaytiles.normalize(swaytiles.LAYOUTS[name](list(ids)))
    rects, bars = {}, []
    x, y, w, h = area

    def place(node, x, y, w, h):
        if isinstance(node, int):
            rects[node] = ((x + GAP / 2, y + GAP / 2, w - GAP, h - GAP), 1.0, True)
            return
        layout, children = node
        count = len(children)
        if layout in ("tabbed", "stacked"):
            rows = 1 if layout == "tabbed" else count
            bar = rows * TITLE_H
            frame = (x + GAP / 2, y + GAP / 2, w - GAP, h - GAP)
            shown = active if active in children else children[0]
            bars.append((layout, frame, [leaf for leaf in children if isinstance(leaf, int)], shown))
            for child in children:
                if isinstance(child, int):
                    rects[child] = ((frame[0], frame[1] + bar, frame[2], frame[3] - bar), 1.0 if child == shown else 0.0, False)
            return
        if layout == "float":
            step = 0.4 / max(count - 1, 1)
            for index, child in enumerate(children):
                k = count - 1 - index
                rects[child] = ((x + k * step * w + 20, y + k * step * h + 16, w * 0.6 - 40, h * 0.6 - 32), 1.0, True)
            return
        for index, child in enumerate(children):
            if layout == "splith":
                place(child, x + index * w / count, y, w / count, h)
            else:
                place(child, x, y + index * h / count, w, h / count)

    place(tree, x + GAP / 2, y + GAP / 2, w - GAP, h - GAP)
    order = list(reversed(ids)) if name == "float" else list(ids)
    return rects, bars, order


# ---------------------------------------------------------------- windows

CODE = '''def tile(outer, inner, master_last=False):
    def arrange(ids):
        stack = (inner, ids[1:])
        if master_last:
            return (outer, [stack, ids[0]])
        return (outer, [ids[0], stack])
    return arrange


def centered(ids):
    if len(ids) < 3:
        return tile("splith", "splitv")(ids)
    return ("splith", [("splitv", ids[2::2]),
                       ids[0], ("splitv", ids[1::2])])


def grid(ids):
    columns = math.ceil(math.sqrt(len(ids)))
    return ("splitv", [("splith", ids[row:row + columns])
                       for row in range(0, len(ids), columns)])
'''.splitlines()

LOG = [
    ("a47f2f8", "Windows cross to the next output"),
    ("8b82741", "Windows keep the sizes they were given"),
    ("b33e7ae", "Closing is drawn in one step"),
    ("f58aa7f", "Layout pictures read on any theme"),
    ("4d7c85b", "One script installs and updates"),
    ("c890e51", "The menu keeps its order in wofi"),
    ("deaed6a", "Add CONTRIBUTING"),
]

TOKEN = re.compile(r'(?P<kw>\b(?:def|return|if|for|in|range|len|False)\b)|(?P<str>"[^"]*")|(?P<num>\b\d+\b)|(?P<fn>\b\w+(?=\())|(?P<word>\w+)|(?P<other>.)')


class Window:
    def __init__(self, id, kind, title, accent):
        self.id, self.kind, self.title, self.accent = id, kind, title, accent
        self.rect = Track((0.0, 0.0, 10.0, 10.0))
        self.shown = Track(0.0)      # opening and closing
        self.visible = Track(1.0)    # hidden behind a tab
        self.titled = Track(1.0)     # own title bar, or the tabs'
        self.focus = Track(0.0)
        self.opened = 0.0
        self.random = random.Random(id)

    def draw(self, ctx, t, scene):
        shown = self.shown(t)
        alpha = shown * self.visible(t)
        if alpha <= 0.01:
            return
        x, y, w, h = self.rect(t)
        scale = 0.55 + 0.45 * out_back(clamp(shown)) if shown < 1 else 1.0
        cx, cy = x + w / 2, y + h / 2
        ctx.save()
        ctx.translate(cx, cy)
        ctx.scale(scale, scale)
        ctx.translate(-cx, -cy)
        focus = self.focus(t)
        shadow(ctx, x, y, w, h, 12, alpha)
        if focus > 0.01:
            glow(ctx, x, y, w, h, 12, self.accent, focus * alpha)
        ctx.push_group()
        rounded(ctx, x, y, w, h, 12)
        ctx.set_source_rgb(0.07, 0.085, 0.15)
        ctx.fill_preserve()
        ctx.clip()
        bar = TITLE_H * self.titled(t)
        if bar > 0.5:
            ctx.rectangle(x, y, w, bar)
            ctx.set_source_rgb(0.10, 0.12, 0.21)
            ctx.fill()
            font(ctx, UI, 14, bold=True)
            ctx.save()
            ctx.rectangle(x, y, w, bar)
            ctx.clip()
            ctx.arc(x + 16, y + bar / 2, 5, 0, 2 * math.pi)
            ctx.set_source_rgb(*self.accent)
            ctx.fill()
            text(ctx, x + w / 2, y + bar / 2 + 5, self.title, INK if focus > 0.5 else DIM, 0.9, "center")
            ctx.restore()
        ctx.save()
        ctx.rectangle(x, y + bar, w, h - bar)
        ctx.clip()
        getattr(self, "draw_" + self.kind)(ctx, t, x, y + bar, w, h - bar)
        ctx.restore()
        if focus < 0.99:
            rounded(ctx, x, y, w, h, 12)
            ctx.set_source_rgba(0.02, 0.03, 0.08, 0.28 * (1 - focus))
            ctx.fill()
        ctx.pop_group_to_source()
        ctx.paint_with_alpha(alpha)
        rounded(ctx, x + 0.75, y + 0.75, w - 1.5, h - 1.5, 11.5)
        if focus > 0.01:
            ctx.set_source(gradient(x, y, x + w, y + h, (0, self.accent, alpha * focus), (1, CYAN, alpha * focus)))
            ctx.set_line_width(2.5)
        else:
            ctx.set_source_rgba(1, 1, 1, 0.08 * alpha)
            ctx.set_line_width(1.5)
        ctx.stroke()
        ctx.restore()

    # ---- contents

    def draw_browser(self, ctx, t, x, y, w, h):
        ctx.rectangle(x, y, w, h)
        ctx.set_source_rgb(0.95, 0.96, 0.99)
        ctx.fill()
        rounded(ctx, x + 14, y + 10, min(w - 28, 520), 28, 14)
        ctx.set_source_rgb(0.88, 0.90, 0.96)
        ctx.fill()
        font(ctx, UI, 14)
        text(ctx, x + 32, y + 29, "swaywm.org", (0.35, 0.38, 0.5))
        top = y + 56
        ctx.rectangle(x, top, w, min(260, h * 0.5))
        ctx.set_source(gradient(x, top, x + w, top + 260, (0, VIOLET), (0.6, (0.30, 0.45, 1.0)), (1, CYAN)))
        ctx.fill()
        for i in range(3):
            ctx.arc(x + w * (0.72 + 0.1 * i), top + 60 + 40 * math.sin(t * 1.3 + i), 90 - 20 * i, 0, 2 * math.pi)
            ctx.set_source_rgba(1, 1, 1, 0.08)
            ctx.fill()
        font(ctx, DISPLAY, 46, bold=True)
        text(ctx, x + 40, top + 90, "sway", (1, 1, 1))
        font(ctx, UI, 18)
        text(ctx, x + 42, top + 122, "i3-compatible Wayland compositor", (1, 1, 1), 0.85)
        rounded(ctx, x + 42, top + 145, 130, 38, 19)
        ctx.set_source_rgb(1, 1, 1)
        ctx.fill()
        font(ctx, UI, 15, bold=True)
        text(ctx, x + 107, top + 169, "Get started", VIOLET, 1, "center")
        card_y = top + 290
        columns = max(1, int((w - 40) // 260))
        for i in range(columns * 2):
            cx = x + 20 + (i % columns) * ((w - 40) / columns)
            cy = card_y + (i // columns) * 150
            cw = (w - 40) / columns - 16
            rounded(ctx, cx, cy, cw, 130, 12)
            ctx.set_source_rgb(1, 1, 1)
            ctx.fill()
            rounded(ctx, cx + 16, cy + 18, 36, 36, 9)
            ctx.set_source_rgb(*(VIOLET, CYAN, PINK, AMBER)[i % 4])
            ctx.fill()
            for j, length in enumerate((0.7, 0.9, 0.5)):
                rounded(ctx, cx + 16, cy + 70 + j * 16, (cw - 32) * length, 8, 4)
                ctx.set_source_rgb(0.84, 0.86, 0.92)
                ctx.fill()

    def draw_code(self, ctx, t, x, y, w, h):
        ctx.rectangle(x, y, w, h)
        ctx.set_source_rgb(0.06, 0.07, 0.12)
        ctx.fill()
        font(ctx, MONO, 15)
        colors = {"kw": (0.78, 0.55, 1.0), "str": (0.60, 0.90, 0.55), "num": AMBER, "fn": (0.45, 0.75, 1.0),
                  "word": (0.85, 0.88, 0.95), "other": (0.62, 0.66, 0.78)}
        for row, line in enumerate(CODE):
            ly = y + 24 + row * 22
            if ly > y + h + 20:
                break
            if row == 6:
                ctx.rectangle(x, ly - 16, w, 22)
                ctx.set_source_rgba(1, 1, 1, 0.04)
                ctx.fill()
            text(ctx, x + 40, ly, str(row + 1), (0.35, 0.38, 0.5), 1, "right")
            cx = x + 56
            for match in TOKEN.finditer(line):
                cx += text(ctx, cx, ly, match.group(), colors[match.lastgroup])
        if (t * 2) % 1 < 0.6:
            ctx.rectangle(x + 56 + 9 * 23, y + 24 + 6 * 22 - 15, 2, 19)
            ctx.set_source_rgb(*CYAN)
            ctx.fill()
        ctx.rectangle(x, y + h - 24, w, 24)
        ctx.set_source_rgb(*VIOLET)
        ctx.fill()
        font(ctx, MONO, 13, bold=True)
        text(ctx, x + 12, y + h - 7, "NORMAL  swaytiles.py", (1, 1, 1))

    def draw_term(self, ctx, t, x, y, w, h):
        ctx.rectangle(x, y, w, h)
        ctx.set_source_rgb(0.035, 0.04, 0.075)
        ctx.fill()
        font(ctx, MONO, 16)
        command = "git log --oneline"
        typed = command[:int(clamp((t - self.opened - 0.25) * 28, 0, len(command)))]
        ly = y + 30
        width = text(ctx, x + 16, ly, PROMPT + " ", GREEN)
        width += text(ctx, x + 16 + width, ly, "~/swaytiles ", CYAN)
        text(ctx, x + 16 + width, ly, typed, INK)
        done = t - self.opened - 0.25 - len(command) / 28
        rows = int(clamp(done * 20, 0, len(LOG))) if done > 0.1 else 0
        for row, (sha, message) in enumerate(LOG[:rows]):
            ly = y + 58 + row * 24
            offset = text(ctx, x + 16, ly, sha + " ", AMBER)
            text(ctx, x + 16 + offset, ly, message, (0.80, 0.83, 0.92))
        if rows == len(LOG):
            ly = y + 58 + rows * 24 + 10
            width = text(ctx, x + 16, ly, PROMPT + " ", GREEN)
            if (t * 2) % 1 < 0.6:
                ctx.rectangle(x + 16 + width, ly - 15, 9, 19)
                ctx.set_source_rgb(*INK)
                ctx.fill()

    def draw_files(self, ctx, t, x, y, w, h):
        ctx.rectangle(x, y, w, h)
        ctx.set_source_rgb(0.09, 0.10, 0.17)
        ctx.fill()
        side = min(170, w * 0.3)
        ctx.rectangle(x, y, side, h)
        ctx.set_source_rgb(0.07, 0.08, 0.14)
        ctx.fill()
        font(ctx, UI, 14)
        for i, place in enumerate(("Home", "Documents", "Downloads", "Music", "Pictures")):
            if i == 1:
                rounded(ctx, x + 8, y + 14 + i * 32, side - 16, 28, 7)
                ctx.set_source_rgba(*AMBER, 0.22)
                ctx.fill()
            text(ctx, x + 22, y + 33 + i * 32, place, INK if i == 1 else DIM)
        names = ["docs", "tests", "gifs", ".github", "swaytiles.py", "install.sh", "README.md", "LICENSE"]
        columns = max(1, int((w - side - 20) // 110))
        for i, name in enumerate(names):
            gx = x + side + 20 + (i % columns) * 110
            gy = y + 18 + (i // columns) * 110
            folder = "." not in name or name == ".github"
            if folder:
                rounded(ctx, gx + 18, gy + 8, 30, 14, 4)
                ctx.set_source_rgb(0.85, 0.60, 0.20)
                ctx.fill()
                rounded(ctx, gx + 18, gy + 14, 66, 48, 7)
                ctx.set_source(gradient(gx, gy, gx, gy + 60, (0, (1.0, 0.80, 0.35)), (1, AMBER)))
                ctx.fill()
            else:
                rounded(ctx, gx + 28, gy + 6, 46, 58, 6)
                ctx.set_source_rgb(0.80, 0.84, 0.95)
                ctx.fill()
                for j in range(3):
                    rounded(ctx, gx + 36, gy + 22 + j * 10, 30, 4, 2)
                    ctx.set_source_rgb(0.55, 0.60, 0.75)
                    ctx.fill()
            font(ctx, UI, 13)
            text(ctx, gx + 51, gy + 84, name, INK, 0.9, "center")

    def draw_music(self, ctx, t, x, y, w, h):
        ctx.rectangle(x, y, w, h)
        ctx.set_source(gradient(x, y, x, y + h, (0, (0.16, 0.07, 0.22)), (1, (0.06, 0.05, 0.12))))
        ctx.fill()
        art = min(w - 40, h - 120, 220)
        if art > 30:
            ax, ay = x + (w - art) / 2, y + 22
            rounded(ctx, ax, ay, art, art, 14)
            ctx.set_source(gradient(ax, ay, ax + art, ay + art, (0, PINK), (0.5, VIOLET), (1, CYAN)))
            ctx.fill()
            pulse = 1 + 0.06 * math.exp(-((t % BEAT) / BEAT) * 6)
            for ring in range(4):
                ctx.arc(ax + art / 2, ay + art / 2, art * (0.12 + 0.09 * ring) * pulse, 0, 2 * math.pi)
                ctx.set_source_rgba(1, 1, 1, 0.25 - ring * 0.05)
                ctx.set_line_width(2)
                ctx.stroke()
            ty = ay + art + 34
        else:
            ty = y + 30
        font(ctx, UI, 17, bold=True)
        text(ctx, x + w / 2, ty, "Night Tiles", INK, 1, "center")
        font(ctx, UI, 13)
        text(ctx, x + w / 2, ty + 20, "Synthwave for sway", DIM, 1, "center")
        bars = 18
        bw = (w - 60) / bars
        for i in range(bars):
            level = 0.25 + 0.75 * abs(math.sin(t * (3 + i * 0.37) + i)) * math.exp(-((t % BEAT) / BEAT) * 2.5)
            bh = 36 * level
            rounded(ctx, x + 30 + i * bw + 2, ty + 70 - bh, bw - 4, bh, 2)
            ctx.set_source(gradient(0, ty + 34, 0, ty + 70, (0, PINK), (1, VIOLET)))
            ctx.fill()
        rounded(ctx, x + 30, ty + 86, w - 60, 5, 2.5)
        ctx.set_source_rgba(1, 1, 1, 0.15)
        ctx.fill()
        rounded(ctx, x + 30, ty + 86, (w - 60) * ((t / LENGTH) * 0.8 + 0.1), 5, 2.5)
        ctx.set_source_rgb(*PINK)
        ctx.fill()

    def draw_chat(self, ctx, t, x, y, w, h):
        ctx.rectangle(x, y, w, h)
        ctx.set_source_rgb(0.08, 0.09, 0.15)
        ctx.fill()
        messages = [(False, "did you try swaytiles?"), (True, "yes! master + grid"), (False, "per workspace??"),
                    (True, "per workspace :)"), (False, "installing now")]
        shown = int(clamp((t - self.opened - 0.2) * 4, 0, len(messages)))
        font(ctx, UI, 15)
        for i, (mine, message) in enumerate(messages[:shown]):
            width = ctx.text_extents(message).x_advance + 28
            bx = x + w - width - 16 if mine else x + 16
            by = y + 18 + i * 46
            rounded(ctx, bx, by, width, 34, 17)
            if mine:
                ctx.set_source(gradient(bx, by, bx + width, by, (0, VIOLET), (1, CYAN)))
            else:
                ctx.set_source_rgb(0.17, 0.19, 0.30)
            ctx.fill()
            text(ctx, bx + 14, by + 22, message, INK)


# ---------------------------------------------------------------- scene

class Scene:
    def __init__(self):
        self.windows = {}
        self.order = []           # tiling order, master first
        self.z = []
        self.layout = "master"
        self.layouts = [(-1e9, "default")]
        self.bars = [(-1e9, [])]
        self.focused = [(-1e9, None)]
        self.captions = []
        self.keys = []
        self.chips = []
        self.camera = Track((1.0, W / 2, H / 2))
        self.screen = Track(1.0)      # 1 shown, 0 pushed back
        self.brand = Track(0.0)
        self.outro = Track(0.0)
        self.menu = Track(0.0)
        self.selection = Track(3.0)
        self.menu_opened = 0.0
        self.cursor = Track((W + 60.0, H + 60.0))
        self.clicks = []
        self.sounds = []           # (time, kind, extra)
        self.snapped = Track(0.0)

    def area(self):
        x, y, w, h = SCREEN
        return (x, y + BAR_H, w, h - BAR_H)

    def add(self, window):
        self.windows[window.id] = window

    def tile(self, t, length=0.5, ease=out_expo, layout=None, tree=None, active=None):
        if layout:
            self.layout = layout
            self.layouts.append((t, layout))
        rects, bars, z = arrange(self.layout, self.order, self.area(), tree, active)
        for id, (rect, visible, titled) in rects.items():
            window = self.windows[id]
            window.rect.to(t, rect, length, ease)
            window.visible.to(t, visible, 0.1 if visible == 0 else length * 0.6, out_cubic)
            window.titled.to(t, 1.0 if titled else 0.0, 0.12, out_cubic)
        self.bars.append((t, bars))
        self.z.append((t, z))

    def focus(self, t, id):
        for window in self.windows.values():
            window.focus.to(t, 1.0 if window.id == id else 0.0, 0.3, out_cubic)
        self.focused.append((t, id))

    def at(self, history, t):
        index = bisect.bisect_right([entry[0] for entry in history], t) - 1
        return history[max(index, 0)]

    def caption(self, start, end, string, accent=None):
        self.captions.append((start, end, string, accent))

    def press(self, t, *labels):
        self.keys.append((t, labels))
        self.sounds.append((t, "key", None))


def build(brand=True):
    s = Scene()
    kinds = [
        (1, "browser", "swaywm.org", VIOLET),
        (2, "code", "swaytiles.py — nvim", CYAN),
        (3, "term", "~/swaytiles — foot", GREEN),
        (4, "files", "~/Documents", AMBER),
        (5, "music", "Night Tiles", PINK),
    ]
    for id, kind, title, accent in kinds:
        s.add(Window(id, kind, title, accent))
    s.add(Window(6, "chat", "Chat", (0.45, 0.75, 1.0)))
    for window in s.windows.values():
        window.opened = -2.0

    # Hook: plain sway. Each new window squeezes in beside the last, and
    # anything better is split and tabbed by hand.
    s.order = [1, 2, 3, 4]
    s.focus(-1, 1)
    s.windows[1].shown.to(-1, 1.0, 0)
    s.tile(-1, 0, tree=("splith", [1]))
    for index, id in enumerate((2, 3, 4)):
        t = (index + 1) * BEAT
        s.press(t - 0.05, "Super", "Enter")
        s.windows[id].opened = t
        s.windows[id].shown.to(t, 1.0, 0.45, linear)
        s.tile(t, 0.4, tree=("splith", [1, 2, 3, 4][:index + 2]))
        s.focus(t, id)
        s.sounds.append((t, "pop", None))
    s.caption(0.1, 1.95, "New windows just squeeze in.", "squeeze in.")
    s.camera.to(-1, (1.04, W / 2, H / 2 + 10), 0)
    s.press(2.0, "Super", "W")
    s.tile(2.05, 0.35, tree=("tabbed", [1, 2, 3, 4]), active=4)
    s.press(2.5, "Super", "E")
    s.tile(2.55, 0.35, tree=("splith", [1, 2, 3, 4]))
    s.press(3.0, "Super", "V")
    s.press(3.25, "Super", "Enter")
    s.order.append(5)
    s.windows[5].opened = 3.3
    s.windows[5].shown.to(3.3, 1.0, 0.45, linear)
    s.tile(3.3, 0.4, tree=("splith", [1, 2, 3, ("splitv", [4, 5])]))
    s.focus(3.3, 5)
    s.sounds.append((3.3, "pop", None))
    s.press(3.6, "Super", "S")
    s.tile(3.65, 0.3, tree=("splith", [1, 2, 3, ("stacked", [4, 5])]), active=5)
    s.caption(2.0, 3.95, "Still splitting and tabbing by hand?", "by hand?")

    # The drop: swaytiles lays them out.
    t = DROP
    s.sounds.append((t, "impact", None))
    s.tile(t, 0.55, lambda p: out_back(p, 1.2), layout="master")
    s.focus(t, 1)
    s.camera.to(t, (1.0, W / 2, H / 2), 0.7, out_expo)
    s.caption(t + 0.08, t + 1.9, "Let swaytiles lay them out.", "lay them out.")

    # Brand.
    if brand:
        s.screen.to(6.0, 0.0, 0.6, out_expo)
        s.brand.to(6.0, 1.0, 0.5, out_expo)
        s.sounds.append((6.0, "whoosh", None))
        s.brand.to(7.75, 0.0, 0.35, out_cubic)
        s.screen.to(7.8, 1.0, 0.5, out_expo)

    # Open and close.
    s.caption(8.0, 9.95, "Open. Close. Always tidy.", "Always tidy.")
    s.press(8.25, "Super", "Enter")
    s.order.append(6)
    s.windows[6].opened = 8.3
    s.windows[6].shown.to(8.3, 1.0, 0.5, linear)
    s.tile(8.3, 0.5)
    s.focus(8.3, 6)
    s.sounds.append((8.3, "pop", 0))
    s.focus(8.95, 4)
    s.press(9.25, "Super", "Shift", "Q")
    s.windows[4].shown.to(9.3, 0.0, 0.28, out_cubic)
    s.sounds.append((9.3, "close", None))
    s.order.remove(4)
    s.tile(9.38, 0.5)
    s.focus(9.38, 6)

    # The menu.
    s.caption(10.0, 13.9, "Pick a layout for each workspace.", "each workspace")
    s.press(10.0, "Super", "Shift", "T")
    s.menu.to(10.1, 1.0, 0.45, out_expo)
    s.menu_opened = 10.1
    s.sounds.append((10.1, "open", None))
    s.camera.to(10.1, (1.1, W / 2, H / 2 + 10), 1.2, in_out_cubic)
    for t, index in ((11.0, 4.0), (11.5, 5.0)):
        s.press(t, "↓")
        s.selection.to(t, index, 0.3, out_expo)
    s.press(12.0, "Enter")
    s.menu.to(12.05, 0.0, 0.3, out_cubic)
    s.sounds.append((12.05, "impact", None))
    s.tile(12.1, 0.6, lambda p: out_back(p, 1.1), layout="wide")
    s.camera.to(12.1, (1.0, W / 2, H / 2), 0.9, out_expo)

    # The montage, one layout a beat.
    s.caption(14.0, 17.95, "12 layouts. One key away.", "12 layouts.")
    montage = ["centered", "grid", "dwindle", "spiral", "tabbed-master", "stacked-master", "float", "master"]
    for index, name in enumerate(montage):
        t = 14.0 + index * BEAT
        s.tile(t, 0.38, out_expo, layout=name)
        s.chips.append((t, name))
        s.sounds.append((t, "tick", index))

    # Swap.
    s.caption(18.0, 19.95, "Swap anything into the spotlight.", "spotlight.")
    rects = arrange("master", s.order, s.area())[0]
    tx, ty, tw, th = rects[3][0]
    s.cursor.to(17.9, (W - 260.0, H - 120.0), 0)
    s.cursor.to(18.0, (tx + tw * 0.55, ty + th * 0.5), 0.55, in_out_cubic)
    s.clicks.append(18.6)
    s.sounds.append((18.6, "click", None))
    s.focus(18.6, 3)
    s.press(19.0, "Super", "M")
    s.order.remove(3)
    s.order.insert(0, 3)
    s.tile(19.05, 0.6, lambda p: out_back(p, 1.1))
    s.sounds.append((19.05, "swap", None))
    s.cursor.to(19.3, (W + 80.0, H + 80.0), 0.6, in_out_cubic)

    # Call to action.
    s.screen.to(20.0, 0.0, 0.8, out_expo)
    s.outro.to(20.0, 1.0, 0.6, out_expo)
    s.sounds.append((20.0, "whoosh", None))
    s.sounds.append((20.0, "impact", None))
    return s


# ---------------------------------------------------------------- frame

def background(ctx, t):
    ctx.set_source_rgb(0.035, 0.045, 0.09)
    ctx.paint()
    blobs = [(VIOLET, 0.25, 0.3, 700), (CYAN, 0.8, 0.75, 650), (PINK, 0.65, 0.15, 520)]
    for i, (color, bx, by, radius) in enumerate(blobs):
        cx = W * bx + 80 * math.sin(t * 0.4 + i * 2)
        cy = H * by + 60 * math.cos(t * 0.33 + i)
        pattern = cairo.RadialGradient(cx, cy, 0, cx, cy, radius)
        pattern.add_color_stop_rgba(0, *color, 0.30)
        pattern.add_color_stop_rgba(1, *color, 0)
        ctx.set_source(pattern)
        ctx.paint()
    ctx.set_source_rgba(1, 1, 1, 0.035)
    for gx in range(0, W, 48):
        for gy in range(0, H, 48):
            ctx.rectangle(gx, gy, 2, 2)
    ctx.fill()


def wallpaper(ctx, x, y, w, h, t):
    ctx.set_source(gradient(x, y, x + w, y + h, (0, (0.10, 0.07, 0.22)), (0.55, (0.05, 0.07, 0.16)), (1, (0.03, 0.12, 0.20))))
    ctx.paint()
    for i in range(3):
        cx = x + w * (0.2 + 0.3 * i)
        cy = y + h * (0.5 + 0.25 * math.sin(i * 1.7))
        pattern = cairo.RadialGradient(cx, cy, 0, cx, cy, 420)
        pattern.add_color_stop_rgba(0, *(VIOLET, CYAN, PINK)[i], 0.18)
        pattern.add_color_stop_rgba(1, *(VIOLET, CYAN, PINK)[i], 0)
        ctx.set_source(pattern)
        ctx.paint()


def swaybar(ctx, scene, t):
    x, y, w, _ = SCREEN
    ctx.rectangle(x, y, w, BAR_H)
    ctx.set_source_rgba(0.02, 0.03, 0.07, 0.85)
    ctx.fill()
    font(ctx, UI, 15, bold=True)
    for i, name in enumerate(("1", "2", "3", "4")):
        px = x + 10 + i * 36
        rounded(ctx, px, y + 6, 30, BAR_H - 12, 6)
        if i == 0:
            ctx.set_source(gradient(px, 0, px + 30, 0, (0, VIOLET), (1, CYAN)))
        else:
            ctx.set_source_rgba(1, 1, 1, 0.06)
        ctx.fill()
        text(ctx, px + 15, y + 23, name, INK if i == 0 else DIM, 1, "center")
    focused = scene.at(scene.focused, t)[1]
    if focused:
        font(ctx, UI, 15)
        text(ctx, x + w / 2, y + 23, scene.windows[focused].title, INK, 0.85, "center")
    since, layout = scene.at(scene.layouts, t)
    pop = out_back(window_of(t, since, 0.35)) if since > 0 else 1
    font(ctx, MONO, 14, bold=True)
    width = ctx.text_extents(layout).x_advance + 58
    px = x + w - width - 96
    ctx.save()
    ctx.translate(px + width / 2, y + BAR_H / 2)
    ctx.scale(0.8 + 0.2 * pop, 0.8 + 0.2 * pop)
    rounded(ctx, -width / 2, -11, width, 22, 11)
    ctx.set_source_rgba(*VIOLET, 0.35)
    ctx.fill()
    mini(ctx, layout, -width / 2 + 10, -7, 23, 14, True, t, False)
    text(ctx, -width / 2 + 42, 5, layout, INK)
    ctx.restore()
    font(ctx, UI, 15, bold=True)
    text(ctx, x + w - 16, y + 23, "21:30", INK, 0.9, "right")


def title_bars(ctx, scene, t):
    since, bars = scene.at(scene.bars, t)
    alpha = window_of(t, since, 0.25)
    for layout, (x, y, w, _), leaves, active in bars:
        rows = 1 if layout == "tabbed" else len(leaves)
        for index, leaf in enumerate(leaves):
            window = scene.windows[leaf]
            if layout == "tabbed":
                bx, by, bw, bh = x + index * w / len(leaves), y, w / len(leaves), TITLE_H
            else:
                bx, by, bw, bh = x, y + index * TITLE_H, w, TITLE_H
            selected = leaf == active
            ctx.save()
            rounded(ctx, x, y, w, rows * TITLE_H + 12, 12)
            ctx.clip()
            ctx.rectangle(bx, by, bw, bh)
            ctx.set_source_rgba(*((0.20, 0.17, 0.42) if selected else (0.10, 0.12, 0.21)), alpha)
            ctx.fill()
            ctx.rectangle(bx, by + bh - 1, bw, 1)
            ctx.set_source_rgba(0, 0, 0, 0.4 * alpha)
            ctx.fill()
            ctx.arc(bx + 14, by + bh / 2, 4.5, 0, 2 * math.pi)
            ctx.set_source_rgba(*window.accent, alpha)
            ctx.fill()
            font(ctx, UI, 13, bold=selected)
            ctx.rectangle(bx, by, bw - 6, bh)
            ctx.clip()
            text(ctx, bx + 26, by + bh / 2 + 5, window.title, INK if selected else DIM, alpha)
            ctx.restore()


def keycaps(ctx, scene, t):
    current = [(start, labels) for start, labels in scene.keys if start <= t < start + 0.85]
    if not current:
        return
    start, labels = current[-1]
    age = t - start
    pop = out_back(clamp(age / 0.25))
    fade = 1 - clamp((age - 0.6) / 0.25)
    font(ctx, UI, 22, bold=True)
    widths = [max(64, ctx.text_extents(label).x_advance + 36) for label in labels]
    total = sum(widths) + 14 * (len(labels) - 1)
    x = W / 2 - total / 2
    y = H - 128
    for index, (label, width) in enumerate(zip(labels, widths, strict=True)):
        delay = clamp((age - index * 0.05) / 0.2)
        dy = (1 - out_back(delay)) * 30
        press = 4 * math.exp(-max(0, age - index * 0.05) * 12)
        ctx.save()
        ctx.translate(0, dy + press)
        shadow(ctx, x, y, width, 58, 12, fade * pop)
        rounded(ctx, x, y + 6, width, 58, 12)
        ctx.set_source_rgba(0.05, 0.06, 0.12, fade * delay)
        ctx.fill()
        rounded(ctx, x, y, width, 58, 12)
        ctx.set_source(gradient(x, y, x, y + 58, (0, (0.20, 0.22, 0.36), fade * delay), (1, (0.13, 0.14, 0.25), fade * delay)))
        ctx.fill_preserve()
        ctx.set_source(gradient(x, y, x + width, y + 58, (0, VIOLET, fade * delay), (1, CYAN, fade * delay)))
        ctx.set_line_width(2)
        ctx.stroke()
        text(ctx, x + width / 2, y + 37, label, INK, fade * delay, "center")
        ctx.restore()
        if index < len(labels) - 1:
            font(ctx, UI, 22, bold=True)
        x += width + 14


def caption(ctx, scene, t):
    for start, end, string, accent in scene.captions:
        if not start - 0.05 <= t < end:
            continue
        words = string.split(" ")
        accent_words = set(accent.split(" ")) if accent else set()
        font(ctx, DISPLAY, 54, bold=True)
        space = ctx.text_extents(" ").x_advance + 6
        widths = [ctx.text_extents(word).x_advance for word in words]
        x = W / 2 - (sum(widths) + space * (len(words) - 1)) / 2
        leaving = clamp((t - (end - 0.25)) / 0.25)
        for index, (word, width) in enumerate(zip(words, widths, strict=True)):
            p = out_back(clamp((t - start - index * 0.06) / 0.35))
            alpha = clamp((t - start - index * 0.06) / 0.2) * (1 - leaving)
            y = 118 + (1 - p) * 40 - leaving * 20
            color = gradient(x, 0, x + width, 0, (0, VIOLET), (1, CYAN)) if word in accent_words else None
            ctx.move_to(x, y)
            if color:
                ctx.set_source(gradient(x, 0, x + width, 0, (0, VIOLET, alpha), (1, CYAN, alpha)))
            else:
                ctx.set_source_rgba(*INK, alpha)
            ctx.show_text(word)
            x += width + space


def chip(ctx, scene, t):
    current = [(start, name) for start, name in scene.chips if start <= t]
    if not current or t > scene.chips[-1][0] + 0.9:
        return
    start, name = current[-1]
    age = t - start
    pop = out_back(clamp(age / 0.3), 2.2)
    fade = 1 - clamp((t - scene.chips[-1][0] - 0.6) / 0.3)
    x, y, w, h = SCREEN
    font(ctx, MONO, 30, bold=True)
    width = ctx.text_extents(name).x_advance + 60
    cx, cy = x + w / 2, y + h + 0 - 60
    ctx.save()
    ctx.translate(cx, cy)
    ctx.scale(0.7 + 0.3 * pop, 0.7 + 0.3 * pop)
    shadow(ctx, -width / 2, -32, width, 64, 32, fade)
    rounded(ctx, -width / 2, -32, width, 64, 32)
    ctx.set_source(gradient(-width / 2, 0, width / 2, 0, (0, VIOLET, 0.95 * fade), (1, CYAN, 0.95 * fade)))
    ctx.fill()
    text(ctx, 0, 11, name, (1, 1, 1), fade, "center")
    ctx.restore()


def cursor(ctx, scene, t):
    x, y = scene.cursor(t)
    if x > W or y > H:
        return
    for click in scene.clicks:
        age = t - click
        if 0 <= age < 0.5:
            ctx.arc(x, y, 10 + 50 * out_cubic(age / 0.5), 0, 2 * math.pi)
            ctx.set_source_rgba(*CYAN, 0.6 * (1 - age / 0.5))
            ctx.set_line_width(3)
            ctx.stroke()
    press = any(0 <= t - click < 0.15 for click in scene.clicks)
    s = 0.85 if press else 1.0
    ctx.save()
    ctx.translate(x, y)
    ctx.scale(1.25 * s, 1.25 * s)
    ctx.move_to(0, 0)
    for px, py in ((0, 22), (6, 17), (10, 26), (14, 24), (10, 15.5), (17, 15.5)):
        ctx.line_to(px, py)
    ctx.close_path()
    ctx.set_source_rgb(1, 1, 1)
    ctx.fill_preserve()
    ctx.set_source_rgb(0.05, 0.05, 0.1)
    ctx.set_line_width(1.5)
    ctx.stroke()
    ctx.restore()


def mini(ctx, name, x, y, w, h, colors, t, live):
    """A layout's picture, from the same boxes swaytiles draws its icons with."""
    layout = swaytiles.LAYOUTS[name]
    tree = swaytiles.normalize((layout or swaytiles.flat("splith"))(list(range(5))))
    rounded(ctx, x, y, w, h, 5)
    ctx.set_source_rgb(0.11, 0.12, 0.20)
    ctx.fill_preserve()
    ctx.set_source_rgba(1, 1, 1, 0.18)
    ctx.set_line_width(1)
    ctx.stroke()
    pulse = int(t / BEAT) % 5 if live else 0
    sx, sy = w / 48, h / 30
    for leaf, bx, by, bw, bh in swaytiles.boxes(tree, 3, 3, 42, 24):
        rounded(ctx, x + (bx + 0.75) * sx, y + (by + 0.75) * sy, (bw - 1.5) * sx, (bh - 1.5) * sy, 1.5)
        if colors:
            color = (VIOLET, CYAN, PINK, AMBER, GREEN)[leaf % 5]
            ctx.set_source_rgba(*color, 1.0 if leaf == pulse or layout is None else 0.75)
        else:
            ctx.set_source_rgba(0.62, 0.66, 0.78, 1.0 if leaf == 0 and layout is not None else 0.55)
        ctx.fill()


def menu(ctx, scene, t):
    shown = scene.menu(t)
    if shown <= 0.01:
        return
    names = list(swaytiles.LAYOUTS)
    row_h = 44
    w, h = 700, 74 + row_h * len(names) + 16
    x = SCREEN[0] + SCREEN[2] / 2 - w / 2
    y = SCREEN[1] + BAR_H + (SCREEN[3] - BAR_H) / 2 - h / 2
    ctx.save()
    ctx.translate(x + w / 2, y + h / 2)
    ctx.scale(0.92 + 0.08 * shown, 0.92 + 0.08 * shown)
    ctx.translate(-x - w / 2, -y - h / 2 + (1 - shown) * 20)
    ctx.push_group()
    shadow(ctx, x, y, w, h, 18, 1.5)
    rounded(ctx, x, y, w, h, 18)
    ctx.set_source_rgb(0.06, 0.07, 0.13)
    ctx.fill_preserve()
    ctx.set_source(gradient(x, y, x + w, y + h, (0, VIOLET), (1, CYAN)))
    ctx.set_line_width(2)
    ctx.stroke()
    font(ctx, MONO, 18, bold=True)
    text(ctx, x + 26, y + 44, PROMPT, VIOLET)
    font(ctx, UI, 18)
    text(ctx, x + 52, y + 44, "layout", DIM, 0.8)
    if ((t - scene.menu_opened) * 2) % 1 < 0.6:
        ctx.rectangle(x + 112, y + 26, 2, 24)
        ctx.set_source_rgb(*CYAN)
        ctx.fill()
    ctx.rectangle(x + 18, y + 64, w - 36, 1)
    ctx.set_source_rgba(1, 1, 1, 0.08)
    ctx.fill()
    selection = scene.selection(t)
    top = y + 74
    sel_y = top + selection * row_h
    rounded(ctx, x + 12, sel_y + 2, w - 24, row_h - 4, 10)
    ctx.set_source(gradient(x, 0, x + w, 0, (0, VIOLET, 0.55), (1, CYAN, 0.35)))
    ctx.fill()
    current = scene.at(scene.layouts, t)[1]
    for index, name in enumerate(names):
        ry = top + index * row_h
        appear = out_cubic(clamp((t - scene.menu_opened - index * 0.018) / 0.3))
        ctx.save()
        ctx.translate((1 - appear) * -24, 0)
        selected = abs(selection - index) < 0.5
        mini(ctx, name, x + 26, ry + 8, 46, 28, selected, t, selected)
        font(ctx, UI, 18, bold=True)
        width = text(ctx, x + 90, ry + 28, name, INK, appear)
        font(ctx, UI, 16)
        text(ctx, x + 90 + width, ry + 28, " — " + swaytiles.DESCRIPTIONS[name], INK if selected else DIM, appear * 0.85)
        if name == current:
            text(ctx, x + w - 34, ry + 28, "●", CYAN, appear, "center")
        ctx.restore()
    ctx.pop_group_to_source()
    ctx.paint_with_alpha(clamp(shown * 1.4))
    ctx.restore()


def logo(ctx, cx, cy, size, t, start):
    """The mark: the master layout's three tiles, popping in on the beat."""
    tiles = [(0, 0, 0.56, 1), (0.6, 0, 0.4, 0.48), (0.6, 0.52, 0.4, 0.48)]
    colors = [(VIOLET, (0.36, 0.45, 1.0)), (CYAN, (0.2, 0.6, 1.0)), (PINK, VIOLET)]
    x0, y0 = cx - size / 2, cy - size / 2
    for index, ((tx, ty, tw, th), (a, b)) in enumerate(zip(tiles, colors, strict=True)):
        p = out_back(clamp((t - start - index * BEAT / 2) / 0.4), 2.0)
        if p <= 0:
            continue
        x, y, w, h = x0 + tx * size, y0 + ty * size, tw * size, th * size
        ctx.save()
        ctx.translate(x + w / 2, y + h / 2)
        ctx.scale(p, p)
        rounded(ctx, -w / 2, -h / 2, w, h, size * 0.08)
        ctx.set_source(gradient(-w / 2, -h / 2, w / 2, h / 2, (0, a), (1, b)))
        ctx.fill()
        ctx.restore()


def brand(ctx, t, amount, start, url):
    if amount <= 0.01:
        return
    ctx.save()
    ctx.push_group()
    logo(ctx, W / 2 - 300, H / 2 - 40, 150, t, start)
    font(ctx, DISPLAY, 120, bold=True)
    reveal = out_expo(clamp((t - start - 0.35) / 0.6))
    ctx.save()
    ctx.rectangle(W / 2 - 200, H / 2 - 160, 700 * reveal, 200)
    ctx.clip()
    ctx.move_to(W / 2 - 195 - (1 - reveal) * 60, H / 2 + 2)
    ctx.set_source(gradient(W / 2 - 200, 0, W / 2 + 450, 0, (0, INK), (1, (0.75, 0.85, 1.0))))
    ctx.show_text("swaytiles")
    ctx.restore()
    tag = clamp((t - start - 0.8) / 0.4)
    font(ctx, UI, 34)
    text(ctx, W / 2, H / 2 + 110 + (1 - out_cubic(tag)) * 20, "Automatic layouts for sway, per workspace", DIM, tag, "center")
    if url:
        cta = out_back(clamp((t - start - 1.3) / 0.45))
        font(ctx, MONO, 30, bold=True)
        label = "github.com/sertacartun/swaytiles"
        width = ctx.text_extents(label).x_advance + 70
        ctx.save()
        ctx.translate(W / 2, H / 2 + 210)
        ctx.scale(0.6 + 0.4 * cta, 0.6 + 0.4 * cta)
        breathe = 0.5 + 0.5 * math.sin((t - start) * math.pi)
        glow(ctx, -width / 2, -36, width, 72, 36, CYAN, clamp(cta) * (0.6 + 0.4 * breathe))
        rounded(ctx, -width / 2, -36, width, 72, 36)
        ctx.set_source(gradient(-width / 2, 0, width / 2, 0, (0, VIOLET, clamp(cta)), (1, CYAN, clamp(cta))))
        ctx.fill()
        text(ctx, 0, 11, label, (1, 1, 1), clamp(cta), "center")
        ctx.restore()
        foot = clamp((t - start - 1.9) / 0.5)
        font(ctx, UI, 24)
        text(ctx, W / 2, H / 2 + 300, "Free and open source  ·  MIT  ·  one Python file", DIM, foot, "center")
    ctx.pop_group_to_source()
    ctx.paint_with_alpha(amount)
    ctx.restore()


def frame(scene, t, surface):
    ctx = cairo.Context(surface)
    background(ctx, t)
    zoom, cx, cy = scene.camera(t)
    shown = scene.screen(t)
    ctx.save()
    ctx.translate(W / 2, H / 2)
    ctx.scale(zoom, zoom)
    ctx.translate(-cx, -cy)
    if shown > 0.01:
        ctx.save()
        push = 0.86 + 0.14 * shown
        ctx.translate(W / 2, H / 2 + (1 - shown) * 60)
        ctx.scale(push, push)
        ctx.translate(-W / 2, -H / 2)
        x, y, w, h = SCREEN
        ctx.push_group()
        shadow(ctx, x, y, w, h, 18, 2.5)
        rounded(ctx, x - 10, y - 10, w + 20, h + 20, 22)
        ctx.set_source(gradient(x, y, x + w, y + h, (0, (0.16, 0.17, 0.28)), (1, (0.08, 0.09, 0.16))))
        ctx.fill()
        ctx.save()
        rounded(ctx, x, y, w, h, 12)
        ctx.clip()
        wallpaper(ctx, x, y, w, h, t)
        since, z = scene.at(scene.z, t)
        prior = [entry for entry in scene.z if entry[0] < since]
        order = z if t - since > 0.3 or not prior else prior[-1][1]
        order = [id for id in order if id in scene.windows] + [id for id in scene.windows if id not in order]
        for id in order:
            scene.windows[id].draw(ctx, t, scene)
        title_bars(ctx, scene, t)
        snap = window_of(t, DROP, 0.35)
        if 0 < snap < 1:
            ctx.rectangle(x, y, w, h)
            ctx.set_source_rgba(1, 1, 1, 0.22 * (1 - snap))
            ctx.fill()
        swaybar(ctx, scene, t)
        menu(ctx, scene, t)
        cursor(ctx, scene, t)
        ctx.restore()
        ctx.pop_group_to_source()
        ctx.paint_with_alpha(clamp(shown * 1.6))
        ctx.restore()
    chip(ctx, scene, t)
    ctx.restore()
    keycaps(ctx, scene, t)
    brand(ctx, t, scene.brand(t), 6.0, False)
    brand(ctx, t, scene.outro(t), 20.0, True)
    caption(ctx, scene, t)
    end = clamp((t - (LENGTH - 0.6)) / 0.6)
    if end > 0:
        ctx.set_source_rgba(0, 0, 0, end)
        ctx.paint()


# ---------------------------------------------------------------- audio

RATE = 48000


def note(name):
    names = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5, "F#": 6, "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}
    pitch, octave = name[:-1], int(name[-1])
    return 440.0 * 2 ** ((names[pitch] + 12 * (octave + 1) - 69) / 12)


def lowpass(signal, cutoff):
    cutoff = np.broadcast_to(np.asarray(cutoff, dtype=float), signal.shape)
    alpha = 1 - np.exp(-2 * np.pi * cutoff / RATE)
    out = np.empty_like(signal)
    state = 0.0
    for i in range(len(signal)):
        state += alpha[i] * (signal[i] - state)
        out[i] = state
    return out


def onepole(signal, cutoff):
    from scipy.signal import lfilter
    a = np.exp(-2 * np.pi * cutoff / RATE)
    return lfilter([1 - a], [1, -a], signal)


def highpass(signal, cutoff):
    return signal - onepole(signal, cutoff)


def saw(freq, length, detune=0.0, phase=0.0):
    t = np.arange(int(length * RATE)) / RATE
    return 2 * ((t * freq * (1 + detune) + phase) % 1) - 1


def envelope(count, attack, release, sustain=1.0):
    t = np.arange(count) / RATE
    env = np.minimum(1, t / max(attack, 1e-4)) * sustain
    tail = np.clip((count / RATE - t) / max(release, 1e-4), 0, 1)
    return env * tail


def place_into(track, start, sound, gain=1.0):
    i = int(start * RATE)
    if i >= len(track):
        return
    end = min(len(track), i + len(sound))
    track[i:end] += sound[:end - i] * gain


def reverb(signal, seconds=2.4, mix_level=0.3, seed=1):
    rng = np.random.default_rng(seed)
    n = int(seconds * RATE)
    ir = rng.standard_normal(n) * np.exp(-np.arange(n) / RATE * 4.5 / seconds)
    ir = onepole(ir, 5000)
    ir /= np.sqrt(np.sum(ir ** 2))
    from scipy.signal import fftconvolve
    wet = fftconvolve(signal, ir)[:len(signal)]
    return signal * (1 - mix_level) + wet * mix_level


def soundtrack(scene):
    total = int((LENGTH + 0.5) * RATE)
    rng = np.random.default_rng(7)
    pad = np.zeros(total)
    bass = np.zeros(total)
    drums = np.zeros(total)
    arp = np.zeros(total)
    fx = np.zeros(total)
    chords = [("A2", ["A3", "C4", "E4", "B4"]), ("F2", ["F3", "A3", "C4", "E4"]),
              ("C3", ["G3", "C4", "E4", "G4"]), ("G2", ["G3", "B3", "D4", "F#4"])]
    bars = int(LENGTH / BAR)
    for bar in range(bars):
        root, voicing = chords[bar % 4] if bar < 11 else chords[0]
        start = bar * BAR
        length = BAR if bar < bars - 1 else BAR + 0.5
        n = int(length * RATE)
        chord = sum(saw(note(v), length, detune) for v in voicing for detune in (-0.004, 0.0, 0.005))
        cutoff = 900 + 1400 * min(1, bar / 3) if bar != 0 else np.linspace(300, 1400, n)
        if bar >= 10:
            cutoff = np.linspace(2400, 700, n)
        tone = onepole(chord, cutoff if np.isscalar(cutoff) else 1200) if np.isscalar(cutoff) else lowpass(chord, cutoff)
        place_into(pad, start, tone * envelope(n, 0.05, 0.12), 0.05)
        if 1 <= bar < 10:
            for eighth in range(8):
                when = start + eighth * BEAT / 2
                octave = 2 if eighth % 2 else 1
                freq = note(root) * octave / 1
                b = saw(freq, BEAT / 2 * 0.9) + 0.5 * saw(freq, BEAT / 2 * 0.9, 0.006)
                b = onepole(b, 380 + 260 * (eighth % 2))
                place_into(bass, when, b * envelope(len(b), 0.004, 0.06), 0.28)
        if 2 <= bar < 10:
            for beat in range(4):
                when = start + beat * BEAT
                k_len = int(0.32 * RATE)
                kt = np.arange(k_len) / RATE
                freq = 48 + 110 * np.exp(-kt * 28)
                kick = np.sin(2 * np.pi * np.cumsum(freq) / RATE) * np.exp(-kt * 9)
                place_into(drums, when, kick, 0.9)
                if beat % 2 == 1:
                    s_len = int(0.22 * RATE)
                    noise = highpass(rng.standard_normal(s_len), 1200) * np.exp(-np.arange(s_len) / RATE * 18)
                    body = np.sin(2 * np.pi * 190 * np.arange(s_len) / RATE) * np.exp(-np.arange(s_len) / RATE * 30)
                    place_into(drums, when, noise * 0.5 + body * 0.4, 0.55)
                for sixteenth in range(4 if 7 <= bar < 9 else 2):
                    hat_when = when + (sixteenth + (0 if 7 <= bar < 9 else 0.5)) * BEAT / (4 if 7 <= bar < 9 else 2)
                    h_len = int(0.05 * RATE)
                    hat = highpass(rng.standard_normal(h_len), 7000) * np.exp(-np.arange(h_len) / RATE * 70)
                    place_into(drums, hat_when, hat, 0.16 if sixteenth % 2 else 0.22)
        if 4 <= bar < 12:
            tones = [note(v) * 2 for v in voicing]
            pattern = [0, 1, 2, 3, 2, 1, 2, 3, 0, 2, 1, 3, 2, 1, 3, 2]
            for step, which in enumerate(pattern):
                when = start + step * BEAT / 4
                length_p = 0.16
                tt = np.arange(int(length_p * RATE)) / RATE
                freq = tones[which]
                pluck = (np.sign(np.sin(2 * np.pi * freq * tt)) * 0.5 + np.sin(2 * np.pi * freq * tt)) * np.exp(-tt * 22)
                place_into(arp, when, onepole(pluck, 3200), 0.07 * (1 if bar < 11 else 1 - (bar - 10) / 2))
    # sidechain: duck the pad and bass under the kick
    duck = np.ones(total)
    for bar in range(2, 10):
        for beat in range(4):
            i = int((bar * BAR + beat * BEAT) * RATE)
            n = int(0.25 * RATE)
            curve = 1 - 0.6 * np.exp(-np.arange(n) / RATE * 14)
            end = min(total, i + n)
            duck[i:end] = np.minimum(duck[i:end], curve[:end - i])
    pad *= duck
    bass *= duck
    # the hook's riser
    n = int(DROP * RATE)
    riser = lowpass(rng.standard_normal(n), np.geomspace(200, 9000, n)) * np.linspace(0, 1, n) ** 2
    place_into(fx, 0.0, riser, 0.22)
    for when, kind, extra in scene.sounds:
        if kind == "impact":
            n = int(1.6 * RATE)
            tt = np.arange(n) / RATE
            boom = np.sin(2 * np.pi * np.cumsum(40 + 80 * np.exp(-tt * 10)) / RATE) * np.exp(-tt * 3.5)
            crash = highpass(rng.standard_normal(n), 3000) * np.exp(-tt * 2.6)
            place_into(fx, when, boom * 0.8 + crash * 0.18, 0.7)
        elif kind == "whoosh":
            n = int(0.7 * RATE)
            sweep = lowpass(rng.standard_normal(n), np.geomspace(6000, 300, n)) * np.sin(np.linspace(0, np.pi, n)) ** 2
            place_into(fx, when - 0.1, sweep, 0.35)
        elif kind in ("pop", "tick", "swap", "open"):
            freqs = {"pop": [880, 1320], "tick": [note(n_) for n_ in ("E5", "G5", "A5", "B5", "C6", "D6", "E6", "A6")][extra or 0:(extra or 0) + 1],
                     "swap": [660, 990, 1320], "open": [520, 780, 1040]}[kind]
            if kind == "tick":
                freqs = [[note(n_) for n_ in ("E5", "G5", "A5", "B5", "C6", "D6", "E6", "A6")][extra]]
            for k, freq in enumerate(freqs):
                n = int(0.35 * RATE)
                tt = np.arange(n) / RATE
                blip = np.sin(2 * np.pi * freq * tt * (1 + 0.02 * np.exp(-tt * 30))) * np.exp(-tt * 14)
                place_into(fx, when + k * 0.05, blip, 0.16)
        elif kind == "close":
            n = int(0.3 * RATE)
            tt = np.arange(n) / RATE
            down = np.sin(2 * np.pi * np.cumsum(900 * np.exp(-tt * 8)) / RATE) * np.exp(-tt * 12)
            place_into(fx, when, down, 0.18)
        elif kind in ("key", "click"):
            n = int(0.04 * RATE)
            tick = highpass(rng.standard_normal(n), 2500) * np.exp(-np.arange(n) / RATE * 120)
            place_into(fx, when, tick, 0.35)
    # the logo's three notes and the last chord's bell
    for start in (6.0, 20.0):
        for k, name in enumerate(("A5", "C6", "E6")):
            n = int(1.4 * RATE)
            tt = np.arange(n) / RATE
            bell = (np.sin(2 * np.pi * note(name) * tt) + 0.3 * np.sin(2 * np.pi * note(name) * 2.01 * tt)) * np.exp(-tt * 3)
            place_into(fx, start + k * BEAT / 2, bell, 0.13)
    music = pad + bass + drums + arp
    left = music + reverb(arp, 1.2, 0.5, 2) * 0.6 + fx
    right = music + reverb(arp, 1.3, 0.5, 3) * 0.6 + fx
    stereo = np.stack([reverb(left, 2.4, 0.18, 4), reverb(right, 2.4, 0.18, 5)], axis=1)
    fade = np.ones(total)
    tail = int(1.2 * RATE)
    fade[total - tail:] = np.linspace(1, 0, tail)
    stereo *= fade[:, None]
    stereo = np.tanh(stereo * 1.4) / np.tanh(1.4)
    stereo /= np.max(np.abs(stereo)) * 1.05
    return stereo[:int(LENGTH * RATE)]


def write_wav(path, stereo):
    data = (stereo * 32767).astype("<i2")
    with wave.open(str(path), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(data.tobytes())


# ---------------------------------------------------------------- output

def render_segment(args):
    first, last, path = args
    scene = build()
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    encoder = subprocess.Popen(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{W}x{H}", "-r", str(FPS),
         "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p", str(path)],
        stdin=subprocess.PIPE)
    for number in range(first, last):
        frame(scene, number / FPS, surface)
        surface.flush()
        encoder.stdin.write(bytes(surface.get_data()))
    encoder.stdin.close()
    encoder.wait()
    return path


# ---------------------------------------------------------------- the README's pictures

LAYOUT_ORDER = ["master", "master-right", "wide", "centered", "tabbed-master", "stacked-master",
                "dwindle", "spiral", "grid", "float", "tabbed", "stacking"]
NAVY = (0.11, 0.12, 0.23)


def wordmark(ctx, x, baseline, size, color):
    font(ctx, DISPLAY, size, bold=True)
    ctx.move_to(x, baseline)
    ctx.set_source(color)
    ctx.show_text("swaytiles")


def logo_svg(path, dark):
    """The mark and the name, with the name in the colour that reads on
    GitHub's dark or light page."""
    probe = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1))
    font(probe, DISPLAY, 120, bold=True)
    extents = probe.text_extents("swaytiles")
    mark, gap, pad = 140, 30, 14
    width = pad + mark + gap + extents.x_advance + pad
    height = 170
    surface = cairo.SVGSurface(str(path), width, height)
    ctx = cairo.Context(surface)
    logo(ctx, pad + mark / 2, height / 2, mark, 99, 0)
    left = pad + mark + gap
    color = gradient(left, 0, left + extents.x_advance, 0, (0, INK), (1, (0.75, 0.85, 1.0))) if dark else \
        gradient(left, 0, left + extents.x_advance, 0, (0, NAVY), (1, (0.22, 0.24, 0.45)))
    wordmark(ctx, left - extents.x_bearing, height / 2 - extents.y_bearing - extents.height / 2, 120, color)
    surface.finish()


def layouts_svg(path):
    """The twelve layouts in two rows, named, in a grey that reads on both themes."""
    columns, cell_w, cell_h = 6, 150, 132
    surface = cairo.SVGSurface(str(path), columns * cell_w, 2 * cell_h)
    ctx = cairo.Context(surface)
    for index, name in enumerate(LAYOUT_ORDER):
        x = (index % columns) * cell_w
        y = (index // columns) * cell_h
        mini(ctx, name, x + 15, y + 8, 120, 75, True, 0, False)
        font(ctx, MONO, 16, bold=True)
        text(ctx, x + cell_w / 2, y + 112, name, (0.53, 0.57, 0.68), 1, "center")
    surface.finish()


def social_png(path):
    """The card shown where the repository's link is shared: 1280 x 640."""
    w, h = 1280, 640
    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
    ctx = cairo.Context(surface)
    ctx.save()
    ctx.scale(w / W, h / H * 1.0)
    background(ctx, 3.0)
    ctx.restore()
    font(ctx, DISPLAY, 110, bold=True)
    extents = ctx.text_extents("swaytiles")
    mark, gap = 128, 30
    left = (w - (mark + gap + extents.x_advance)) / 2
    logo(ctx, left + mark / 2, 230, mark, 99, 0)
    x = left + mark + gap
    wordmark(ctx, x - extents.x_bearing, 230 - extents.y_bearing - extents.height / 2, 110,
             gradient(x, 0, x + extents.x_advance, 0, (0, INK), (1, (0.75, 0.85, 1.0))))
    font(ctx, UI, 34)
    text(ctx, w / 2, 368, "Tiling layouts for sway, one per workspace", DIM, 1, "center")
    names = LAYOUT_ORDER
    size_w, size_h, spacing = 72, 45, 18
    row = len(names) * size_w + (len(names) - 1) * spacing
    for index, name in enumerate(names):
        mini(ctx, name, (w - row) / 2 + index * (size_w + spacing), 440, size_w, size_h, True, 0, False)
    font(ctx, MONO, 22, bold=True)
    text(ctx, w / 2, 560, "github.com/sertacartun/swaytiles", (0.75, 0.85, 1.0), 0.9, "center")
    surface.write_to_png(str(path))


HERO = [(0.0, 6.0), (10.0, 12.9), (14.0, 17.95)]
HERO_SIZE, HERO_FPS, HERO_CROSS = (1600, 900), 25, 0.2


def hero_frames(args):
    jobs, folder = args
    scene = build(brand=False)
    full = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    before = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    small = cairo.ImageSurface(cairo.FORMAT_ARGB32, *HERO_SIZE)
    for number, t, previous, amount in jobs:
        frame(scene, t, full)
        ctx = cairo.Context(small)
        ctx.scale(HERO_SIZE[0] / W, HERO_SIZE[1] / H)
        if previous is not None:
            frame(scene, previous, before)
            ctx.set_source_surface(before)
            ctx.get_source().set_filter(cairo.FILTER_GOOD)
            ctx.paint()
        ctx.set_source_surface(full)
        ctx.get_source().set_filter(cairo.FILTER_GOOD)
        ctx.paint_with_alpha(amount)
        small.write_to_png(str(folder / f"{number:05}.png"))


def hero_webp(path):
    """A silent loop of the promo without the logo, for the top of the
    README: the hook, the menu and the montage, each cut crossfaded."""
    import tempfile
    times = []
    for index, (start, end) in enumerate(HERO):
        for i in range(int((end - start) * HERO_FPS)):
            age = i / HERO_FPS
            fading = index > 0 and age < HERO_CROSS
            previous = HERO[index - 1][1] + age if fading else None
            times.append((start + age, previous, in_out_cubic(age / HERO_CROSS) if fading else 1.0))
    jobs = [(number, *entry) for number, entry in enumerate(times)]
    workers = os.cpu_count() or 4
    with tempfile.TemporaryDirectory() as folder:
        folder = Path(folder)
        with Pool(workers) as pool:
            pool.map(hero_frames, [(jobs[i::workers], folder) for i in range(workers)])
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-framerate", str(HERO_FPS), "-i", str(folder / "%05d.png"),
                        "-c:v", "libwebp_anim", "-lossless", "0", "-q:v", "78", "-compression_level", "6",
                        "-loop", "0", str(path)], check=True)


def video(path):
    import tempfile
    with tempfile.TemporaryDirectory() as folder:
        folder = Path(folder)
        write_wav(folder / "soundtrack.wav", soundtrack(build()))
        frames = int(LENGTH * FPS)
        workers = os.cpu_count() or 4
        chunk = math.ceil(frames / workers)
        jobs = [(i * chunk, min(frames, (i + 1) * chunk), folder / f"part{i:02}.mp4") for i in range(workers) if i * chunk < frames]
        with Pool(len(jobs)) as pool:
            parts = pool.map(render_segment, jobs)
        listing = folder / "parts.txt"
        listing.write_text("".join(f"file '{part}'\n" for part in parts))
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
                        "-i", str(folder / "soundtrack.wav"), "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                        "-movflags", "+faststart", "-shortest", str(path)], check=True)


def main():
    command, arguments = (sys.argv[1], sys.argv[2:]) if len(sys.argv) > 1 else ("assets", [])
    if command == "assets":
        logo_svg(HERE / "logo.svg", dark=True)
        logo_svg(HERE / "logo-light.svg", dark=False)
        layouts_svg(HERE / "layouts.svg")
        social_png(HERE / "social.png")
        hero_webp(HERE / "hero.webp")
    elif command == "video":
        video(Path(arguments[0] if arguments else "swaytiles-promo.mp4"))
    elif command == "still":
        scene = build()
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
        for value in arguments:
            frame(scene, float(value), surface)
            surface.write_to_png(f"promo-{value}.png")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()

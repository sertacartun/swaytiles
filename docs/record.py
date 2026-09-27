"""Record the README animations in a disposable headless sway.

    uv run python docs/record.py            # every animation
    uv run python docs/record.py master     # only some

Needs sway, grim, wtype, ImageMagick, GTK 4 for Python and the Fira Sans font.
"""

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent / "tests")]

import harness  # noqa: E402

import sway_layout  # noqa: E402

OUT = HERE / "gifs"
WIDTH, HEIGHT, SCALE = 1280, 720, 0.75
CONFIG = """\
output HEADLESS-1 resolution 1280x{height} bg #2E3440 solid_color
font pango:Fira Sans SemiBold 11
default_border pixel 3
default_floating_border normal 3
titlebar_padding 10 6
gaps inner 8
gaps outer 6
client.focused #88C0D0 #88C0D0 #2E3440 #88C0D0 #88C0D0
client.focused_inactive #4C566A #4C566A #D8DEE9 #4C566A #4C566A
client.unfocused #3B4252 #3B4252 #D8DEE9 #3B4252 #3B4252
bindsym Mod4+m nop layout master
bindsym Mod4+Shift+Left nop layout move left
bindsym Mod4+Shift+Right nop layout move right
bindsym Mod4+Shift+Up nop layout move up
bindsym Mod4+Shift+Down nop layout move down
"""


class Recorder:
    def __init__(self, layout, height=HEIGHT):
        self.base = Path(tempfile.mkdtemp(prefix="swl-rec-"))
        self.session = harness.Session(self.base, layout, config=CONFIG.replace("{height}", str(height))).start()
        self.frames = []

    def open(self, number):
        environment = {**self.session.env, "XDG_CONFIG_HOME": str(self.base / "config"), "GTK_THEME": "Adwaita:dark"}
        client = subprocess.Popen([harness.CLIENT_PYTHON, str(HERE / "window.py"), str(number)], env=environment,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        self.session.clients.append(client)
        if not self.session.wait(lambda: self.session.node(f"Window {number}") is not None, 8):
            raise RuntimeError(f"window {number} did not map")
        self.session.settle(quiet=0.5)

    def command(self, text):
        self.session.command(text, settle=False)
        self.session.settle(quiet=0.5)

    def focus(self, number):
        self.command(f'[title="^Window {number}$"] focus')

    def close(self, number):
        self.command(f'[title="^Window {number}$"] kill')

    def key(self, *keys):
        modifiers = keys[:-1]
        arguments = [part for modifier in modifiers for part in ("-M", modifier)]
        arguments += ["-k", keys[-1]] + [part for modifier in reversed(modifiers) for part in ("-m", modifier)]
        subprocess.run(["wtype", *arguments], env=self.session.env, check=True)
        self.session.settle(quiet=0.5)

    def largest(self):
        nodes = [(number, self.session.node(f"Window {number}")) for number in range(1, 7)]
        return max((node["rect"]["width"] * node["rect"]["height"], -number, number) for number, node in nodes if node)[2]

    def choose(self, layout):
        self.session.choose(layout)
        self.session.settle(quiet=0.5)

    def shoot(self, title, caption, hold=1.1):
        raw = self.base / f"raw{len(self.frames):02}.png"
        framed = self.base / f"frame{len(self.frames):02}.png"
        subprocess.run(["grim", "-o", "HEADLESS-1", str(raw)], env=self.session.env, check=True)
        width = int(WIDTH * SCALE)
        subprocess.run(["magick", str(raw), "-resize", f"{width}x", "-background", "#2E3440", "-gravity", "south",
                        "-splice", "0x52", "-font", "Fira-Sans-Bold", "-pointsize", "19", "-fill", "#ECEFF4",
                        "-gravity", "southwest", "-annotate", "+20+15", title,
                        "-font", "Fira-Sans-Regular", "-pointsize", "17", "-fill", "#D8DEE9",
                        "-gravity", "southeast", "-annotate", "+20+16", caption, str(framed)], check=True)
        self.frames.append((framed, hold))

    def save(self, name):
        OUT.mkdir(exist_ok=True)
        arguments = []
        for path, hold in self.frames:
            arguments += ["-delay", str(round(hold * 100)), str(path)]
        target = OUT / f"{name}.gif"
        subprocess.run(["magick", *arguments, "+dither", "-loop", "0", "-layers", "OptimizePlus", str(target)], check=True)
        print(f"{target.relative_to(HERE.parent)}  {target.stat().st_size // 1024} KiB")

    def stop(self):
        self.session.stop()
        shutil.rmtree(self.base, ignore_errors=True)


def layout_demo(layout):
    recorder = Recorder(layout)
    try:
        title = f"{layout}  ·  {sway_layout.DESCRIPTIONS[layout]}"
        count = 4 if layout in ("tabbed", "stacking") else 5
        for number in range(1, count + 1):
            recorder.open(number)
            recorder.shoot(title, f"open window {number}", 0.8 if number < count else 1.4)
        if layout in ("tabbed", "stacking"):
            arrow, direction = ("←", "Left") if layout == "tabbed" else ("↑", "Up")
            for _ in range(2):
                recorder.key("logo", "shift", direction)
                recorder.shoot(title, f"Super+Shift+{arrow}  ·  move window 4 {direction.lower()}", 1.2)
            recorder.close(4)
            recorder.shoot(title, "close window 4", 2.0)
        elif layout == "float":
            recorder.close(2)
            recorder.shoot(title, "close window 2", 1.2)
            recorder.open(6)
            recorder.shoot(title, "open window 6", 2.0)
        else:
            recorder.focus(4)
            recorder.key("logo", "m")
            recorder.shoot(title, "Super+M  ·  swap window 4 with the master", 1.6)
            recorder.key("logo", "shift", "Right")
            recorder.shoot(title, "Super+Shift+→  ·  move it right", 1.4)
            master = recorder.largest()
            recorder.close(master)
            recorder.shoot(title, f"close window {master}, the master", 2.0)
        recorder.save(layout)
    finally:
        recorder.stop()


def tour():
    recorder = Recorder("master")
    try:
        for number in range(1, 6):
            recorder.open(number)
        recorder.focus(1)
        for layout in ("master", "master-right", "wide", "centered", "tabbed-master", "stacked-master",
                       "dwindle", "spiral", "grid", "tabbed", "stacking", "float"):
            recorder.choose(layout)
            recorder.shoot(layout, sway_layout.DESCRIPTIONS[layout], 1.3)
        recorder.save("tour")
    finally:
        recorder.stop()


def menu():
    recorder = Recorder("master", height=820)
    try:
        for number in range(1, 4):
            recorder.open(number)
        config = recorder.base / "config"
        (config / "fuzzel").mkdir(parents=True)
        (config / "fuzzel" / "fuzzel.ini").write_text(
            "[main]\ndpi-aware=no\nfont=Fira Sans:size=11\nicon-theme=hicolor\nlines=13\nwidth=52\nhorizontal-pad=20\nvertical-pad=12\n"
            "inner-pad=8\nline-height=24\nimage-size-ratio=0.5\n"
            "[colors]\nbackground=2E3440f2\ntext=D8DEE9ff\nmatch=88C0D0ff\nselection=434C5Eff\nselection-text=ECEFF4ff\n"
            "selection-match=88C0D0ff\nborder=88C0D0ff\n[border]\nwidth=2\nradius=10\n")
        environment = {**recorder.session.env, "XDG_CONFIG_HOME": str(config)}
        process = subprocess.Popen([sys.executable, str(harness.DAEMON), "menu"], env=environment, start_new_session=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2.5)
        raw = recorder.base / "menu.png"
        subprocess.run(["grim", "-o", "HEADLESS-1", str(raw)], env=recorder.session.env, check=True)
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(5)
        OUT.mkdir(exist_ok=True)
        subprocess.run(["magick", str(raw), "-resize", f"{int(WIDTH * SCALE)}x", str(OUT / "menu.png")], check=True)
        print("docs/gifs/menu.png")
    finally:
        recorder.stop()


if __name__ == "__main__":
    wanted = sys.argv[1:] or ["tour", "menu", *(name for name in sway_layout.LAYOUTS if name != "sway")]
    for name in wanted:
        tour() if name == "tour" else menu() if name == "menu" else layout_demo(name)

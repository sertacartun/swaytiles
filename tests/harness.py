"""A disposable headless sway with the daemon and GTK test windows.

Nothing touches the user's session: sway, the daemon and the windows get their
own runtime, state and cache directories.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DAEMON = ROOT / "swaytiles.py"
CLIENT = Path(__file__).resolve().parent / "client.py"
COUNTED = Path(__file__).resolve().parent / "daemon.py"
CLIENT_PYTHON = os.environ.get("SWAY_LAYOUT_TEST_PYTHON", "/usr/bin/python3")
LETTERS = {"splith": "H", "splitv": "V", "tabbed": "T", "stacked": "S"}
QUIET = ("mark ", "unmark ", "set ")


def available():
    if not all(shutil.which(tool) for tool in ("sway", "swaymsg", "wtype")):
        return False
    probe = "import gi; gi.require_version('Gtk', '4.0'); from gi.repository import Gtk"
    return subprocess.run([CLIENT_PYTHON, "-c", probe], capture_output=True).returncode == 0


def raw(node):
    if not node["nodes"]:
        return node["name"] + ("*" if node.get("fullscreen_mode") else "")
    return LETTERS[node["layout"]] + "[" + " ".join(raw(child) for child in node["nodes"]) + "]"


def tidy(node):
    if not node["nodes"]:
        return raw(node)
    children = [tidy(child) for child in node["nodes"]]
    if len(children) == 1 and node["layout"] in ("splith", "splitv"):
        return children[0]
    return LETTERS[node["layout"]] + "[" + " ".join(children) + "]"


def expected(layout, count, names=None):
    import swaytiles
    names = names or [f"w{index + 1}" for index in range(count)]
    tree = swaytiles.LAYOUTS[layout](list(range(count)))

    def render(node):
        if isinstance(node, int):
            return names[node]
        children = [render(child) for child in node[1]]
        if len(children) == 1 and node[0] in ("splith", "splitv"):
            return children[0]
        return LETTERS[node[0]] + "[" + " ".join(children) + "]"
    return render(swaytiles.normalize(tree))


class Session:
    def __init__(self, base, layout="master", workspaces=None, config="", outputs=1):
        self.base = Path(tempfile.mkdtemp(prefix="swl-", dir="/tmp"))
        self.state = Path(base) / "state"
        self.cache = Path(base) / "cache"
        self.state.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps({"layout": layout, "workspaces": workspaces or {}}))
        self.errors = Path(base) / "daemon.err"
        self.sent = Path(base) / "daemon.sent"
        self.config = Path(base) / "sway.conf"
        self.config.write_text("default_border normal\nfocus_follows_mouse no\n" + config)
        self.outputs = outputs
        self.releasing = False
        self.layout, self.picked = layout, dict(workspaces or {})
        self.clients = []
        self.daemon = None
        self.sway = None
        self.env = None

    @property
    def state_file(self):
        return self.state / "swaytiles.json"

    def start(self):
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", ""),
            "XDG_RUNTIME_DIR": str(self.base),
            "WLR_BACKENDS": "headless",
            "WLR_LIBINPUT_NO_DEVICES": "1",
            "WLR_RENDERER": "pixman",
            "WLR_HEADLESS_OUTPUTS": str(self.outputs),
        }
        self.sway = subprocess.Popen(["sway", "-c", str(self.config)], env=environment, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            sockets = list(self.base.glob("sway-ipc.*.sock"))
            if sockets and (self.base / "wayland-1").exists():
                socket = str(sockets[0])
                if subprocess.run(["swaymsg", "-s", socket, "-t", "get_version"], capture_output=True).returncode == 0:
                    break
            time.sleep(0.05)
        else:
            raise RuntimeError("headless sway did not start")
        self.env = {**environment, "SWAYSOCK": socket, "I3SOCK": socket, "WAYLAND_DISPLAY": "wayland-1",
                    "XDG_STATE_HOME": str(self.state), "XDG_CACHE_HOME": str(self.cache),
                    "GSK_RENDERER": "cairo", "GDK_BACKEND": "wayland", "GTK_A11Y": "none", "NO_AT_BRIDGE": "1"}
        self.start_daemon()
        return self

    def start_daemon(self):
        with open(self.errors, "a") as errors:
            self.daemon = subprocess.Popen([sys.executable, str(COUNTED)], env={**self.env, "SWAYTILES_SENT": str(self.sent)},
                                           stdout=subprocess.DEVNULL,
                                           stderr=errors, start_new_session=True)
        self.wait(self.locked, 5)
        time.sleep(0.2)
        self.settle()

    def locked(self):
        for path in self.base.glob("swaytiles.*.lock"):
            with open(path) as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return True
                fcntl.flock(lock, fcntl.LOCK_UN)
        return False

    def stop_daemon(self, number=signal.SIGTERM):
        if self.daemon and self.daemon.poll() is None:
            os.killpg(self.daemon.pid, number)
            self.daemon.wait(5)

    def stop(self):
        try:
            self.stop_daemon()
        except (ProcessLookupError, subprocess.TimeoutExpired):
            os.killpg(self.daemon.pid, signal.SIGKILL)
        for client in self.clients:
            if client.poll() is None:
                client.kill()
        if self.sway and self.sway.poll() is None:
            os.killpg(self.sway.pid, signal.SIGTERM)
            try:
                self.sway.wait(5)
            except subprocess.TimeoutExpired:
                os.killpg(self.sway.pid, signal.SIGKILL)
        shutil.rmtree(self.base, ignore_errors=True)

    @property
    def alive(self):
        return self.daemon.poll() is None

    def drawn(self, action):
        """The command messages the daemon sent while `action` ran that can
        change what sway draws: anything but marks and variables."""
        before = len(self.sent.read_text().splitlines()) if self.sent.exists() else 0
        action()
        self.settle()
        sent = self.sent.read_text().splitlines()[before:] if self.sent.exists() else []
        return [message for message in sent
                if any(not part.split("] ", 1)[-1].startswith(QUIET) for part in re.split(r"[;,] ", message))]

    def stderr(self):
        return self.errors.read_text() if self.errors.exists() else ""

    def msg(self, *arguments):
        return subprocess.run(["swaymsg", "-s", self.env["SWAYSOCK"], *arguments], capture_output=True, text=True).stdout

    def command(self, text, settle=True):
        result = json.loads(self.msg("-r", text) or "[]")
        if settle:
            self.settle()
        return result

    def tree(self):
        return json.loads(self.msg("-r", "-t", "get_tree"))

    def workspace(self, name="1"):
        return next((ws for output in self.tree()["nodes"] for ws in output["nodes"] if ws["name"] == name), None)

    def node(self, title):
        stack = [self.tree()]
        while stack:
            node = stack.pop()
            if node.get("name") == title and node["type"] in ("con", "floating_con"):
                return node
            stack += node["nodes"] + node["floating_nodes"]
        return None

    def shape(self, name="1", exact=False):
        ws = self.workspace(name)
        if ws is None:
            return "-"
        text = (raw(ws) if exact else tidy(ws)) if ws["nodes"] else "-"
        floats = " ".join(node["name"] for node in ws["floating_nodes"])
        return text + (f" F[{floats}]" if floats else "")

    def focused(self):
        stack = [self.tree()]
        while stack:
            node = stack.pop()
            if node.get("focused"):
                return node.get("name")
            stack += node["nodes"] + node["floating_nodes"]
        return None

    def fullscreen(self, title):
        return self.node(title)["fullscreen_mode"] != 0

    def chosen(self, name="1"):
        return json.loads(self.state_file.read_text())["workspaces"].get(name)

    def released(self):
        """The workspaces the daemon let go of by itself: switched to default without being picked."""
        chosen = json.loads(self.state_file.read_text())["workspaces"]
        return {name for name, layout in chosen.items() if layout == "default" and self.picked.get(name, self.layout) != "default"}

    def width(self, title, of="1"):
        node, ws = self.node(title), self.workspace(of)
        return round(node["rect"]["width"] / ws["rect"]["width"], 2)

    def wait(self, condition, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return True
            time.sleep(0.02)
        return False

    def settle(self, quiet=0.3, timeout=6.0):
        deadline = time.monotonic() + timeout
        last, since = None, time.monotonic()
        while time.monotonic() < deadline:
            current = self.msg("-t", "get_tree", "-r")
            if current != last:
                last, since = current, time.monotonic()
            elif time.monotonic() - since >= quiet:
                return
            time.sleep(0.03)

    def open(self, title, settle=True):
        client = subprocess.Popen([CLIENT_PYTHON, str(CLIENT), title], env=self.env, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, start_new_session=True)
        self.clients.append(client)
        if not self.wait(lambda: self.node(title) is not None, 8):
            raise RuntimeError(f"window {title} did not map")
        if settle:
            self.settle()

    def close(self, title, settle=True):
        self.msg(f"[title=^{title}$] kill")
        self.wait(lambda: self.node(title) is None, 5)
        if settle:
            self.settle()

    def title_bar(self, title):
        """The middle of the window's title bar, in layout coordinates: sway
        gives a title bar's place relative to the window's parent."""
        stack = [self.tree()]
        while stack:
            node = stack.pop()
            for child in node["nodes"]:
                if child.get("name") == title and child["type"] == "con":
                    deco = child["deco_rect"]
                    return (node["rect"]["x"] + deco["x"] + deco["width"] // 2,
                            node["rect"]["y"] + deco["y"] + deco["height"] // 2)
                stack.append(child)
        raise LookupError(title)

    def drag(self, title, x, y):
        """Drag the window by its title bar with the mouse and drop it at x, y."""
        from pointer import Pointer
        boxes = [output["rect"] for output in self.tree()["nodes"] if output["name"] != "__i3"]
        size = max(box["x"] + box["width"] for box in boxes), max(box["y"] + box["height"] for box in boxes)
        pointer = Pointer(self.env)
        try:
            pointer.drag(self.title_bar(title), (x, y), size)
        finally:
            pointer.close()
        self.settle()

    def focus(self, title):
        self.command(f"[title=^{title}$] focus")

    def key(self, name):
        subprocess.run(["wtype", "-M", "logo", "-k", name, "-m", "logo"], env=self.env, check=True)
        self.settle()

    def choose(self, layout):
        focused = next(ws["name"] for ws in json.loads(self.msg("-r", "-t", "get_workspaces")) if ws["focused"])
        self.picked[focused] = layout
        subprocess.run([sys.executable, str(DAEMON), layout], env=self.env, check=True)
        self.settle()

    def run(self, *arguments, env=None):
        return subprocess.run([sys.executable, str(DAEMON), *arguments], env={**self.env, **(env or {})},
                              capture_output=True, text=True, timeout=10)

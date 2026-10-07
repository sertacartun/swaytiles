#!/usr/bin/env python3
import contextlib
import fcntl
import glob
import json
import math
import os
import re
import signal
import socket
import struct
import sys
import threading
import time
from pathlib import Path

# shlex, shutil, subprocess, tempfile and traceback are imported where they are
# used: the daemon itself never needs them, and they cost megabytes.

STATE = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "swaytiles.json"
ICONS = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "swaytiles"
MARK = "_layout"
FLOATED = "_layout_floated"
AFTER = "_layout_after_"
SYNC = "layout:sync"
ACT = "layout:act"
RENAMED = {"sway": "default"}
CASCADE = 40
TABBED = ("tabbed", "stacked")
SPLITS = {"right": "splith", "down": "splitv", "left": "splith", "up": "splitv"}
PARALLEL = {"left": ("splith", "tabbed"), "right": ("splith", "tabbed"), "up": ("splitv", "stacked"), "down": ("splitv", "stacked")}
EVENTS = {0: "workspace", 1: "output", 3: "window", 5: "binding", 6: "shutdown", 7: "tick"}
SUBSCRIPTIONS = ["window", "tick", "workspace", "binding", "output", "shutdown"]
FOCUSING = re.compile(r"focus (left|right|up|down)")
MOVING = re.compile(r"move (left|right|up|down)|move (?:container |window )?(?:to )?workspace (number \d+|[^\s\"']+)")
ELSEWHERE = ("next", "prev", "next_on_output", "prev_on_output", "back_and_forth", "current", "number")
RESHAPING = re.compile(r"(?:^|[;,\]])\s*(?:split[hvt]?|layout)\b")


class Sway:
    MAGIC = b"i3-ipc"
    COMMAND, WORKSPACES, TREE, TICK, SUBSCRIBE, CONFIG, MODE = 0, 1, 4, 10, 2, 9, 12

    def __init__(self, path=None, timeout=None):
        self.given = path
        self.timeout = timeout
        self.lock = threading.Lock()
        self.connection = None

    @property
    def path(self):
        if not self.given:
            self.given = os.environ.get("SWAYSOCK") or discover()
        if not self.given:
            raise ConnectionError("no sway socket: SWAYSOCK is unset and no running sway was found")
        return self.given

    def connect(self):
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(self.timeout)
        connection.connect(self.path)
        return connection

    def send(self, connection, kind, payload=""):
        data = payload.encode()
        connection.sendall(self.MAGIC + struct.pack("=II", len(data), kind) + data)

    def receive(self, connection):
        size, kind = struct.unpack("=II", self.read(connection, 14)[len(self.MAGIC):])
        return kind & 0x7FFFFFFF, json.loads(self.read(connection, size))

    @staticmethod
    def read(connection, size):
        data = bytearray()
        while len(data) < size:
            chunk = connection.recv(size - len(data))
            if not chunk:
                raise ConnectionError("sway closed the IPC socket")
            data += chunk
        return bytes(data)

    def request(self, kind, payload=""):
        with self.lock:
            try:
                self.connection = self.connection or self.connect()
                self.send(self.connection, kind, payload)
                return self.receive(self.connection)[1]
            except BaseException:
                if self.connection:
                    self.connection.close()
                self.connection = None
                raise

    def command(self, *commands):
        joined = "; ".join(command for command in commands if command)
        if not joined:
            return []
        results = self.request(self.COMMAND, joined)
        for result in results:
            if result.get("parse_error"):
                print(f"layout: sway rejected {joined!r}: {result.get('error')}", file=sys.stderr, flush=True)
        return results

    def tree(self):
        return self.request(self.TREE)

    def tick(self, payload):
        return self.request(self.TICK, payload)

    def focused_workspace(self):
        return next(ws["name"] for ws in self.request(self.WORKSPACES) if ws["focused"])

    def subscribe(self, names):
        connection = self.connect()
        self.send(connection, self.SUBSCRIBE, json.dumps(names))
        self.receive(connection)

        def stream():
            with connection:
                while True:
                    kind, event = self.receive(connection)
                    yield EVENTS.get(kind), event
        return stream()


def discover():
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
    found = []
    for path in runtime.glob(f"sway-ipc.{os.getuid()}.*.sock"):
        try:
            if Path(f"/proc/{path.name.split('.')[2]}/comm").read_text().strip() == "sway":
                found.append((path.stat().st_mtime, str(path)))
        except (OSError, IndexError):
            continue
    return max(found)[1] if found else None


def config_path(socket_path):
    """The config file the sway behind `socket_path` was started with."""
    home = Path.home()
    settings = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    try:
        pid = Path(socket_path).name.split(".")[2]
        arguments = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace").split("\0")
        for index, argument in enumerate(arguments):
            given = argument.partition("=")[2] if argument.startswith("--config=") else None
            if argument in ("-c", "--config") and index + 1 < len(arguments):
                given = arguments[index + 1]
            if given:
                return Path(os.readlink(f"/proc/{pid}/cwd")) / given
    except (OSError, IndexError):
        pass
    places = (home / ".sway/config", settings / "sway/config", home / ".i3/config", settings / "i3/config",
              Path("/etc/sway/config"), Path("/etc/i3/config"))
    return next((path for path in places if path.is_file()), None)


def bindings(text, directory, variables=None, seen=None):
    """The key bindings of a sway config outside modes, as (words before the
    command, command), in the order sway reads them. Includes are followed."""
    variables = {} if variables is None else variables
    seen = set() if seen is None else seen
    found, block, depth = [], None, 0
    for line in text.replace("\\\n", "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        words = line.split()
        if words[0] == "set" and len(words) > 2 and words[1].startswith("$") and not depth:
            line = f"set {words[1]} {replaced(line.split(None, 2)[2], variables)}"
            variables[words[1]] = line.split(None, 2)[2]
            continue
        line = replaced(line, variables)
        words = line.split()
        if depth:
            depth += (words[-1] == "{") - (line == "}")
            if block is not None and depth == 1 and line != "}" and words[-1] != "{":
                found.append(bound([*block, *words]))
        elif words[-1] == "{":
            depth, block = 1, words[:-1] if words[0] in ("bindsym", "bindcode") else None
        elif words[0] in ("bindsym", "bindcode"):
            found.append(bound(words))
        elif words[0] == "include" and len(words) > 1:
            pattern = os.path.expandvars(os.path.expanduser(line.split(None, 1)[1]))
            for name in sorted(glob.glob(os.path.join(directory, pattern))):
                path = Path(name).resolve()
                if path in seen or not path.is_file():
                    continue
                seen.add(path)
                with contextlib.suppress(OSError, UnicodeDecodeError):
                    found += bindings(path.read_text(), path.parent, variables, seen)
    return [binding for binding in found if binding]


def replaced(line, variables):
    for name in sorted(variables, key=len, reverse=True):
        line = line.replace(name, variables[name])
    return line


def bound(words):
    keys = next((index for index, word in enumerate(words) if index and not word.startswith("--")), None)
    if keys is None or keys + 1 >= len(words):
        return None
    return " ".join(words[:keys + 1]), " ".join(words[keys + 1:])


def same_keys(prefix):
    kind, *flags, keys = prefix.split()
    return kind, frozenset(flags), frozenset(keys.lower().split("+"))


def following(command):
    """`nop layout move …` for a binding that is sway's own move, else None."""
    match = MOVING.fullmatch(" ".join(command.split()))
    if match is None or match[2] in ELSEWHERE:
        return None
    return f"nop layout move {match[1] or match[2]}"


def flat(layout):
    return lambda ids: (layout, ids)


def tile(outer, inner, master_last=False):
    def arrange(ids):
        stack = (inner, ids[1:])
        return (outer, [stack, ids[0]] if master_last else [ids[0], stack])
    return arrange


def centered(ids):
    if len(ids) < 3:
        return tile("splith", "splitv")(ids)
    return ("splith", [("splitv", ids[2::2]), ids[0], ("splitv", ids[1::2])])


def turning(*directions):
    def arrange(ids, step=0):
        direction = directions[step % len(directions)]
        if len(ids) < 2:
            return (SPLITS[direction], ids)
        rest = arrange(ids[1:], step + 1)
        pair = [rest, ids[0]] if direction in ("left", "up") else [ids[0], rest]
        return (SPLITS[direction], pair)
    return arrange


def grid(ids):
    columns = math.ceil(math.sqrt(len(ids)))
    return ("splitv", [("splith", ids[row:row + columns]) for row in range(0, len(ids), columns)])


LAYOUTS = {
    "default": None,
    "tabbed": flat("tabbed"),
    "stacking": flat("stacked"),
    "master": tile("splith", "splitv"),
    "master-right": tile("splith", "splitv", master_last=True),
    "wide": tile("splitv", "splith"),
    "tabbed-master": tile("splith", "tabbed"),
    "stacked-master": tile("splith", "stacked"),
    "centered": centered,
    "dwindle": turning("right", "down"),
    "spiral": turning("right", "down", "left", "up"),
    "grid": grid,
    "float": flat("float"),
}
DESCRIPTIONS = {
    "default": "Left to sway, no layout",
    "tabbed": "Tabs, one window visible",
    "stacking": "Stacked titles, one window visible",
    "master": "Master left, stack right",
    "master-right": "Master right, stack left",
    "wide": "Master on top, stack below",
    "tabbed-master": "Master left, stack as tabs",
    "stacked-master": "Master left, stack as stacked titles",
    "centered": "Master centred, stack on both sides",
    "dwindle": "Splits right, then down, shrinking",
    "spiral": "Clockwise spiral inwards",
    "grid": "Even grid",
    "float": "All windows floating",
}


def normalize(node):
    if isinstance(node, int) or node is None:
        return node
    children = [child for child in map(normalize, node[1]) if child is not None]
    return (node[0], children) if children else None


def loose(node):
    if isinstance(node, int) or node is None:
        return node
    children = [child for child in map(loose, node[1]) if child is not None]
    if len(children) == 1 and node[0] not in TABBED:
        return children[0]
    return (node[0], children) if children else None


def plain(node):
    node = loose(node)
    if isinstance(node, int) or node is None:
        return node
    merged = []
    for child in map(plain, node[1]):
        nested = not isinstance(child, int) and child[0] == node[0] and node[0] not in TABBED
        merged += child[1] if nested else [child]
    return (node[0], merged)


def singles(node):
    if isinstance(node, int):
        return []
    found = [node] if len(node[1]) == 1 and node[0] not in TABBED and isinstance(node[1][0], int) else []
    return found + [single for child in node[1] for single in singles(child)]


def trimmed(node):
    node = normalize(node)
    while node and not isinstance(node, int) and len(node[1]) == 1 and node[0] not in TABBED:
        node = node[1][0]
    return node


def without(node, *cons):
    if isinstance(node, int):
        return None if node in cons else node
    return (node[0], [child for child in (without(child, *cons) for child in node[1]) if child is not None])


def parent_of(node, con):
    if isinstance(node, int):
        return None
    if con in node[1]:
        return node
    return next((parent for child in node[1] if (parent := parent_of(child, con))), None)


def leaves(node):
    return [node] if isinstance(node, int) else [leaf for child in node[1] for leaf in leaves(child)]


def bare(node):
    return node if isinstance(node, int) or node is None else ("", [bare(child) for child in node[1]])


def outline(node):
    return None if node is None else plain(trimmed(node))


def layout_command(layout):
    return f"layout {'stacking' if layout == 'stacked' else layout}"


def ancestors(node, con):
    if node["id"] == con:
        return [node]
    return next(([node, *path] for child in node["nodes"] if (path := ancestors(child, con))), [])


def shape(node):
    return node["id"] if not node["nodes"] else (node["layout"], [shape(child) for child in node["nodes"]])


def descendants(node):
    return [con for child in node["nodes"] for con in (child["id"], *descendants(child))]


def containers(node):
    return [found for child in node["nodes"] if child["nodes"] for found in (child, *containers(child))]


def leaf_nodes(node):
    if node["type"] == "con" and not node["nodes"]:
        return [node]
    return [leaf for child in node["nodes"] for leaf in leaf_nodes(child)]


def tiled(node):
    return [leaf["id"] for leaf in leaf_nodes(node)]


def covered(node):
    return any(child.get("fullscreen_mode") or covered(child) for child in node["nodes"] + node["floating_nodes"])


def top(workspace):
    node = workspace
    while len(node["nodes"]) == 1 and node["layout"] not in TABBED and node["nodes"][0]["nodes"]:
        node = node["nodes"][0]
    return node


def shown(node):
    if not node["nodes"]:
        return [node] if node["type"] == "con" else []
    children = node["nodes"]
    if node["layout"] in TABBED:
        children = [next((child for con in node["focus"] for child in children if child["id"] == con), children[0])]
    return [leaf for child in children for leaf in shown(child)]


def box(node):
    rect, deco = node["rect"], node["deco_rect"]["height"]
    return rect["x"], rect["y"] - deco, rect["x"] + rect["width"], rect["y"] + rect["height"]


def beside(workspace, node, direction, order):
    parent = ancestors(workspace, node["id"])[-2]
    if parent["layout"] == ("tabbed" if direction in ("left", "right") else "stacked"):
        siblings = parent["nodes"]
        index = [child["id"] for child in siblings].index(node["id"]) + (1 if direction in ("right", "down") else -1)
        if 0 <= index < len(siblings) and not siblings[index]["nodes"]:
            return siblings[index]["id"]
    left, top, right, bottom = box(node)
    across = direction in ("left", "right")
    found = []
    for leaf in shown(workspace):
        x1, y1, x2, y2 = box(leaf)
        gap = {"left": left - x2, "right": x1 - right, "up": top - y2, "down": y1 - bottom}[direction]
        overlap = min(bottom, y2) - max(top, y1) if across else min(right, x2) - max(left, x1)
        size = min(bottom - top, y2 - y1) if across else min(right - left, x2 - x1)
        if leaf["id"] != node["id"] and gap >= 0 and overlap > 0:
            rank = order.index(leaf["id"]) if leaf["id"] in order else len(order)
            found.append((round(gap / 10), 2 * overlap < size - 2, rank, leaf["id"]))
    return min(found)[3] if found else None


def windows(workspace):
    return [*leaf_nodes(workspace), *workspace["floating_nodes"]]


def outputs(tree):
    return [output for output in tree["nodes"] if output["name"] != "__i3"]


def workspaces(tree):
    return [ws for output in outputs(tree) for ws in output["nodes"]]


def visible(tree):
    return {output.get("current_workspace") for output in tree["nodes"]}


def focused_node(tree):
    stack = [tree]
    while stack:
        node = stack.pop()
        if node.get("focused"):
            return node
        stack += node["nodes"] + node["floating_nodes"]
    node = tree
    while node["focus"]:
        node = next(child for child in node["nodes"] + node["floating_nodes"] if child["id"] == node["focus"][0])
    return node


def invisible(tree):
    return [node["id"] for ws in workspaces(tree) for node in ws["floating_nodes"] if node.get("opacity") == 0]


def marked(node, prefix):
    found = {mark: node["id"] for mark in node.get("marks", ()) if mark.startswith(prefix)}
    for child in node["nodes"] + node["floating_nodes"]:
        found.update(marked(child, prefix))
    return found


def encoded(name):
    return name.encode().hex()


def criteria(name):
    return re.escape(name).replace('"', '\\"')


def elsewhere(name):
    """A pattern for sway criteria, free of quotes and spaces, that matches the workspaces not called `name`."""
    return "^(?!" + "".join(letter if letter.isascii() and (letter.isalnum() or letter in "_-") else "." for letter in name) + "$)"


def quoted(name):
    return '"' + name.replace('\\', '\\\\').replace('"', '\\"') + '"'


def build(node, root=True):
    if isinstance(node, int):
        return []
    layout, children = node
    first = leaves(node)[0]
    commands = [] if root else [f"[con_id={first}] split h"]
    commands += [f"[con_id={first}] {layout_command(layout)}", f"[con_id={first}] mark --add {MARK}"]
    commands += [f"[con_id={leaves(child)[0]}] move to mark {MARK}" for child in reversed(children[1:])]
    commands.append(f"[con_id={first}] unmark {MARK}")
    return commands + [command for child in children for command in build(child, False)]


def after(anchor, con):
    return [f"[con_id={anchor}] mark --add {MARK}", f"[con_id={con}] move to mark {MARK}", f"[con_id={anchor}] unmark {MARK}"]


def refocus(con):
    """Focus `con` again, unless the user has gone to another workspace since the tree was read."""
    return f"[con_id={con} workspace=__focused__] focus"


def current(sway, workspace_id):
    return next(ws for ws in workspaces(sway.tree()) if ws["id"] == workspace_id)


def placing(workspace, target, new, focused):
    """The commands that take `new` from just after the last window to its
    place in `target`, when nothing else is out of place."""
    parent = parent_of(target, new)
    if parent is None or trimmed(without(target, new)) != trimmed(without(shape(workspace), new)):
        return None
    layout, children = parent
    index = children.index(new)
    siblings = children[:index] + children[index + 1:]
    anchor = siblings[max(index - 1, 0)] if siblings else new
    grand = parent_of(target, parent) if not siblings else None
    follower = grand[1][1] if grand and grand[1][0] == parent else None
    swapped = isinstance(follower, int)
    holders = [node["id"] for node in ancestors(workspace, anchor)[1:-1]] if isinstance(anchor, int) else []
    if (siblings or swapped) and not holders:
        return None
    parked = (siblings or swapped) and focused in leaves(target)
    commands = [f"[con_id={new}] swap container with con_id {follower}"] if swapped else []
    if siblings:
        commands += after(anchor, new)
    else:
        commands += [f"[con_id={anchor}] split h", f"[con_id={anchor}] {layout_command(layout)}"]
    commands += [f"[con_id={new}] swap container with con_id {anchor}"] if index == 0 and siblings else []
    return commands + ([refocus(focused)] if parked else [])


def insert(sway, workspace, target, new, focused):
    commands = placing(workspace, target, new, focused)
    if commands is None:
        return False
    sway.command(*commands)
    return trimmed(shape(current(sway, workspace["id"]))) == target


def tidy(sway, workspace, target, new):
    wanted = {leaf: layout for layout, (leaf,) in singles(target)}
    nested = [node for top in workspace["nodes"] for node in containers(top)]
    parents = {child["id"]: node for node in [workspace, *containers(workspace)] for child in node["nodes"]}
    commands = [f"[con_id={node['nodes'][0]['id']}] split none" for node in nested
                if len(node["nodes"]) == 1 and node["layout"] not in TABBED
                and wanted.get(node["nodes"][0]["id"]) != node["layout"]]
    for leaf, layout in wanted.items():
        parent = parents[leaf]
        others = [child["id"] for child in parent["nodes"] if child["id"] != new]
        if parent["type"] != "con" or parent["layout"] != layout or others != [leaf]:
            commands += [f"[con_id={leaf}] split h", f"[con_id={leaf}] {layout_command(layout)}"]
    if not commands:
        return workspace
    sway.command(*commands)
    return current(sway, workspace["id"])


def relabel(node, target):
    if isinstance(target, int):
        return [] if not node["nodes"] and node["id"] == target else None
    layout, children = target
    if len(node["nodes"]) != len(children):
        return None
    found = [relabel(child, part) for child, part in zip(node["nodes"], children, strict=True)]
    if None in found:
        return None
    commands = [] if node["layout"] == layout else [f"[con_id={node['nodes'][0]['id']}] {layout_command(layout)}"]
    return commands + [command for part in found for command in part]


def unstacking(workspace, focused):
    """The commands that turn a tabbed or stacked workspace back into a split one.
    sway changes the workspace's own layout only while the workspace itself has
    focus; `layout` on a window right under it wraps the windows in a new
    container and leaves the workspace as it was."""
    if workspace["layout"] not in TABBED or covered(workspace):
        return []
    if focused["id"] == workspace["id"]:
        return ["layout splith"]
    if not workspace["nodes"]:
        return []
    back = f"[con_id={focused['id']}] focus" if focused["type"] != "workspace" else f"workspace {quoted(focused['name'])}"
    return [f"[con_id={workspace['nodes'][0]['id']}] focus", "focus parent", "layout splith", back]


def restyle(sway, workspace, target):
    node = workspace
    while len(node["nodes"]) == 1 and node["layout"] not in TABBED:
        node = node["nodes"][0]
    commands = relabel(node, target)
    if commands:
        sway.command(*commands)
    return bool(commands)


def rearrange(sway, workspace, target, focused):
    if isinstance(target, int):
        # A lone window keeps the geometry of the tabs it was in when `layout`
        # only flattens its parent: sway arranges the workspace only when the
        # layout changes. `split none` flattens and arranges.
        parent = ancestors(workspace, target)[-2]
        alone = parent["type"] == "con" and len(parent["nodes"]) == 1
        return sway.command(f"[con_id={target}] {'split none' if alone else 'layout splith'}")
    if len(target[1]) == 1:
        return sway.command(f"[con_id={target[1][0]}] {layout_command(target[0])}")
    if all(not node["nodes"] for node in workspace["nodes"]):
        sway.command(f"[con_id={workspace['nodes'][0]['id']}] split h")
        workspace = current(sway, workspace["id"])
    sway.command(*rebuilding(workspace, target, focused))


def rebuilding(workspace, target, focused):
    """The commands that gather the windows in a container of the workspace
    and build `target` from them, or None if it has no container."""
    root = next((node["id"] for node in workspace["nodes"] if node["nodes"]), None)
    if root is None:
        return None
    commands = [f"[con_id={root}] mark --add {MARK}"]
    commands += [f"[con_id={leaf}] move to mark {MARK}" for leaf in leaves(target)]
    commands += [f"[con_id={root}] unmark {MARK}", *build(target)]
    return commands + ([refocus(focused)] if focused in leaves(target) else [])


def settling(workspace, target, con):
    """The commands that take `con`, put just after the workspace's last
    window, to its place in `target`."""
    steps = placing(workspace, target, con, con) or rebuilding(workspace, target, con)
    if steps is None and all(not node["nodes"] for node in workspace["nodes"]):
        steps = build(target)
    return steps or []


def conforming(layout, present):
    cons = leaves(present) if present is not None else []
    if not cons:
        return None
    logical = [0] * len(cons)
    for position, index in enumerate(leaves(trimmed(layout(list(range(len(cons))))))):
        logical[index] = cons[position]
    return logical if outline(layout(logical)) == outline(present) else None


def reorder(order, ids):
    slots, wanted = iter(ids), set(ids)
    order[:] = [next(slots) if con in wanted else con for con in order]


def stairs(area):
    """The cascade starts two steps above the centre and goes down as far as the output has room."""
    room = min(area["width"] - area["width"] * 3 // 5, area["height"] - area["height"] * 3 // 5) // 2 // CASCADE
    above = min(2, room)
    return above, above + room + 1


def cascade(area, slot):
    width, height = area["width"] * 3 // 5, area["height"] * 3 // 5
    above, steps = stairs(area)
    offset = CASCADE * (slot % steps - above)
    return width, height, area["x"] + (area["width"] - width) // 2 + offset, area["y"] + (area["height"] - height) // 2 + offset


def placement(width, height, x, y):
    return f"resize set {width} px {height} px, move absolute position {x} px {y} px"


def origin(node):
    return node["rect"]["x"], node["rect"]["y"] - node["deco_rect"]["height"]


def fits(node, area):
    x, y = origin(node)
    width, height = node["rect"]["width"], node["rect"]["height"] + node["deco_rect"]["height"]
    return area["x"] <= x and area["y"] <= y and x + width <= area["x"] + area["width"] and y + height <= area["y"] + area["height"]


def free_slot(area, taken, start):
    steps = stairs(area)[1]
    return next((slot % steps for slot in range(start, start + steps) if cascade(area, slot % steps)[2:] not in taken), start % steps)


def resolve(tree, target):
    number = target.split()[1] if target.startswith("number ") else None
    found = [ws for ws in workspaces(tree) if (ws["num"] == int(number) if number and number.isdigit() else ws["name"] == target)]
    return found[0] if found else None


def crosses(workspace, con, direction):
    path = ancestors(workspace, con)
    chain = path[:-1]
    while len(chain) > 1 and len(chain[0]["nodes"]) == 1:
        chain.pop(0)
    while len(chain) > 1 and len(chain[-1]["nodes"]) == 1:
        chain.pop()
    top = chain[0]
    if len(chain) != 1 or top["layout"] not in PARALLEL[direction]:
        return False
    ids = [child["id"] for child in top["nodes"]]
    index = ids.index(path[path.index(top) + 1]["id"])
    return index == (len(ids) - 1 if direction in ("right", "down") else 0)


def presses(workspace, con, direction):
    """How many of sway's own `move <direction>` take `con` to the next
    output, or None if one would move it inside the workspace. A window at
    the edge of a container first leaves the container, which shows nothing
    when the container only wraps the workspace's windows. This follows
    container_move_in_direction in sway/commands/move.c."""
    def model(node, parent):
        copy = {"layout": node["layout"], "parent": parent}
        copy["nodes"] = [model(child, copy) for child in node["nodes"]]
        found.update({node["id"]: copy})
        return copy

    def family(layout):
        return "h" if layout in ("splith", "tabbed") else "v" if layout in ("splitv", "stacked") else None

    def squash_children(node):
        index = 0
        while index < len(node["nodes"]):
            index += squash(node["nodes"][index]) + 1

    def squash(node):
        if len(node["nodes"]) != 1:
            squash_children(node)
            return 0
        child, parent = node["nodes"][0], node["parent"]
        if not (node["layout"] in ("splith", "splitv") and child["layout"] in ("splith", "splitv")
                and family(node["layout"]) != family(child["layout"]) and family(parent["layout"]) == family(child["layout"])):
            squash_children(node)
            return 0
        index = parent["nodes"].index(node)
        parent["nodes"].remove(node)
        for grandchild in child["nodes"]:
            parent["nodes"].insert(index, grandchild)
            grandchild["parent"] = parent
        return len(child["nodes"]) - 1

    def step(root, window):
        offset = -1 if direction in ("left", "up") else 1
        current, wrapped = window, False
        while True:
            parent = current["parent"]
            if family(parent["layout"]) != family(SPLITS[direction]):
                if parent is root:
                    wrapper = {"layout": root["layout"], "parent": root, "nodes": root["nodes"]}
                    for child in wrapper["nodes"]:
                        child["parent"] = wrapper
                    root["nodes"], root["layout"], wrapped, current = [wrapper], SPLITS[direction], True, wrapper
                else:
                    current = parent
                continue
            index = parent["nodes"].index(current)
            beside = 0 <= index + offset < len(parent["nodes"])
            if current is not window:
                break
            if beside:
                return "within"
            if parent is root:
                return "cross"
            current = parent
        old = window["parent"]
        if beside:
            return "within"
        if not wrapped and old["parent"] is root and len(old["nodes"]) == 1:
            return "cross"
        old["nodes"].remove(window)
        parent["nodes"].insert(index + (0 if offset < 0 else 1), window)
        window["parent"] = parent
        while old is not root and not old["nodes"]:
            old["parent"]["nodes"].remove(old)
            old = old["parent"]
        squash_children(root)
        return "promote"

    found = {}
    root = model(workspace, None)
    for count in range(1, len(ancestors(workspace, con)) + 2):
        outcome = step(root, found[con])
        if outcome != "promote":
            return count if outcome == "cross" else None
    return None


def neighbour(tree, workspace, direction):
    home = next(output for output in outputs(tree) if any(ws["id"] == workspace["id"] for ws in output["nodes"]))["rect"]
    x, y = home["x"] + home["width"] / 2, home["y"] + home["height"] / 2

    def beyond(box):
        return {"right": box["x"] >= home["x"] + home["width"], "left": box["x"] + box["width"] <= home["x"],
                "down": box["y"] >= home["y"] + home["height"], "up": box["y"] + box["height"] <= home["y"]}[direction]

    def distance(box):
        dx = max(box["x"] - x, 0, x - box["x"] - box["width"])
        dy = max(box["y"] - y, 0, y - box["y"] - box["height"])
        return dx * dx + dy * dy

    found = min((output for output in outputs(tree) if beyond(output["rect"])), key=lambda output: distance(output["rect"]), default=None)
    return next((ws for ws in found["nodes"] if ws["name"] == found.get("current_workspace")), None) if found else None


def facing(node, direction):
    if isinstance(node, int):
        return [node]
    if node[0] == SPLITS[direction]:
        return facing(node[1][0 if direction in ("right", "down") else -1], direction)
    return [leaf for child in node[1] for leaf in facing(child, direction)]


def floater(workspace, node, direction):
    axis, sign = (0 if direction in ("left", "right") else 1), (-1 if direction in ("left", "up") else 1)

    def centre(con):
        x, y = origin(con)
        return (x + con["rect"]["width"] / 2, y + (con["rect"]["height"] + con["deco_rect"]["height"]) / 2)[axis]
    ahead = [(sign * (centre(other) - centre(node)), other["id"]) for other in workspace["floating_nodes"] if other["id"] != node["id"]]
    return min((pair for pair in ahead if pair[0] >= 0), key=lambda pair: pair[0], default=(None, None))[1]


def known(value, choices, default):
    value = RENAMED.get(value, value) if isinstance(value, str) else value
    return value if isinstance(value, str) and value in choices else default


def load_state():
    try:
        saved = json.loads(STATE.read_text())
    except (OSError, ValueError):
        saved = {}
    saved = saved if isinstance(saved, dict) else {}
    chosen = saved.get("workspaces") if isinstance(saved.get("workspaces"), dict) else {}
    return {"layout": known(saved.get("layout"), LAYOUTS, "default"),
            "workspaces": {name: known(layout, LAYOUTS, None) for name, layout in chosen.items() if known(layout, LAYOUTS, None)}}


def save_state(state, path=None):
    path = path or STATE
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}")
    temporary.write_text(json.dumps(state))
    os.replace(temporary, path)


def runtime_path(sway, suffix):
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
    return runtime / f"swaytiles.{Path(sway.path).name}.{suffix}"


def frozen(node):
    if isinstance(node, int) and not isinstance(node, bool):
        return node
    valid = isinstance(node, list) and len(node) == 2 and node[0] in (*TABBED, *SPLITS.values()) and isinstance(node[1], list)
    children = [frozen(child) for child in node[1]] if valid else [None]
    return (node[0], children) if None not in children else None


def load_session(path):
    try:
        saved = json.loads(path.read_text())
    except (OSError, ValueError):
        saved = {}
    saved = saved if isinstance(saved, dict) else {}
    built = saved.get("built")
    built = {name: tree for name, tree in ((name, frozen(tree)) for name, tree in (built.items() if isinstance(built, dict) else ()))
             if tree is not None}
    kept = saved.get("kept")
    kept = {int(con): name for con, name in (kept.items() if isinstance(kept, dict) else ()) if con.isdigit() and isinstance(name, str)}
    return built, kept


class Daemon:
    def __init__(self, sway, keys=True):
        self.sway = sway
        self.keys = keys
        self.adopted = {}
        self.state = load_state()
        self.order = []
        self.rules = {}
        self.anchored = {}
        self.slots = {}
        self.where = {}
        self.names = {}
        self.pending = {}
        self.areas = {}
        self.tiled = set()
        self.ratios = {}
        self.carried = (None, None, None)
        self.focus = [None, None]
        self.refocused = False
        self.session = runtime_path(sway, "json")
        self.built, self.kept = load_session(self.session)
        self.saved = self.remembered()
        self.sync_id = 0
        self.syncing = None

    def remembered(self):
        return {"built": dict(self.built), "kept": {str(con): name for con, name in sorted(self.kept.items())}}

    def remember(self):
        now = self.remembered()
        if now != self.saved:
            save_state(now, self.session)
            self.saved = now

    def chosen(self, name):
        return self.state["workspaces"].get(name)

    def chosen_tiling(self, name):
        return None if self.chosen(name) == "float" else LAYOUTS.get(self.chosen(name))

    def tiling(self, name):
        return self.chosen_tiling(name)

    def ordered(self, cons):
        cons = set(cons)
        return [con for con in self.order if con in cons]

    def target(self, name, ids):
        return trimmed(self.tiling(name)(ids)) if ids else None

    def inspect(self, workspace, ids, arrived):
        name, seen = workspace["name"], self.built.get(workspace["name"])
        known = set(leaves(seen)) if seen is not None else set()
        extra = [con for con in ids if con not in known]
        present = without(shape(workspace), *extra)
        before = without(seen, *known.difference(ids)) if seen is not None else None
        if seen is not None and outline(before) != outline(present):
            self.adapt(workspace, present, bare(trimmed(before)) == bare(trimmed(present)))
        elif arrived in ids and (found := conforming(self.tiling(name), shape(workspace))):
            reorder(self.order, found)

    def adapt(self, workspace, present, restyled):
        name = workspace["name"]
        candidates = [(self.chosen(name), self.tiling(name))]
        if restyled:
            candidates += [(other, layout) for other, layout in LAYOUTS.items() if layout and other not in ("float", self.chosen(name))]
        kept = self.ordered(leaves(present))
        for other, layout in candidates:
            found = conforming(layout, present)
            if found and (other == self.chosen(name) or found == kept):
                reorder(self.order, found)
                if other != self.chosen(name):
                    self.state["workspaces"][name] = other
                    save_state(self.state)
                return
        notify(f"Workspace {name} switched to default after a manual change. Pick a layout from the menu to tile it again.")
        self.state["workspaces"][name] = "default"
        save_state(self.state)
        self.built.pop(name, None)

    def measure(self, workspace, ids):
        node = top(workspace)
        master = next((child for child in node["nodes"] if child["id"] == ids[0]), None) if ids else None
        if node["layout"] in ("splith", "splitv") and len(node["nodes"]) > 1 and master is not None:
            self.share(workspace["name"], node["layout"], master, node["rect"], len(node["nodes"]))

    def share(self, name, layout, master, area, count):
        axis = "width" if layout == "splith" else "height"
        size = master["rect"][axis] + (master["deco_rect"]["height"] if axis == "height" else 0)
        ratio = round(size / area[axis], 2)
        if abs(ratio - 1 / count) < 0.02:
            self.ratios.pop(name, None)
        elif abs(ratio - self.ratios.get(name, 0)) >= 0.02:
            self.ratios[name] = ratio

    def farewell(self, node):
        name = self.where.get(node["id"])
        built = self.built.get(name)
        if isinstance(built, int) or built is None or built[0] not in ("splith", "splitv") or len(built[1]) < 2:
            return
        master = next((con for con in self.order if con in leaves(built)), None)
        workspace = next((ws for ws in workspaces(self.sway.tree()) if ws["name"] == name), None)
        if master == node["id"] and master in built[1] and workspace is not None:
            self.share(name, built[0], node, workspace["rect"], len(built[1]))

    def measure_all(self, tree):
        for workspace in workspaces(tree):
            name = workspace["name"]
            if self.tiling(name) is None or covered(workspace):
                continue
            ids = self.ordered(tiled(workspace))
            if ids and trimmed(shape(workspace)) == self.target(name, ids):
                self.measure(workspace, ids)

    def resize(self, name, target, ids):
        ratio = self.ratios.get(name)
        if ratio is None or isinstance(target, int) or target[0] not in ("splith", "splitv") or ids[0] not in target[1]:
            return []
        return [f"[con_id={ids[0]}] resize set {'width' if target[0] == 'splith' else 'height'} {round(ratio * 100)} ppt"]

    def insist(self, command):
        for delay in (0.3, 1.0):
            threading.Timer(delay, self.sway.command, [command]).start()

    def sync(self):
        self.sync_id += 1
        self.sway.tick(f"{SYNC} {self.sync_id}")
        self.syncing = self.sync_id

    def float_rule(self, name, geometry):
        if self.rules.get(name) == geometry or (geometry is None and name not in self.rules):
            return []
        prefix = f"$layout_float_{encoded(name)}_"
        values = {"mark": "_layout_idle"} if geometry is None else dict(zip(("mark", "w", "h", "x", "y"), ("_layout_new", *geometry), strict=True))
        commands = [f"set {prefix}{key} {value}" for key, value in values.items()]
        if name not in self.rules:
            variables = [f"${prefix}{key}" for key in ("mark", "w", "h", "x", "y")]
            action = (f"mark --add {variables[0]}; [con_mark=^_layout_new$] floating enable, opacity 0, {placement(*variables[1:])}; "
                      "[con_mark=^_layout_new$] unmark _layout_new; [con_mark=^_layout_idle$] unmark _layout_idle")
            commands.append(f'for_window [workspace="^{criteria(name)}$" tiling] "{action}"')
        self.rules[name] = geometry
        return commands

    def tile_rule(self, name, active):
        if self.anchored.get(name) == active or (not active and name not in self.anchored):
            return []
        variable = f"$layout_tile_{encoded(name)}"
        commands = [] if self.anchored else ["set $layout_gate _layout_off", 'for_window [all] "mark --add $$layout_gate; unmark $$layout_gate"',
                                             "[all] mark --add _layout_arm", "unmark _layout_arm", "set $layout_gate _layout_fresh"]
        commands.append(f"set {variable} {AFTER + encoded(name) if active else '_layout_off'}")
        if name not in self.anchored:
            mark = AFTER + encoded(name)
            action = (f"[con_mark=^{mark}$ workspace={elsewhere(name)}] unmark {mark}; "
                      f"[con_mark=^_layout_fresh$] move container to mark ${variable}; [con_mark=^_layout_fresh$] mark --add ${variable}; "
                      "[con_mark=^_layout_off$] unmark _layout_off")
            commands.append(f'for_window [workspace="^{criteria(name)}$" tiling] "{action}"')
        self.anchored[name] = active
        return commands

    def anchors(self, tree, tiles):
        held = marked(tree, AFTER)
        wanted, commands = {}, []
        for ws in workspaces(tree):
            active = self.chosen(ws["name"]) not in ("default", "float")
            commands += self.tile_rule(ws["name"], active)
            ids = self.ordered(tiles[ws["id"]])
            if active and ids:
                wanted[AFTER + encoded(ws["name"])] = ids[-1]
        commands += [f"unmark {mark}" for mark in held if mark not in wanted]
        return commands + [f"[con_id={con}] mark --add {mark}" for mark, con in wanted.items() if held.get(mark) != con]

    def reveal(self, con, width, height, hold=0.0):
        begin = time.monotonic()
        while time.monotonic() - begin < 1.5:
            node = next((node for ws in workspaces(self.sway.tree()) for node in windows(ws) if node["id"] == con), None)
            if node is None:
                break
            if (node["rect"]["width"], node["rect"]["height"] + node["deco_rect"]["height"]) == (width, height):
                time.sleep(max(0.05, hold - (time.monotonic() - begin)))
                break
            time.sleep(0.02)
        self.sway.command(f"[con_id={con}] opacity 1")

    def float_all(self, workspace, ids, new, shown):
        name, area = workspace["name"], workspace["rect"]

        def place(con, width, height, x, y):
            if shown:
                return f"[con_id={con}] {placement(width, height, x, y)}"
            self.pending.setdefault(name, {})[con] = (width, height, x, y)
            return f"[con_id={con}] resize set {width} px {height} px"

        resized = self.areas.get(name, area) != area
        self.areas[name] = area
        floating = workspace["floating_nodes"]
        fresh = [node for node in floating if node["id"] == new and f"{FLOATED}{new}" not in node["marks"]]
        misfits = [node for node in floating if resized and f"{FLOATED}{node['id']}" in node["marks"] and not fits(node, area)]
        taken = {origin(node) for node in floating if node not in fresh + misfits}
        start = self.slots.get(name, 0)
        commands, hidden = [], []
        for node in fresh + misfits:
            con = node["id"]
            slot = free_slot(area, taken, start) if node in misfits or origin(node) in taken else start % stairs(area)[1]
            geometry = cascade(area, slot)
            commands += [f"[con_id={con}] mark --add {FLOATED}{con}", place(con, *geometry)]
            taken.add(geometry[2:])
            start = slot + 1
            if node in fresh:
                hidden.append((con, *geometry[:2]))
        for con in ids:
            slot = free_slot(area, taken, start)
            geometry = cascade(area, slot)
            command = place(con, *geometry)
            commands += [f"[con_id={con}] opacity {0 if con == new else 1}, floating enable, mark --add {FLOATED}{con}", command]
            taken.add(geometry[2:])
            start = slot + 1
            if con == new:
                hidden.append((con, *geometry[:2], 0.45))
                self.insist(command)
        self.slots[name] = free_slot(area, taken, start)
        self.sway.command(*commands, *self.float_rule(name, cascade(area, self.slots[name])))
        for args in hidden:
            threading.Thread(target=self.reveal, args=args, daemon=True).start()

    def placed(self, tree, new, moved):
        where = {node["id"]: ws["name"] for ws in workspaces(tree) for node in windows(ws)}
        local = moved and new in where and self.where.get(new) == where[new]
        (carried, name, heading), self.carried = self.carried, (None, None, None)
        landed = where.get(carried) == name and self.where.get(carried) != name
        self.where = where
        self.names = {ws["id"]: ws["name"] for ws in workspaces(tree)}
        if landed:
            ids = self.ordered(con for ws in workspaces(tree) if ws["name"] == name for con in tiled(ws) if con != carried)
            layout = self.tiling(name)
            leads = bool(ids and layout and heading) and facing(normalize(layout([carried, *ids])), heading) == [carried]
            self.order[:] = [con for con in self.order if con != carried]
            self.order.insert(self.order.index(ids[0]) if leads else len(self.order), carried)
        if landed and new == carried:
            return False, None
        if local and self.syncing is not None:
            return True, None
        if not moved or local:
            return False, None
        if new in self.order:
            self.order.remove(new)
            self.order.append(new)
        return False, new

    def release(self, tree, new, moved):
        commands = []
        for ws in workspaces(tree):
            for node in ws["floating_nodes"]:
                mark = f"{FLOATED}{node['id']}"
                if mark in node["marks"] and self.chosen(ws["name"]) != "float":
                    commands.append(f"[con_id={node['id']}] unmark {mark}, floating disable, opacity 1")
                elif mark in node["marks"] and moved and node["id"] == new:
                    commands.append(f"[con_id={node['id']}] unmark {mark}")
        self.sway.command(*commands)
        return self.sway.tree() if commands else tree

    def shape_up(self, workspace, ids, new, focused):
        name = workspace["name"]
        target = self.target(name, ids)
        if target is None or target == trimmed(shape(workspace)):
            return False
        reference = trimmed(without(target, new)) if new in ids else target
        present = trimmed(without(shape(workspace), new))
        if reference != present and loose(reference) == loose(present):
            workspace = tidy(self.sway, workspace, reference, new)
        if (trimmed(shape(workspace)) != target and not restyle(self.sway, workspace, target)
                and (new not in ids or not insert(self.sway, workspace, target, new, focused))):
            rearrange(self.sway, current(self.sway, workspace["id"]), target, focused)
        self.sway.command(*self.resize(name, target, ids))
        return True

    def arrange(self, new=None, moved=False):
        tree = self.sway.tree()
        unseen = [ws["name"] for ws in workspaces(tree) if ws["name"] not in self.state["workspaces"]]
        if unseen:
            self.state["workspaces"].update(dict.fromkeys(unseen, self.state["layout"]))
            save_state(self.state)
        skip, arrived = self.placed(tree, new, moved)
        if skip:
            return self.remember()
        tree = self.release(tree, new, moved and arrived is not None)
        if unstack := [command for ws in workspaces(tree) if self.tiling(ws["name"]) is not None
                       for command in unstacking(ws, focused_node(tree))]:
            self.sway.command(*unstack)
            tree = self.sway.tree()
        tiles = {ws["id"]: tiled(ws) for ws in workspaces(tree)}
        self.kept = {con: ws["name"] for ws in workspaces(tree) for con in tiles[ws["id"]]
                     if self.kept.get(con) == ws["name"] and self.chosen(ws["name"]) == "float"}
        present = {node["id"] for ws in workspaces(tree) for node in windows(ws)}
        self.tiled = (self.tiled & present) | {con for ids in tiles.values() for con in ids}
        self.order[:] = [con for con in self.order if con in self.tiled]
        self.order += [con for ids in tiles.values() for con in ids if con not in self.order]
        for workspace in workspaces(tree):
            name = workspace["name"]
            if covered(workspace) or self.chosen_tiling(name) is None:
                continue
            if ids := self.ordered(tiles[workspace["id"]]):
                self.inspect(workspace, ids, arrived)
        self.sway.command(*self.anchors(tree, tiles))
        focused, shown, shaped = focused_node(tree)["id"], visible(tree), False
        for workspace in workspaces(tree):
            name = workspace["name"]
            ids = self.ordered(tiles[workspace["id"]])
            if covered(workspace):
                continue
            if self.chosen(name) == "float":
                self.float_all(workspace, [con for con in ids if con not in self.kept], new, name in shown)
            elif self.tiling(name) is not None:
                tree_shape = shape(workspace)
                if ids and (trimmed(tree_shape) == self.target(name, ids) or trimmed(without(tree_shape, new)) == self.built.get(name)):
                    self.measure(workspace, ids)
                shaped = self.shape_up(workspace, ids, new, focused) or shaped
        self.record(self.sway.tree() if shaped else tree)
        self.remember()
        if shaped:
            self.sync()

    def record(self, tree):
        for workspace in workspaces(tree):
            name = workspace["name"]
            if self.chosen_tiling(name) is None or not tiled(workspace):
                self.built.pop(name, None)
            elif not covered(workspace):
                self.built[name] = trimmed(shape(workspace))

    def move_to(self, target=None, direction=None):
        tree = self.sway.tree()
        node = focused_node(tree)
        source = next((ws for ws in workspaces(tree) if node in windows(ws)), None)
        native = f"move container to workspace {target}" if target else f"move {direction}"
        if source is None or node["nodes"]:
            return self.sway.command(native)
        con = node["id"]
        floated = f"{FLOATED}{con}" in node["marks"]
        managed = node["type"] == "con" and self.tiling(source["name"]) is not None and not covered(source)
        if direction and managed:
            other = beside(source, node, direction, self.order)
            if other is not None:
                return self.exchange(con, other)
            destination = neighbour(tree, source, direction)
            if destination is None:
                return None
        elif direction:
            crossing = floated or (node["type"] == "con" and crosses(source, con, direction))
            destination = neighbour(tree, source, direction) if crossing else None
        else:
            destination = resolve(tree, target)
        if destination is None or destination["id"] == source["id"]:
            return self.sway.command(f"[con_id={con}] {native}")
        name, area = destination["name"], destination["rect"]
        self.carried = (con, name, direction)
        command = native if target else f"move container to workspace {quoted(name)}, focus"
        floats = self.state["workspaces"].get(name, self.state["layout"]) == "float"
        if floats and name in visible(tree):
            taken = {origin(other) for other in destination["floating_nodes"]}
            slot = free_slot(area, taken, self.slots.get(name, 0))
            geometry = cascade(area, slot)
            self.slots[name] = free_slot(area, taken | {geometry[2:]}, slot + 1)
            self.where[con] = name
            self.sway.command(f"[con_id={con}] floating enable, mark --add {FLOATED}{con}, resize set {geometry[0]} px {geometry[1]} px, "
                              f"{command}, move absolute position {geometry[2]} px {geometry[3]} px",
                              *self.float_rule(name, cascade(area, self.slots[name])))
            self.insist(f"[con_id={con}] {placement(*geometry)}")
        elif floated and not floats:
            self.sway.command(f"[con_id={con}] unmark {FLOATED}{con}, floating disable, {command}")
        elif node["type"] == "con" and (steps := self.landing(destination, con, direction)):
            self.sway.command(*steps, *([f"[con_id={con}] focus"] if direction else []))
            self.record(self.sway.tree())
        else:
            steps = 1 if managed or not direction else presses(source, con, direction) or 1
            self.sway.command(f"[con_id={con}] " + (command if managed else ", ".join([native] * steps)))

    def landing(self, destination, con, heading):
        """The commands that move `con` straight to its place in the layout of
        `destination`, in one step, so sway never draws it where it would put
        it first, next to the focused window there."""
        name, ids = destination["name"], self.ordered(tiled(destination))
        layout = self.tiling(name)
        if layout is None or not ids or covered(destination):
            return None
        leads = bool(heading) and facing(normalize(layout([con, *ids])), heading) == [con]
        order = [con, *ids] if leads else [*ids, con]
        target = self.target(name, order)
        return after(ids[-1], con) + settling(destination, target, con) + self.resize(name, target, order)

    def unhide(self, con=None):
        """Bring a window back from the scratchpad, the last one hidden unless
        `con` says which, to where a new window would open on the focused
        workspace, in one step so it is not drawn anywhere else first."""
        tree = self.sway.tree()
        hidden = [node["id"] for output in tree["nodes"] if output["name"] == "__i3" for ws in output["nodes"] for node in ws["floating_nodes"]]
        if con is None and hidden:
            con = hidden[-1]
        if con not in hidden:
            return None
        name = self.sway.focused_workspace()
        workspace = next(ws for ws in workspaces(tree) if ws["name"] == name)
        commands = [f"[con_id={con}] scratchpad show"]
        if self.chosen(name) == "float":
            area = workspace["rect"]
            taken = {origin(node) for node in workspace["floating_nodes"]}
            slot = free_slot(area, taken, self.slots.get(name, 0))
            geometry = cascade(area, slot)
            self.slots[name] = free_slot(area, taken | {geometry[2:]}, slot + 1)
            commands += [f"[con_id={con}] mark --add {FLOATED}{con}, {placement(*geometry)}", *self.float_rule(name, cascade(area, self.slots[name]))]
        else:
            commands.append(f"[con_id={con}] floating disable")
            ids = [c for c in self.ordered(tiled(workspace)) if c != con]
            if ids and self.tiling(name) is not None and not covered(workspace):
                self.order[:] = [c for c in self.order if c != con] + [con]
                target = self.target(name, [*ids, con])
                commands += after(ids[-1], con) + settling(workspace, target, con) + self.resize(name, target, [*ids, con])
        self.where[con] = name
        self.sway.command(*commands, f"[con_id={con}] focus")
        self.record(self.sway.tree())
        self.remember()
        return self.sync()

    def escape(self, direction, refocused):
        tree = self.sway.tree()
        node = focused_node(tree)
        if node["type"] == "workspace":
            floating = {con["id"] for con in node["floating_nodes"]}
            latest = next((con for con in node["focus"] if con in floating), None)
            if latest is not None and self.chosen(node["name"]) == "float":
                self.sway.command(f"[con_id={latest}] focus")
            return
        source = next((ws for ws in workspaces(tree) if any(con["id"] == node["id"] for con in ws["floating_nodes"])), None)
        if source is None or node.get("fullscreen_mode") or self.chosen(source["name"]) != "float":
            return
        previous = next((con for con in source["floating_nodes"] if con["id"] == self.focus[0]), None)
        if refocused and previous is not None and floater(source, previous, direction) == node["id"]:
            return
        if floater(source, node, direction) is None and neighbour(tree, source, direction) is not None:
            self.sway.command(f"focus output {direction}")

    def promote(self):
        tree = self.sway.tree()
        node = focused_node(tree)
        workspace = next((ws for ws in workspaces(tree) if node["id"] in tiled(ws)), None)
        if workspace is None or self.tiling(workspace["name"]) is None or covered(workspace):
            return
        ids = self.ordered(tiled(workspace))
        if len(ids) > 1:
            self.exchange(node["id"], ids[1] if node["id"] == ids[0] else ids[0])

    def exchange(self, con, other):
        first, second = self.order.index(con), self.order.index(other)
        self.order[first], self.order[second] = other, con
        self.sway.command(f"[con_id={con}] swap container with con_id {other}")
        self.sync()
        self.arrange()

    def choose(self, choice):
        self.measure_all(self.sway.tree())
        name = self.sway.focused_workspace()
        previous = self.chosen(name)
        self.state["workspaces"][name] = choice
        save_state(self.state)
        self.built.pop(name, None)
        self.kept = {con: place for con, place in self.kept.items() if place != name}
        if previous == "float" and choice != "float":
            workspace = next(ws for ws in workspaces(self.sway.tree()) if ws["name"] == name)
            floated = [node["id"] for node in workspace["floating_nodes"] if f"{FLOATED}{node['id']}" in node["marks"]]
            self.slots.pop(name, None)
            self.sway.command(*(f"[con_id={con}] unmark {FLOATED}{con}, floating disable" for con in floated), *self.float_rule(name, None))
        if choice == "default":
            tree = self.sway.tree()
            workspace = next(ws for ws in workspaces(tree) if ws["name"] == name)
            self.sway.command(*unstacking(workspace, focused_node(tree)),
                              *(f"[con_id={con}] layout splith" for con in descendants(workspace)))
        self.arrange()

    def rename(self, workspace):
        old, new = self.names.get(workspace["id"]), workspace["name"]
        if old is None or old == new:
            return
        for table in (self.state["workspaces"], self.slots, self.pending, self.areas, self.ratios, self.built):
            if old in table and new not in table:
                table[new] = table.pop(old)
        self.kept = {con: new if place == old else place for con, place in self.kept.items()}
        save_state(self.state)

    def restore(self, sway):
        tree = sway.tree()
        commands = [command for name in list(self.rules) for command in self.float_rule(name, None)]
        commands += [command for name in list(self.anchored) for command in self.tile_rule(name, False)]
        sway.command(*commands, *(f"[con_id={con}] opacity 1" for con in invisible(tree)), *(f"unmark {mark}" for mark in marked(tree, AFTER)))

    def recover(self):
        self.sway.command(*(f"[con_id={con}] opacity 1" for con in invisible(self.sway.tree())))

    def handle(self, kind, event):
        change = event.get("change")
        if kind == "tick":
            payload = event.get("payload", "")
            if payload == f"{SYNC} {self.syncing}":
                self.syncing = None
            elif payload.startswith("layout ") and payload[7:] in LAYOUTS:
                self.choose(payload[7:])
            elif payload.startswith(f"{ACT} "):
                self.act(payload[len(ACT) + 1:])
        elif kind == "binding":
            command, refocused, self.refocused = event["binding"].get("command", ""), self.refocused, False
            if focusing := FOCUSING.fullmatch(command):
                self.escape(focusing[1], refocused)
            elif command.startswith("nop layout "):
                self.act(command[11:])
            elif RESHAPING.search(command):
                self.arrange()
            elif following(command):
                self.learn(event["binding"], command)
                self.measure_all(self.sway.tree())
            else:
                self.measure_all(self.sway.tree())
        elif kind == "output":
            self.arrange()
        elif kind == "workspace" and change == "focus":
            positions = self.pending.pop(event["current"]["name"], {})
            self.sway.command(*(f"[con_id={con}] {placement(*geometry)}" for con, geometry in positions.items()))
        elif kind == "workspace" and change in ("init", "move", "rename", "reload"):
            if change == "rename":
                self.rename(event["current"])
            if change == "reload":
                self.rules.clear()
                self.anchored.clear()
                self.adopted.clear()
                self.adopt()
            self.arrange()
        elif kind == "window" and change == "focus":
            self.focus, self.refocused = [self.focus[1], event["container"]["id"]], True
        elif kind == "window" and change == "fullscreen_mode":
            self.arrange()
        elif kind == "window" and change in ("new", "close", "floating", "move"):
            con = event["container"]["id"]
            if change == "close":
                self.farewell(event["container"])
            if change == "floating" and event["container"]["type"] == "con" and self.chosen(self.where.get(con)) == "float":
                self.kept[con] = self.where[con]
            elif change == "floating":
                self.kept.pop(con, None)
            if change == "new":
                self.sync()
                if con not in self.order:
                    self.order.append(con)
            self.arrange(con, moved=change == "move")

    def adopt(self):
        """Put the layout's moves on the keys that the config binds to sway's
        own `move`. Bindings made at runtime last until sway reloads."""
        self.give_back(self.sway)
        if not self.keys:
            return
        latest = {}
        with contextlib.suppress(OSError, KeyError, TypeError, ValueError):
            path = config_path(self.sway.path)
            for prefix, command in bindings(self.sway.request(Sway.CONFIG)["config"], path.parent if path else Path.home()):
                latest[same_keys(prefix)] = (prefix, command)
        self.adopted = {keys: binding for keys, binding in latest.items() if following(binding[1])}
        self.sway.command(*(f"{prefix} {following(command)}" for prefix, command in self.adopted.values()))

    def learn(self, binding, command):
        """A move key the config reader missed: take it over once it is used."""
        if not self.keys or binding.get("input_type") != "keyboard" or not binding.get("symbol"):
            return
        prefix = "bindsym " + "+".join([*binding.get("event_state_mask", []), binding["symbol"]])
        if same_keys(prefix) in self.adopted or self.sway.request(Sway.MODE).get("name") != "default":
            return
        self.adopted[same_keys(prefix)] = (prefix, command)
        self.sway.command(f"{prefix} {following(command)}")

    def give_back(self, sway):
        adopted, self.adopted = self.adopted, {}
        sway.command(*(f"{prefix} {command}" for prefix, command in adopted.values()))

    def act(self, action):
        if action == "master":
            self.promote()
        elif action == "show" or (action.startswith("show ") and action[5:].isdigit()):
            self.unhide(int(action[5:]) if action[5:] else None)
        elif action.startswith("default ") and action[8:] in LAYOUTS:
            self.state["layout"] = action[8:]
            save_state(self.state)
        elif action.startswith("move ") and action[5:].strip():
            argument = action[5:].strip()
            self.move_to(direction=argument) if argument in PARALLEL else self.move_to(target=argument)

    def run(self):
        events = self.sway.subscribe(SUBSCRIPTIONS)
        self.restore(self.sway)
        self.adopt()
        self.arrange()
        for kind, event in events:
            if kind == "shutdown":
                return False
            try:
                self.handle(kind, event)
            except ConnectionError:
                raise
            except Exception:
                import traceback
                traceback.print_exc(file=sys.stderr)
                sys.stderr.flush()
                self.recover()
        return False


def claim(sway):
    lock = open(runtime_path(sway, "lock"), "w")
    for _ in range(50):
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return lock
        except BlockingIOError:
            time.sleep(0.1)
    lock.close()
    return None


def stop(*_):
    raise SystemExit(0)


def running():
    """The socket of a sway that answers. systemd may still hold the SWAYSOCK
    of an earlier session, so the variable alone is not trusted."""
    for path in (os.environ.get("SWAYSOCK"), discover()):
        if path:
            with contextlib.suppress(OSError), socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.settimeout(2)
                probe.connect(path)
                return path
    return None


def wait(keys=True):
    """Serve every sway session of this login: wait for sway, run until it
    ends, wait for the next one."""
    while True:
        path = running()
        if path and main(keys, path) == 2:
            return 2
        time.sleep(1)


def main(keys=True, path=None):
    sway = Sway(path)
    try:
        lock = claim(sway)
    except ConnectionError as error:
        print(f"swaytiles: {error}", file=sys.stderr)
        return 1
    if lock is None:
        print("swaytiles: another daemon already runs for this sway session", file=sys.stderr)
        return 2
    for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(number, stop)
    daemon, alive = Daemon(sway, keys), True
    try:
        alive = daemon.run()
    except ConnectionError:
        alive = False
    finally:
        if alive:
            with contextlib.suppress(OSError, ValueError):
                closing = Sway(sway.path, timeout=2)
                daemon.restore(closing)
                daemon.give_back(closing)
        lock.close()
    return 0


def boxes(node, x, y, width, height):
    if isinstance(node, int):
        return [(node, x, y, width, height)]
    layout, children = node
    count = len(children)
    if layout == "tabbed":
        tabs = [(child, x + index * width / count, y, width / count, 4) for index, child in enumerate(children)]
        return [*tabs, (children[0], x, y + 4, width, height - 4)]
    if layout == "float":
        step = 0.4 / max(count - 1, 1)
        return [(child, x + (count - 1 - index) * step * width, y + (count - 1 - index) * step * height, width * 0.6, height * 0.6)
                for index, child in reversed(list(enumerate(children)))]
    if layout == "stacked":
        tabs = [(child, x, y + index * 4, width, 4) for index, child in enumerate(children)]
        return [*tabs, (children[0], x, y + 4 * count, width, height - 4 * count)]
    if layout == "splith":
        return [box for index, child in enumerate(children)
                for box in boxes(child, x + index * width / count, y, width / count, height)]
    return [box for index, child in enumerate(children)
            for box in boxes(child, x, y + index * height / count, width, height / count)]


def fuzzel_text(path=None, seen=None):
    """The colour fuzzel writes its text in, so the icons match any theme:
    fuzzel draws an SVG's currentColor black whatever the theme is."""
    if path is None:
        home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        folders = [home, *(Path(folder) for folder in (os.environ.get("XDG_CONFIG_DIRS") or "/etc/xdg").split(":") if folder)]
        path = next((folder / "fuzzel/fuzzel.ini" for folder in folders if (folder / "fuzzel/fuzzel.ini").is_file()), None)
    seen = set() if seen is None else seen
    colour, section = None, "main"
    if path is None or path in seen:
        return colour
    seen.add(path)
    try:
        lines = Path(path).read_text(errors="replace").splitlines()
    except OSError:
        return colour
    for line in lines:
        line = line.strip()
        if line.startswith("["):
            section = line.strip("[] ").lower()
            continue
        key, _, value = (part.strip() for part in line.partition("="))
        if key == "include":
            colour = fuzzel_text(Path(value).expanduser(), seen) or colour
        elif section == "colors" and key == "text" and re.fullmatch(r"[0-9a-fA-F]{8}", value):
            colour = f"#{value[:6]}"
    return colour


def icon(name, tree, highlight, colour):
    rects = "".join(
        f'<rect x="{x + 1:.1f}" y="{y + 1:.1f}" width="{width - 2:.1f}" height="{height - 2:.1f}" rx="1" '
        f'fill="{colour if leaf == highlight else "none"}" stroke="{colour}" stroke-width="1"/>'
        for leaf, x, y, width, height in boxes(normalize(tree), 0, 0, 48, 30))
    path = ICONS / f"{name}.svg"
    path.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 30">{rects}</svg>')
    return path


def select(sway, choice):
    choice = RENAMED.get(choice, choice)
    if choice in LAYOUTS:
        sway.tick(f"layout {choice}")


def act(sway, action):
    sway.tick(f"{ACT} {action}")
    return 0


LAUNCHERS = ("fuzzel", "rofi", "wofi", "tofi", "bemenu", "wmenu", "dmenu")
CONFIG = """\
# Lines for your sway config, printed by `swaytiles config`. Pick your own keys.

# Pick a layout for the focused workspace, in the menu program you use.
bindsym $mod+Shift+t exec {command} menu --launcher fuzzel

# Swap the focused window with the master.
bindsym $mod+m exec {command} swap

# Without the systemd service, start the daemon from here:
# exec {command}
"""


def launcher(custom, count, selected):
    """A known program by its name alone gets the arguments that suit the
    menu; anything longer is the command as given."""
    import shlex
    words = shlex.split(custom)
    if len(words) != 1 or words[0] not in LAUNCHERS:
        return words, False
    commands = {
        "fuzzel": (["fuzzel", "--dmenu", "--index", "--no-sort", "--width", "45", "--lines", str(count), "--line-height", "40",
                    "--prompt", "layout: ", "--select-index", str(selected)], True),
        "rofi": (["rofi", "-dmenu", "-i", "-no-custom", "-format", "i", "-show-icons", "-p", "layout", "-selected-row", str(selected)], True),
        "wofi": (["wofi", "--dmenu", "--insensitive", "--prompt", "layout"], False),
        "tofi": (["tofi", "--prompt-text", "layout: "], False),
        "bemenu": (["bemenu", "-i", "-l", str(count), "-p", "layout"], False),
        "wmenu": (["wmenu", "-i", "-l", str(count), "-p", "layout"], False),
        "dmenu": (["dmenu", "-i", "-l", str(count), "-p", "layout"], False),
    }
    return commands[words[0]]


def picked_layout(output, names):
    output = output.strip()
    if output.isdigit():
        return names[int(output)] if int(output) < len(names) else None
    name = output.split(" — ")[0].strip()
    return name if name in names else None


def notify(message):
    """Show `message` as a desktop notification, without waiting for it."""
    import shutil
    import subprocess
    def send():
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(["notify-send", "swaytiles", message], capture_output=True, timeout=5)
    if shutil.which("notify-send"):
        threading.Thread(target=send, daemon=True).start()


def warn(message):
    import shutil
    import subprocess
    print(f"swaytiles: {message}", file=sys.stderr)
    if shutil.which("notify-send"):
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(["notify-send", "swaytiles", message], capture_output=True, timeout=5)


def menu(sway, custom):
    import subprocess
    import tempfile
    state = load_state()
    workspace = sway.focused_workspace()
    names = list(LAYOUTS)
    chosen = state["workspaces"].get(workspace, state["layout"])
    command, icons = launcher(custom or "", len(names), names.index(chosen) if chosen in names else 0)
    if not command:
        warn(f"say which menu program to use: swaytiles menu --launcher NAME, with one of {', '.join(LAUNCHERS)} or a dmenu-style command")
        return 1
    entries = [f"{name} — {DESCRIPTIONS[name]}{'  ●' if name == chosen else ''}" for name in names]
    if icons:
        ICONS.mkdir(parents=True, exist_ok=True)
        # fuzzel's own default text colour, and for the others a grey that shows on light and on dark.
        colour = (fuzzel_text() or "#657b83") if command[0] == "fuzzel" else "#808080"
        entries = [f"{entry}\0icon\x1f{icon(name, (layout or flat('splith'))(list(range(5))), None if layout is None else 0, colour)}"
                   for entry, (name, layout) in zip(entries, LAYOUTS.items(), strict=True)]
    with tempfile.TemporaryFile("w+") as listing:
        listing.write("".join(entry + "\n" for entry in entries))
        listing.seek(0)
        try:
            output = subprocess.run(command, stdin=listing, capture_output=True, text=True).stdout
        except OSError as error:
            warn(f"cannot run {command[0]}: {error.strerror}")
            return 1
    choice = picked_layout(output, names)
    if choice:
        select(sway, choice)
    return 0


def config():
    import shutil
    found = shutil.which(sys.argv[0]) if Path(sys.argv[0]).name == "swaytiles" else None
    print(CONFIG.format(command=found or "swaytiles"), end="")
    return 0


def cli():
    arguments = sys.argv[1:]
    try:
        if set(arguments) <= {"--no-keys", "--wait"} and len(set(arguments)) == len(arguments):
            return (wait if "--wait" in arguments else main)(keys="--no-keys" not in arguments)
        if arguments[0] == "menu" and len(arguments) in (1, 3) and arguments[1:2] in ([], ["--launcher"]):
            return menu(Sway(), arguments[2] if len(arguments) == 3 else None)
        if arguments == ["config"]:
            return config()
        if arguments == ["swap"]:
            return act(Sway(), "master")
        if arguments[0] == "show" and (len(arguments) == 1 or (len(arguments) == 2 and arguments[1].isdigit())):
            return act(Sway(), " ".join(arguments))
        if arguments[0] == "move" and (arguments[1:] in ([name] for name in PARALLEL) or (arguments[1:2] == ["number"] and len(arguments) == 3)):
            return act(Sway(), " ".join(arguments))
        if len(arguments) == 1 and RENAMED.get(arguments[0], arguments[0]) in LAYOUTS:
            return select(Sway(), arguments[0])
        if len(arguments) == 2 and arguments[0] == "default" and RENAMED.get(arguments[1], arguments[1]) in LAYOUTS:
            return act(Sway(), f"default {RENAMED.get(arguments[1], arguments[1])}")
    except ConnectionError as error:
        print(f"swaytiles: {error}", file=sys.stderr)
        return 1
    usage = ("usage: swaytiles [--wait] [--no-keys] | "
             "swaytiles [menu --launcher COMMAND | swap | show [ID] | move DIRECTION | move number N | config | LAYOUT | default LAYOUT]")
    print(f"{usage}\nlayouts: {', '.join(LAYOUTS)}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(cli())

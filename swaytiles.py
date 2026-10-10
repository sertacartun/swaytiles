#!/usr/bin/env python3
import contextlib
import fcntl
import glob
import itertools
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
SIZED = "_layout_sized_"
# The marks that point the tile rule at the place of the next window.
PLACING = (AFTER, SIZED)
NEXT = 0
SYNC = "layout:sync"
ACT = "layout:act"
RENAMED = {"sway": "default"}
CASCADE = 40
# sway reports no event for a window dropped on a workspace's edge or swapped
# with the mouse; the daemon looks at the tree this often, for this long after
# the focus changes, as pressing on a window to drag it focuses it. A swap
# keeps the layout in a new order; any other drop lets the workspace go.
WATCH = (0.2, 4.0)
# How long a window that was asked to close may take before it is taken
# back into the layout: it asks first, or will not close.
CLOSING = 1.0
TABBED = ("tabbed", "stacked")
SPLITS = {"right": "splith", "down": "splitv", "left": "splith", "up": "splitv"}
EVENTS = {0: "workspace", 1: "output", 3: "window", 5: "binding", 6: "shutdown", 7: "tick"}
SUBSCRIPTIONS = ["window", "tick", "workspace", "binding", "output", "shutdown"]
FOCUSING = re.compile(r"focus (left|right|up|down)")
MOVING = re.compile(r"move (left|right|up|down)|move (?:container |window )?(?:to )?workspace (number \d+|[^\s\"']+)")
HIDING = re.compile(r"move (?:container |window )?(?:to )?scratchpad")
FLOATING = re.compile(r"floating (toggle|enable|disable)")
KILLING = re.compile(r"kill")
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
                print(f"swaytiles: sway rejected {joined!r}: {result.get('error')}", file=sys.stderr, flush=True)
        return results

    def tree(self):
        return self.request(self.TREE)

    def tick(self, payload):
        return self.request(self.TICK, payload)

    def focused_workspace(self):
        return next(ws["name"] for ws in self.request(self.WORKSPACES) if ws["focused"])

    def subscribe(self, names, pause=lambda: None):
        """The events, and an "idle" one each time no event comes for the
        seconds `pause` returns, when it returns any."""
        from select import select as readable
        connection = self.connect()
        self.send(connection, self.SUBSCRIBE, json.dumps(names))
        self.receive(connection)

        def stream():
            with connection:
                while True:
                    wait = pause()
                    if wait is not None and not readable([connection], [], [], wait)[0]:
                        yield "idle", {}
                        continue
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
    """`nop layout …` for a binding that is sway's own move, a move to the
    scratchpad, a floating switch or a kill, else None."""
    command = " ".join(command.split())
    if HIDING.fullmatch(command):
        return "nop layout hide"
    if KILLING.fullmatch(command):
        return "nop layout close"
    if floating := FLOATING.fullmatch(command):
        return f"nop layout float {floating[1]}"
    match = MOVING.fullmatch(command)
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
    return plain(trimmed(node))


def layout_command(layout):
    return f"layout {'stacking' if layout == 'stacked' else layout}"


def ancestors(node, con):
    if node["id"] == con:
        return [node]
    return next(([node, *path] for child in node["nodes"] if (path := ancestors(child, con))), [])


def shape(node):
    return node["id"] if not node["nodes"] else (node["layout"], [shape(child) for child in node["nodes"]])


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


def span(node, axis):
    """The node's width or height, its title bar included."""
    return node["rect"][axis] + (node["deco_rect"]["height"] if axis == "height" else 0)


def flanks(workspace, master):
    """The two sides of a master that stands in the middle of three, as in
    centered, or None."""
    node = top(workspace)
    children = node["nodes"]
    if node["layout"] not in ("splith", "splitv") or len(children) != 3 or children[1]["id"] != master:
        return None
    return children[0], children[2]


def gap(workspace, axis):
    """The gap sway leaves between the windows at the top of the workspace."""
    node = top(workspace)
    children = node["nodes"]
    if len(children) < 2 or family(node["layout"]) != ("h" if axis == "width" else "v"):
        return 0
    return max(0, (node["rect"][axis] - sum(span(child, axis) for child in children)) // (len(children) - 1))


def sides_room(workspace, axis, share, lean):
    """The room in px of the two sides of a master in the middle that takes
    `share` ppt of the workspace, the first side taking `lean` of theirs."""
    room = workspace["rect"][axis]
    rest = room - 2 * gap(workspace, axis) - room * share // 100
    return round(rest * lean), rest - round(rest * lean)


def balancing(workspace, master, sides, axis, share, lean=0.5):
    """The commands that give a master in the middle `share` ppt of the
    workspace and its two `sides` their room, the first `lean` of it, whatever
    room they have now. sway spreads each resize over every sibling alike, and
    refuses one that would leave a sibling too small, so each round sets both
    sides and then the master: that cuts the error to an eighth, and a side
    too small to give room is first given some. After five rounds less than
    a pixel is left."""
    rooms = sides_room(workspace, axis, share, lean)
    return [*(f"[con_id={side}] resize set {axis} {size} px" for side, size in zip(sides, rooms, strict=True)),
            f"[con_id={master}] resize set {axis} {share} ppt"] * 5


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


def focused_window(tree):
    """The focused node and the workspace it is a window of, if any."""
    node = focused_node(tree)
    return node, next((ws for ws in workspaces(tree) if node in windows(ws)), None)


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


def build(node):
    """The commands that make the container `node` around its first window,
    which stands where the container goes, with the others right after it."""
    if isinstance(node, int):
        return []
    layout, children = node
    first = leaves(node)[0]
    commands = [f"[con_id={first}] split h", f"[con_id={first}] {layout_command(layout)}",
                *after(first, *(leaves(child)[0] for child in reversed(children[1:])))]
    return commands + [command for child in children for command in build(child)]


def family(layout):
    return "h" if layout in ("splith", "tabbed") else "v" if layout in ("splitv", "stacked") else None


def turns(current, wanted):
    """The moves of a window right under the workspace that turn the
    workspace's own layout from `current` into the split `wanted`. Each move
    is across the workspace, so sway puts the window first and sets the
    workspace to the move's orientation (workspace_rejigger), without
    changing the focus or leaving the output."""
    if current == wanted:
        return []
    across = "up" if family(current) == "h" else "left"
    if family(wanted) != family(current):
        return [across]
    return [across, "left" if family(wanted) == "h" else "up"]


def wrapped(workspace):
    """Whether the workspace holds its windows in a split container of its own,
    which only takes room in the tree: the layout is the same without it."""
    nodes = workspace["nodes"]
    return len(nodes) == 1 and bool(nodes[0]["nodes"]) and nodes[0]["layout"] not in TABBED


def assemble(workspace, target, focused=None):
    """The commands that build `target` right under the workspace, in one
    transaction and without moving the focus. A layout command on a window
    right under the workspace wraps the workspace's windows in a new
    container, so the workspace's own layout is turned by moves instead, and
    a tabbed or stacked layout is one container. Windows are gathered with
    `move to mark`, which leaves the containers they were in empty, and sway
    removes them."""
    if covered(workspace):
        return None
    ids = leaves(target)
    first, rest = ids[0], ids[1:]
    layout = workspace["layout"]
    root = (layout if layout not in TABBED else "splith") if isinstance(target, int) else target[0]
    direct = [node["id"] for node in workspace["nodes"] if not node["nodes"]]
    commands = []
    if direct and first not in direct:
        # The first window goes right under the workspace.
        commands += after(direct[0], first)
    elif not direct:
        # Every window is in a container: gather them in the first one and
        # take the first window out of it, in front of it.
        holder = workspace["nodes"][0]
        commands += after(holder["id"], *ids)
        if not rest:
            # `split none` also arranges the workspace; `layout` would leave the
            # window with the geometry of the container it was in.
            commands.append(f"[con_id={first}] split none")
        else:
            commands.append(f"[con_id={first}] move {'left' if family(holder['layout']) == 'h' else 'up'}")
            if family(layout) != family(holder["layout"]):
                layout = "splith" if family(holder["layout"]) == "h" else "splitv"
    wanted = root if root not in TABBED else layout if layout not in TABBED else "splith"
    commands += [f"[con_id={first}] move {direction}" for direction in turns(layout, wanted)]
    if rest:
        commands += after(first, *reversed(rest))
    if root in TABBED:
        commands.append(f"[con_id={first}] {layout_command(root)}")
    if not isinstance(target, int):
        commands += [command for child in target[1] for command in build(child)]
    return commands + ([refocus(focused)] if focused in ids else [])


def after(anchor, *cons):
    """Put `cons` right after `anchor`, the last one first after it."""
    return [f"[con_id={anchor}] mark --add {MARK}", *(f"[con_id={con}] move to mark {MARK}" for con in cons), f"[con_id={anchor}] unmark {MARK}"]


def refocus(con):
    """Focus `con` again, unless the user has gone to another workspace since the tree was read."""
    return f"[con_id={con} workspace=__focused__] focus"


def settling(workspace, target, con, anchor):
    """The commands that take `con`, put just after `anchor` on the workspace,
    to its place in `target`."""
    model = copied(workspace)
    put_after(model, anchor, con)
    return assemble(model, target, con) or []


# A model of the workspace's tree in sway's own form, to work out what
# sway's commands will make of it before sending them. Containers made in
# the model get ids of their own below zero.
FRESH = itertools.count(-1, -1)


def copied(node):
    return {**node, "nodes": [copied(child) for child in node["nodes"]]}


def position(parent, node):
    return [child["id"] for child in parent["nodes"]].index(node["id"])


def container(layout, nodes):
    return {"id": next(FRESH), "type": "con", "layout": layout, "nodes": nodes, "floating_nodes": []}


def put_after(root, anchor, con):
    """`move to mark` of a new window, `con`, onto `anchor`."""
    parent = ancestors(root, anchor)[-2]
    parent["nodes"].insert(position(parent, {"id": anchor}) + 1, {"id": con, "type": "con", "layout": "none", "nodes": [], "floating_nodes": []})


def take_out(root, con, path=None):
    """`con` leaves, from the place `path` leads to: a container left empty
    goes, one left with a single child stays."""
    path = path or ancestors(root, con)
    for parent, child in zip(reversed(path[:-1]), reversed(path[1:]), strict=True):
        parent["nodes"][:] = [node for node in parent["nodes"] if node is not child]
        if parent["nodes"] or parent is root:
            break


def enclose(root, con, layout):
    """`split h, layout <layout>` on the window `con`."""
    parent = ancestors(root, con)[-2]
    index = position(parent, {"id": con})
    parent["nodes"][index] = container(layout, [parent["nodes"][index]])


def move(root, con, direction):
    """sway's `move <direction>` of the window `con`, as
    container_move_in_direction in sway/commands/move.c does it: "swap"
    past a window beside it, which keeps both sizes, "out" of its
    container to the side, or around the workspace's other windows when the
    move goes across it (workspace_rejigger, which keeps a single child
    wrapped), "cross" to the next output, or "into" a container beside it,
    which the model does not follow."""
    offset = -1 if direction in ("left", "up") else 1
    path = ancestors(root, con)
    window, level, wrapped = path[-1], len(path) - 2, False
    current = window
    while True:
        parent = path[level]
        if family(parent["layout"]) != family(SPLITS[direction]):
            if parent is root:
                wrapper = container(root["layout"], root["nodes"])
                root["nodes"], root["layout"] = [wrapper], SPLITS[direction]
                path.insert(1, wrapper)
                current, level, wrapped = wrapper, 0, True
            else:
                current, level = parent, level - 1
            continue
        index = position(parent, current)
        beside = parent["nodes"][index + offset] if 0 <= index + offset < len(parent["nodes"]) else None
        if current is not window:
            break
        if beside is not None and not beside["nodes"]:
            parent["nodes"][index], parent["nodes"][index + offset] = beside, window
            return "swap"
        if beside is not None:
            return "into"
        if parent is root:
            return "cross"
        current, level = parent, level - 1
    if beside is not None:
        return "into"
    old = path[-2]
    if not wrapped and path[-3] is root and len(old["nodes"]) == 1:
        # A window alone in a container right under the workspace.
        return "cross"
    # sway puts the window in its new place before it reaps its old one.
    parent["nodes"].insert(position(parent, current) + (offset > 0), window)
    take_out(root, con, path)
    squash(root)
    return "out"


def squash(node):
    """sway's flattening of a split that holds nothing but a split across
    it, inside a split going the inner one's way. sway puts the inner
    windows in one by one at the same place, so they end up reversed."""
    index = 0
    while index < len(node["nodes"]):
        child = node["nodes"][index]
        inner = child["nodes"][0] if len(child["nodes"]) == 1 else None
        if (inner is not None and {child["layout"], inner["layout"]} <= {"splith", "splitv"}
                and family(child["layout"]) != family(inner["layout"]) == family(node["layout"])):
            node["nodes"][index:index + 1] = inner["nodes"][::-1]
            index += len(inner["nodes"])
        else:
            squash(child)
            index += 1


def swap(root, first, second):
    """`swap container` of two nodes: each takes the other's place, and with
    it the size sway keeps for that place."""
    one, other = ancestors(root, first), ancestors(root, second)
    at, to = position(one[-2], one[-1]), position(other[-2], other[-1])
    one[-2]["nodes"][at], other[-2]["nodes"][to] = other[-1], one[-1]


def reconcile(workspace, target):
    """The commands that make the model `workspace` into `target` and keep
    its containers, and so the sizes sway keeps for them: a container left
    holding a single container gives way to it, containers turn to the
    layouts of the target, siblings change places, windows go over to a
    sibling container that has too few, a lone window takes a container of
    its own, and the windows are swapped into their places. None where it
    takes more than that, such as a command on a container made just now
    or the workspace's own layout to turn."""
    if isinstance(target, int) or target[0] in TABBED or workspace["layout"] != target[0]:
        return None
    commands = []
    if len(workspace["nodes"]) == 1 and workspace["nodes"][0]["nodes"]:
        # The master went and left the rest in the stack's container: a window
        # moves out of it to the master's side.
        side = next((index for index in (0, -1) if isinstance(target[1][index], int)), None)
        if side is None:
            return None
        con = leaves(shape(workspace))[side]
        way = ("left" if side == 0 else "right") if family(target[0]) == "h" else ("up" if side == 0 else "down")
        if move(workspace, con, way) != "out":
            return None
        commands.append(f"[con_id={con}] move {way}")

    lost = []

    def unwrap(parent, index):
        child = parent["nodes"][index]["nodes"][0]
        commands.append(f"[con_id={child['id']}] split none")
        parent["nodes"][index] = child
        if len(parent["nodes"]) == 1 and parent is not workspace:
            # sway goes on up the tree while a container holds one child
            # (container_flatten), so the one it is in goes too and is made
            # again around the child, with its size and a new id. One more
            # going could not be made again: `split` on an only child turns
            # its container.
            above = ancestors(workspace, parent["id"])[-2]
            if parent["layout"] in TABBED or (above is not workspace and len(above["nodes"]) == 1):
                lost.append(parent)
            commands.append(f"[con_id={child['id']}] split {family(parent['layout'])}")
            parent["id"] = next(FRESH)
        return child

    def align(parent, index, want):
        node = parent["nodes"][index]
        if isinstance(want, int):
            while len(node["nodes"]) == 1:
                node = unwrap(parent, index)
            return not node["nodes"]
        if not node["nodes"]:
            return False
        while len(node["nodes"]) == 1 and node["nodes"][0]["nodes"] and len(want[1]) > 1:
            node = unwrap(parent, index)
        if node["layout"] != want[0]:
            if node["layout"] in TABBED or want[0] in TABBED:
                return False
            # `layout` on a child turns the container it is in; on the only
            # child it would turn the one around that, where `split` turns it.
            turn = f"split {family(want[0])}" if len(node["nodes"]) == 1 else layout_command(want[0])
            commands.append(f"[con_id={node['nodes'][0]['id']}] {turn}")
            node["layout"] = want[0]
        return arrange(node, want[1])

    def arrange(node, wanted):
        nodes = node["nodes"]
        # Columns and rows of windows hand windows over to those with too few,
        # and those left over give theirs away, so sway lets them go.
        lines = [child for child in nodes if child["nodes"] and all(not leaf["nodes"] for leaf in child["nodes"])]
        if len(nodes) > len(wanted) and len(lines) == len(nodes) and all(
                not isinstance(want, int) and all(isinstance(leaf, int) for leaf in want[1]) for want in wanted):
            short = [len(want[1]) for want in wanted] + [0] * (len(nodes) - len(wanted))
            short = [need - len(child["nodes"]) for need, child in zip(short, nodes, strict=True)]
            for index in range(len(nodes)):
                while short[index] > 0:
                    giver = next((other for other in range(len(nodes)) if short[other] < 0), None)
                    if giver is None:
                        return False
                    leaf, anchor = nodes[giver]["nodes"].pop(), nodes[index]["nodes"][-1]
                    commands.extend(after(anchor["id"], leaf["id"]))
                    nodes[index]["nodes"].append(leaf)
                    short[index], short[giver] = short[index] - 1, short[giver] + 1
            nodes[:] = [child for child in nodes if child["nodes"]]
        if len(nodes) != len(wanted):
            return False
        for index, want in enumerate(wanted):
            if (not nodes[index]["nodes"]) == isinstance(want, int):
                continue
            # Siblings change places where a window stands for a container,
            other = next((later for later in range(index + 1, len(nodes))
                          if (not nodes[later]["nodes"]) == isinstance(want, int)), None)
            if other is not None:
                commands.append(f"[con_id={nodes[index]['id']}] swap container with con_id {nodes[other]['id']}")
                nodes[index], nodes[other] = nodes[other], nodes[index]
            elif isinstance(want, int) and len(nodes[index]["nodes"]) == 1 and not nodes[index]["nodes"][0]["nodes"]:
                # a window alone in a container gives it up where a window goes,
                unwrap(node, index)
            elif not isinstance(want, int) and len(want[1]) == 1 and want[0] not in TABBED:
                # and a window takes one where a container of it alone goes:
                # sway makes it the window's size.
                commands.append(f"[con_id={nodes[index]['id']}] split {family(want[0])}")
                nodes[index] = container(want[0], [nodes[index]])
            else:
                return False
        # Columns and rows of windows hand windows over to those with too few.
        flat = [index for index, want in enumerate(wanted) if not isinstance(want, int) and all(isinstance(leaf, int) for leaf in want[1])
                and all(not child["nodes"] for child in nodes[index]["nodes"])]
        short = {index: len(wanted[index][1]) - len(nodes[index]["nodes"]) for index in flat}
        for index in flat:
            while short[index] > 0:
                giver = next((other for other in flat if short[other] < 0), None)
                if giver is None:
                    return False
                leaf, anchor = nodes[giver]["nodes"][-1], nodes[index]["nodes"][-1]
                commands.extend(after(anchor["id"], leaf["id"]))
                nodes[giver]["nodes"].pop()
                nodes[index]["nodes"].append(leaf)
                short[index], short[giver] = short[index] - 1, short[giver] + 1
        return all(align(node, index, want) for index, want in enumerate(wanted))

    if not arrange(workspace, target[1]) or lost:
        return None
    now, want = leaves(shape(workspace)), leaves(target)
    if sorted(now) != sorted(want):
        return None
    for index, con in enumerate(want):
        if now[index] != con:
            other = now[index]
            commands.append(f"[con_id={con}] swap container with con_id {other}")
            swap(workspace, con, other)
            now[now.index(con)], now[index] = other, con
    # A container made just now has no id to name it by.
    named = all(not re.search(r"con_id=-", command) for command in commands)
    return commands if named and trimmed(shape(workspace)) == target and not wrapped(workspace) else None


def foresee(workspace, target):
    """How the tile rule should place the next window, NEXT in `target`, so
    that sway draws it in its place from the first frame: the window it goes
    right after, the direction of a move that turns the workspace with it
    and the layout of a container it nests in, if any. Each plan is tried
    on the model of the workspace, the simplest first; None if none gives
    `target`."""
    if covered(workspace) or not workspace["nodes"]:
        return None
    parent = parent_of(target, NEXT)
    nests = [None, parent[0]] if parent is not None and len(parent[1]) == 1 else [None]
    present = tiled(workspace)
    for way in (None, *SPLITS):
        for nest in nests:
            for after in present:
                model = copied(workspace)
                put_after(model, after, NEXT)
                if way and move(model, NEXT, way) not in ("swap", "out"):
                    continue
                if nest:
                    enclose(model, NEXT, nest)
                built = shape(model)
                if (built == target if not isinstance(target, int) and target[0] not in TABBED else built[1] == [target]):
                    return after, way, nest
    return None


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


def presses(workspace, con, direction):
    """How many of sway's own `move <direction>` take `con` to the next
    output, or None if one would leave it on the workspace. A window at
    the edge of a container first leaves the container, which shows nothing
    when the container only wraps the workspace's windows."""
    model = copied(workspace)
    for count in range(1, len(ancestors(workspace, con)) + 2):
        outcome = move(model, con, direction)
        if outcome != "out":
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


def path(node, leaf):
    if node == leaf:
        return []
    if isinstance(node, int):
        return None
    return next(([index, *rest] for index, child in enumerate(node[1]) if (rest := path(child, leaf)) is not None), None)


def last_focused(node):
    while node["nodes"]:
        node = next((child for id in node["focus"] for child in node["nodes"] if child["id"] == id), node["nodes"][0])
    return node["id"]


def heir(ids, con):
    """The window that comes in the place of `con` in the order `ids` as it
    goes: the next one, or the one before at the end."""
    rest = [other for other in ids if other != con]
    return rest[min(ids.index(con), len(rest) - 1)] if con in ids and rest else None


def areas(node, master=None, share=None, x=0.0, y=0.0, width=1.0, height=1.0):
    """Where the layout `node` puts each window, as parts of the workspace:
    splits shared evenly, but for a master that takes `share` of its own."""
    if isinstance(node, int):
        return {node: (x, y, width, height)}
    layout, children = node
    if layout in TABBED:
        return {leaf: box for child in children for leaf, box in areas(child, master, share, x, y, width, height).items()}
    across = layout == "splith"
    room = width if across else height
    sizes = [room / len(children)] * len(children)
    if share is not None and master in children and len(children) > 1:
        sizes = [room * share if child == master else room * (1 - share) / (len(children) - 1) for child in children]
    found, offset = {}, 0.0
    for child, size in zip(children, sizes, strict=True):
        found.update(areas(child, master, share, *((x + offset, y, size, height) if across else (x, y + offset, width, size))))
        offset += size
    return found


def overlap(first, second):
    width = min(first[0] + first[2], second[0] + second[2]) - max(first[0], second[0])
    height = min(first[1] + first[3], second[1] + second[3]) - max(first[1], second[1])
    return max(0.0, width) * max(0.0, height)


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
        self.heir = None
        self.gone = {}
        self.names = {}
        self.pending = {}
        self.areas = {}
        self.tiled = set()
        self.ratios = {}
        self.leans = {}
        self.gaps = {}
        self.sized = {}
        self.roles = {}
        self.resizing = {}
        self.foreseen = {}
        self.watching = 0.0
        self.carried = (None, None)
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

    def tiling(self, name):
        return None if self.chosen(name) == "float" else LAYOUTS.get(self.chosen(name))

    def ordered(self, cons):
        cons = set(cons)
        return [con for con in self.order if con in cons]

    def target(self, name, ids):
        return trimmed(self.tiling(name)(ids)) if ids else None

    def inspect(self, workspace, ids, arrived):
        name, seen = workspace["name"], self.built.get(workspace["name"])
        if trimmed(shape(workspace)) == self.target(name, ids):
            # As the layout has it, the moves of the tile rule included:
            # nothing was changed by hand.
            return
        if seen is not None:
            had = set(leaves(seen))
            present = without(shape(workspace), *(con for con in ids if con not in had))
            before = without(seen, *had.difference(ids))
            if outline(before) != outline(present):
                return self.adapt(workspace, present, bare(trimmed(before)) == bare(trimmed(present)))
        if arrived in ids and (found := conforming(self.tiling(name), shape(workspace))):
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

    def measure(self, workspace, ids, new=None, exact=False):
        """Read the master's share of the workspace. A window sway has just put
        next to the master took its room from all of them alike, so the share
        is read from the room the others have."""
        node = top(workspace)
        others = [child for child in node["nodes"] if tiled(child) != [new]]
        master = next((child for child in others if child["id"] == ids[0]), None) if ids else None
        if node["layout"] in ("splith", "splitv") and len(others) > 1 and master is not None:
            axis = "width" if node["layout"] == "splith" else "height"
            room = node["rect"][axis] - sum(span(child, axis) for child in node["nodes"] if tiled(child) == [new])
            self.share(workspace["name"], node["layout"], master, room, len(others), exact)
            # Kept for when a single window is left, where no gap shows.
            self.gaps[workspace["name"]] = gap(workspace, axis)
            if len(others) == 3 and others[1] is master:
                first, last = (span(side, axis) for side in (others[0], others[2]))
                self.tilt(workspace["name"], first / (first + last), exact)

    def share(self, name, layout, master, room, count, exact=False):
        """Keep the master's share. A share read in passing may be a step off,
        so it takes a change of two to replace the saved one; one read `exact`
        is taken as it is."""
        axis = "width" if layout == "splith" else "height"
        ratio, saved = round(span(master, axis) / room, 2), self.ratios.get(name)
        if saved is not None and abs(ratio - saved) < (0.01 if exact else 0.02):
            # Still the saved share, though it may be an even split now.
            return
        if abs(ratio - 1 / count) < 0.02:
            self.ratios.pop(name, None)
        else:
            self.ratios[name] = ratio

    def tilt(self, name, lean, exact=False):
        """Keep how the two sides of a master in the middle share their room,
        as the mouse left them dragging one edge of the master."""
        lean = round(lean, 2)
        if abs(lean - self.leans.get(name, 0.5)) < (0.01 if exact else 0.02):
            return
        if abs(lean - 0.5) < 0.02:
            self.leans.pop(name, None)
        else:
            self.leans[name] = lean

    def measure_gone(self, workspace, ids, gone):
        """Read the master's share as it was before the window `gone` beside it
        closed: sway gave that window's room to the others in proportion,
        and the window that closed was not the daemon's to read before."""
        node, seen = top(workspace), self.built.get(workspace["name"])
        master = next((child for child in node["nodes"] if child["id"] == ids[0]), None)
        # The master stood beside the window that closed, at the same level.
        if node["layout"] not in ("splith", "splitv") or master is None or isinstance(seen, int) or ids[0] not in seen[1]:
            return
        axis = "width" if node["layout"] == "splith" else "height"
        after = sum(span(child, axis) for child in node["nodes"])
        before = after - (gap(workspace, axis) if len(node["nodes"]) > 1 else self.gaps.get(workspace["name"], 0))
        was = {"rect": {axis: span(master, axis) / after * (before - span(gone, axis))}, "deco_rect": {"height": 0}}
        self.share(workspace["name"], node["layout"], was, node["rect"][axis], len(node["nodes"]) + 1, exact=True)

    def farewell(self, node):
        name = self.where.get(node["id"])
        built = self.built.get(name)
        if isinstance(built, int) or built is None or built[0] not in ("splith", "splitv") or len(built[1]) < 2:
            return
        master = next((con for con in self.order if con in leaves(built)), None)
        workspace = next((ws for ws in workspaces(self.sway.tree()) if ws["name"] == name), None)
        if master == node["id"] and master in built[1] and workspace is not None:
            self.share(name, built[0], node, workspace["rect"]["width" if built[0] == "splith" else "height"], len(built[1]))

    def measure_all(self, tree, exact=False):
        for workspace in workspaces(tree):
            name = workspace["name"]
            if self.tiling(name) is None or covered(workspace):
                continue
            ids = self.ordered(tiled(workspace))
            if ids and trimmed(shape(workspace)) == self.target(name, ids):
                self.measure(workspace, ids, exact=exact)

    def remeasure(self, tree):
        """Read the masters' sizes again, where no event says they changed,
        and point the tile rules at a size that did, so the next window
        does not bring back the old one."""
        before = dict(self.ratios), dict(self.leans)
        self.measure_all(tree)
        if (self.ratios, self.leans) != before:
            self.sway.command(*self.anchors(tree))

    def sizing(self, name, target, ids):
        """The axis and the share in ppt the master `ids[0]` takes in `target`, if it has a saved one."""
        ratio = self.ratios.get(name)
        if ratio is None or isinstance(target, int) or target[0] not in ("splith", "splitv") or ids[0] not in target[1]:
            return None
        return "width" if target[0] == "splith" else "height", round(ratio * 100)

    def resize(self, name, target, ids, workspace=None, settled=False):
        """The commands that give the master its saved size, and the sides of
        a master in the middle the room they had: from the room they have
        where `workspace` is `settled`, as sway has it now, else whatever room
        the moves before have left them."""
        size = self.sizing(name, target, ids)
        if not size:
            return []
        command = f"[con_id={ids[0]}] resize set {size[0]} {size[1]} ppt"
        if workspace is None:
            return [command]
        lean = self.leans.get(name, 0.5)
        if not settled:
            middle = len(target[1]) == 3 and target[1][1] == ids[0]
            return balancing(workspace, ids[0], (leaves(target[1][0])[0], leaves(target[1][2])[0]), *size, lean) if middle else [command]
        if (sides := flanks(workspace, ids[0])) is not None:
            master = top(workspace)["nodes"][1]
            wanted = (*sides_room(workspace, size[0], size[1], lean), workspace["rect"][size[0]] * size[1] // 100)
            # Shares are kept to the percent: closer than that, the room is
            # as the user left it.
            close = max(2, workspace["rect"][size[0]] // 100)
            if all(abs(span(node, size[0]) - room) <= close for node, room in zip((*sides, master), wanted, strict=True)):
                return []
            return balancing(workspace, ids[0], tuple(tiled(side)[0] for side in sides), *size, lean)
        node = top(workspace)
        master = next((child for child in node["nodes"] if child["id"] == ids[0]), None)
        if master is not None and abs(span(master, size[0]) / node["rect"][size[0]] - size[1] / 100) < 0.01:
            # Already its size: a message would only make sway go round again.
            return []
        return [command]

    def taker(self, name, ids, con, rect, room):
        """The window that takes the place of `con`, at `rect` on a workspace
        at `room`, as it goes: the one the layout without it puts most over
        where it was, which is under the mouse that focused `con`. In
        centered the next window in the order is in the other column."""
        rest = [other for other in ids if other != con]
        if con not in ids or not rest:
            return None
        target = self.target(name, rest)
        size = self.sizing(name, target, rest)
        places = areas(target, rest[0], size[1] / 100 if size else None)
        was = ((rect["x"] - room["x"]) / room["width"], (rect["y"] - room["y"]) / room["height"],
               rect["width"] / room["width"], rect["height"] / room["height"])
        nearest = heir(ids, con)
        return max(rest, key=lambda other: (round(overlap(places.get(other, (0, 0, 0, 0)), was), 4), other == nearest))

    def entry(self, name, ws, coming, master, axis, share):
        """The size in px the next window takes first as it comes in at the side
        of a master in the middle, as the third window of centered does: it
        gets a third of the room and the others give up theirs in proportion,
        and the master's own resize keeps the difference between the sides,
        so this one sets that difference to what their saved lean wants."""
        node = top(ws)
        children = coming[1]
        if len(children) != 3 or children[1] != master or [NEXT] not in (leaves(children[0]), leaves(children[2])) or len(node["nodes"]) != 2:
            return 0
        present = sum(span(child, axis) for child in node["nodes"])
        other = next((child for child in node["nodes"] if child["id"] != master), None)
        if other is None or present <= 0:
            return 0
        # One gap more between three than between two.
        room = 2 * present - node["rect"][axis]
        if room <= 0:
            return 0
        lean = self.leans.get(name, 0.5)
        lean = lean if leaves(children[0]) == [NEXT] else 1 - lean
        # In parts of the room: the new window has a third, the other side two
        # thirds of what it had; the master ends at `taken`.
        other, taken = 2 / 3 * span(other, axis) / present, node["rect"][axis] * share // 100 / room
        return round(((2 * lean - 1) * (1 - taken) + other + 1 / 6) / 1.5 * room)

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
        code = encoded(name)
        variable = f"$layout_tile_{code}"
        commands = [] if self.anchored else ["set $layout_gate _layout_off", 'for_window [all] "mark --add $$layout_gate; unmark $$layout_gate"',
                                             "[all] mark --add _layout_arm", "unmark _layout_arm", "set $layout_gate _layout_fresh"]
        commands.append(f"set {variable} {AFTER + code if active else '_layout_off'}")
        role, way, inner = f"$layout_role_{code}", f"$layout_way_{code}", f"$layout_inner_{code}"
        if not active:
            commands.append(f"set {role} _layout_none_{code}")
            self.roles.pop(name, None)
        if name not in self.anchored:
            # The window goes right after the anchor, turns the workspace with
            # a move or nests in a container, as `foresee` planned, so it is
            # drawn in its place from the first frame. Each step is used once:
            # sway keeps the rules of every daemon that ran since it last read
            # its config.
            axis, share = f"$layout_axis_{code}", f"$layout_share_{code}"
            entry = f"$layout_entry_{code}"
            commands += [f"set {axis} width", f"set {share} 50", f"set {entry} 0", f"set {role} _layout_none_{code}", f"set {way} right", f"set {inner} splith"]
            fresh = "[con_mark=^_layout_fresh$]"
            action = (f"[con_mark=^{AFTER + code}$ workspace={elsewhere(name)}] unmark {AFTER + code}",
                      f"{fresh} move container to mark ${variable}", f"{fresh} mark --add ${variable}",
                      f"{fresh} mark --add ${role}",
                      f"[con_mark=^_layout_(turn|both)_{code}$] move ${way}",
                      f"[con_mark=^_layout_(nest|both)_{code}$] split h, layout ${inner}",
                      f"{fresh} unmark ${role}", f"{fresh} set ${role} _layout_none_{code}",
                      # A lone master marked SIZED takes its saved share as the
                      # window comes in next to it, so sway never draws the two halves;
                      # a window coming in at the side of a master in the middle
                      # first takes the size that leaves the sides at their saved
                      # lean (`entry`; 0 leaves it as it is).
                      f"{fresh} resize set ${axis} ${entry} px",
                      f"[con_mark=^{SIZED + code}$] resize set ${axis} ${share} ppt",
                      "[con_mark=^_layout_off$] unmark _layout_off")
            commands.append(f'for_window [workspace="^{criteria(name)}$" tiling] "{"; ".join(action)}"')
        self.anchored[name] = active
        return commands

    def anchors(self, tree):
        """Point the tile rule of every workspace at the place of its next window."""
        held = marked(tree, PLACING)
        wanted, commands = {}, []
        for ws in workspaces(tree):
            name = ws["name"]
            active = self.chosen(name) not in ("default", "float")
            commands += self.tile_rule(name, active)
            ids = self.ordered(tiled(ws))
            self.resizing[name] = False
            if active and ids:
                commands += self.prepare(ws, name, ids, wanted)
        commands += [f"unmark {mark}" for mark in held if mark not in wanted]
        return commands + [f"[con_id={con}] mark --add {mark}" for mark, con in wanted.items() if held.get(mark) != con]

    def prepare(self, ws, name, ids, wanted):
        """The commands that set the tile rule of the workspace for its next
        window, and the marks it needs in `wanted`."""
        code, now, coming = encoded(name), self.target(name, ids), self.target(name, [*ids, NEXT])
        commands = []
        if len(ids) == 1 and ws["nodes"] and ws["nodes"][0]["id"] == ids[0] and coming[0] not in TABBED and ws["layout"] != coming[0]:
            # A lone window takes the orientation the workspace has with two,
            # which shows nothing, so the next one comes in at its place.
            commands += [f"[con_id={ids[0]}] move {direction}" for direction in turns(ws["layout"], coming[0])]
            ws = {**ws, "layout": coming[0]}
        anchor, way, nest = (self.plan(ws, name, coming) if not covered(ws) else None) or (ids[-1], None, None)
        wanted[AFTER + code] = anchor
        role = "both" if way and nest else "turn" if way else "nest" if nest else "none"
        values = (f"_layout_{role}_{code}", way or "right", layout_command(nest)[7:] if nest else "splith")
        if self.roles.get(name) != values:
            commands += [f"set ${key}_{code} {value}" for key, value in zip(("layout_role", "layout_way", "layout_inner"), values, strict=True)]
            self.roles[name] = values
        # A window coming in at the master's level takes room from it: the
        # rule gives the master its saved size at once.
        level = 1 if isinstance(now, int) or now[0] in TABBED else len(now[1])
        widening = not isinstance(coming, int) and len(coming[1]) > level
        size = self.sizing(name, coming, [*ids, NEXT]) if widening else None
        self.resizing[name] = bool(size)
        entry = self.entry(name, ws, coming, ids[0], *size) if size else 0
        if size and self.sized.get(name, (None, None))[0] != size:
            commands += [f"set $layout_axis_{code} {size[0]}", f"set $layout_share_{code} {size[1]}"]
        if entry != self.sized.get(name, (None, 0))[1]:
            commands.append(f"set $layout_entry_{code} {entry}")
        self.sized[name] = (size if size else self.sized.get(name, (None, 0))[0], entry)
        if size:
            wanted[SIZED + code] = ids[0]
        return commands

    def plan(self, workspace, name, target):
        """`foresee` for the next window of the workspace, worked out again only when the workspace changes."""
        key = (workspace["layout"], shape(workspace), target)
        if self.foreseen.get(name, (None,))[0] != key:
            self.foreseen[name] = (key, foresee(workspace, target))
        return self.foreseen[name][1]

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

    def cascading(self, workspace):
        """The place in the cascade of a window coming to a float workspace,
        and the commands that point its float rule at the place after it."""
        name, area = workspace["name"], workspace["rect"]
        taken = {origin(node) for node in workspace["floating_nodes"]}
        slot = free_slot(area, taken, self.slots.get(name, 0))
        geometry = cascade(area, slot)
        self.slots[name] = free_slot(area, taken | {geometry[2:]}, slot + 1)
        return geometry, self.float_rule(name, cascade(area, self.slots[name]))

    def placed(self, tree, new, moved):
        where = {node["id"]: ws["name"] for ws in workspaces(tree) for node in windows(ws)}
        local = moved and new in where and self.where.get(new) == where[new]
        (carried, name), self.carried = self.carried, (None, None)
        # Put in its place already by `move_to`.
        landed = new == carried and where.get(carried) == name and self.where.get(carried) != name
        self.where = where
        self.names = {ws["id"]: ws["name"] for ws in workspaces(tree)}
        if local and self.syncing is not None:
            return True, None
        if not moved or local or landed:
            return False, None
        if new in self.order:
            self.order.remove(new)
            self.order.append(new)
        return False, new

    def release(self, tree, arrived):
        commands = []
        for ws in workspaces(tree):
            for node in ws["floating_nodes"]:
                mark = f"{FLOATED}{node['id']}"
                if mark in node["marks"] and self.chosen(ws["name"]) != "float":
                    commands.append(f"[con_id={node['id']}] unmark {mark}, floating disable, opacity 1")
                elif mark in node["marks"] and node["id"] == arrived:
                    commands.append(f"[con_id={node['id']}] unmark {mark}")
        self.sway.command(*commands)
        return self.sway.tree() if commands else tree

    def shape_up(self, workspace, ids, new, focused):
        name = workspace["name"]
        target = self.target(name, ids)
        if target is None:
            return False
        # The master's size goes in the same message as the layout, so sway
        # draws them as one.
        if target == trimmed(shape(workspace)) and not wrapped(workspace):
            # In place already, by the tile rule: a new window may still have
            # taken room from the master, and one that closed beside it left
            # its room to the master.
            sized = self.resize(name, target, ids, workspace, settled=True) if new in ids or self.regrouped(workspace) else []
            self.sway.command(*sized)
            return bool(sized)
        model = copied(workspace)
        steps = reconcile(model, target)
        if steps is not None:
            # The containers stay, and the sizes sway keeps for them: only the
            # master's own share is set again, where the places beside it changed.
            moved = {child["id"] for child in top(model)["nodes"]} != {child["id"] for child in top(workspace)["nodes"]}
            sized = self.resize(name, target, ids, workspace) if moved or self.regrouped(workspace) else []
            self.sway.command(*steps, *([refocus(focused)] if focused in ids and steps else []), *sized)
            return bool(steps or sized)
        self.sway.command(*(assemble(workspace, target, focused) or []), *self.resize(name, target, ids, workspace))
        return True

    def regrouped(self, workspace):
        """Whether the workspace holds fewer containers beside the master than
        when the daemon last built it: a window closed, and sway gave its room
        to the others, the master too."""
        seen = self.built.get(workspace["name"])
        return not isinstance(seen, int) and seen is not None and len(top(workspace)["nodes"]) < len(seen[1])

    def arrange(self, new=None, moved=False):
        heir, self.heir = self.heir, None
        gone, self.gone = self.gone, {}
        tree = self.sway.tree()
        unseen = [ws["name"] for ws in workspaces(tree) if ws["name"] not in self.state["workspaces"]]
        if unseen:
            self.state["workspaces"].update(dict.fromkeys(unseen, self.state["layout"]))
            save_state(self.state)
        skip, arrived = self.placed(tree, new, moved)
        if skip:
            return self.remember()
        tree = self.release(tree, arrived)
        tiles = {ws["id"]: tiled(ws) for ws in workspaces(tree)}
        self.kept = {con: ws["name"] for ws in workspaces(tree) for con in tiles[ws["id"]]
                     if self.kept.get(con) == ws["name"] and self.chosen(ws["name"]) == "float"}
        present = {node["id"] for ws in workspaces(tree) for node in windows(ws)}
        self.tiled = (self.tiled & present) | {con for ids in tiles.values() for con in ids}
        self.order[:] = [con for con in self.order if con in self.tiled]
        self.order += [con for ids in tiles.values() for con in ids if con not in self.order]
        for workspace in workspaces(tree):
            if not covered(workspace) and self.tiling(workspace["name"]) is not None and (ids := self.ordered(tiles[workspace["id"]])):
                self.inspect(workspace, ids, arrived)
        heir = heir if heir in present else None
        focused, onscreen, shaped = heir or focused_node(tree)["id"], visible(tree), False
        for workspace in workspaces(tree):
            name = workspace["name"]
            ids = self.ordered(tiles[workspace["id"]])
            if covered(workspace):
                continue
            if self.chosen(name) == "float":
                self.float_all(workspace, [con for con in ids if con not in self.kept], new, name in onscreen)
            elif self.tiling(name) is not None and ids:
                tree_shape = shape(workspace)
                # The tile rule gave the master its saved size as the new
                # window came in: the sizes tell nothing.
                regrouped = self.regrouped(workspace)
                if regrouped and name in gone:
                    self.measure_gone(workspace, ids, gone[name])
                resized = (new in ids and self.resizing.get(name)) or regrouped
                # A window that closed beside the master left the others as
                # they were built.
                seen = self.built.get(name)
                left = (name in gone and not isinstance(seen, int) and seen is not None and ids[0] in seen[1]
                        and trimmed(tree_shape) == trimmed(without(seen, new)))
                if not resized and (trimmed(tree_shape) == self.target(name, ids) or left
                                            or trimmed(without(tree_shape, new)) == self.built.get(name)):
                    # Read as it is when a window closed: the user is not dragging now.
                    self.measure(workspace, ids, new, exact=name in gone)
                shaped = self.shape_up(workspace, ids, new, focused) or shaped
        tree = self.sway.tree() if shaped else tree
        if heir is not None and focused_node(tree)["id"] != heir:
            self.sway.command(refocus(heir))
            tree = self.sway.tree()
        self.sway.command(*self.anchors(tree))
        self.record(tree)
        self.remember()
        if shaped:
            self.sync()

    def record(self, tree):
        for workspace in workspaces(tree):
            name = workspace["name"]
            if self.tiling(name) is None or not tiled(workspace):
                self.built.pop(name, None)
            elif not covered(workspace):
                self.built[name] = trimmed(shape(workspace))

    def move_to(self, target=None, direction=None):
        tree = self.sway.tree()
        node, source = focused_window(tree)
        native = f"move container to workspace {target}" if target else f"move {direction}"
        if source is None or node["nodes"]:
            return self.sway.command(native)
        con = node["id"]
        floated = f"{FLOATED}{con}" in node["marks"]
        managed = node["type"] == "con" and self.tiling(source["name"]) is not None and not covered(source)
        count = direction and node["type"] == "con" and presses(source, con, direction)
        if direction and managed:
            other = beside(source, node, direction, self.order)
            if other is not None:
                return self.exchange(con, other)
            destination = neighbour(tree, source, direction)
            if destination is None:
                return None
        elif direction:
            destination = neighbour(tree, source, direction) if floated or count else None
        else:
            destination = resolve(tree, target)
        if destination is None or destination["id"] == source["id"]:
            return self.sway.command(f"[con_id={con}] {native}")
        name = destination["name"]
        # A move by name or number leaves the focus behind.
        before, away = self.leaving(source, node, focus=bool(target))
        steps = self.entering(destination, con, direction)
        self.carried = (con, name)
        command = native if target else f"move container to workspace {quoted(name)}, focus"
        floats = self.state["workspaces"].get(name, self.state["layout"]) == "float"
        unfloat = [f"[con_id={con}] unmark {FLOATED}{con}, floating disable"] if floated and not floats else []
        if floats and name in visible(tree):
            geometry, rule = self.cascading(destination)
            self.where[con] = name
            self.sway.command(*before, f"[con_id={con}] floating enable, mark --add {FLOATED}{con}, resize set {geometry[0]} px {geometry[1]} px, "
                              f"{command}, move absolute position {geometry[2]} px {geometry[3]} px", *away, *rule)
            self.insist(f"[con_id={con}] {placement(*geometry)}")
        elif steps and (node["type"] == "con" or unfloat):
            self.sway.command(*before, *unfloat, *steps, *away, *([f"[con_id={con}] focus"] if direction else []))
            self.record(self.sway.tree())
        elif unfloat:
            self.sway.command(f"{unfloat[0]}, {command}")
        else:
            # Into a workspace with no layout, no windows or a fullscreen one:
            # sway's own moves, which enter at the near edge, where `move
            # container to workspace` would put it next to the focused window.
            self.sway.command(*before, f"[con_id={con}] " + (", ".join([native] * count) if count else command), *away)

    def entering(self, workspace, con, heading=None):
        """The commands that take `con` from wherever it is to its place in
        the layout of `workspace`, in one step, so sway never draws it where
        it would put it first, next to the focused window there. Coming in
        through an edge, it takes one of the places the layout puts at that
        edge, the nearest one beside the window there that shares the most
        of its branch with the workspace's last focused window, before it on
        a move right or down and after it on a move left or up; when none
        shares a branch, and into tabs and stacked titles, the near end.
        Otherwise it joins the end of the order."""
        name = workspace["name"]
        ids = [other for other in self.ordered(tiled(workspace)) if other != con]
        layout = self.tiling(name)
        near = heading in ("right", "down")

        def built(place):
            return normalize(layout([*ids[:place], con, *ids[place:]]))
        edge = [place for place in range(len(ids) + 1) if con in facing(built(place), heading)] if ids and heading and layout else []
        at = (min if near else max)(edge, default=len(ids))
        if edge and parent_of(built(edge[0]), con)[0] not in TABBED:
            # Beside the window at the edge that is the focused one, or
            # shares the most of its branch; none shares any: the near end.
            now = normalize(layout(ids))
            focus = path(now, last_focused(workspace)) or []
            shared = {leaf: len(os.path.commonprefix([path(now, leaf), focus])) for leaf in facing(now, heading)}
            by = max(shared, key=lambda leaf: (shared[leaf], -ids.index(leaf) if near else ids.index(leaf)))
            if shared[by]:
                want = ids.index(by) + (not near)
                at = min(edge, key=lambda place: (abs(place - want), place if near else -place))
        self.order[:] = [other for other in self.order if other != con]
        self.order.insert(self.order.index(ids[at]) if at < len(ids) else len(self.order), con)
        if not ids or layout is None or covered(workspace):
            return []
        order = [*ids[:at], con, *ids[at:]]
        target = self.target(name, order)
        arrival = self.arrival(workspace, name, ids, con, at, target)
        if arrival is None:
            return after(ids[-1], con) + settling(workspace, target, con, ids[-1]) + self.resize(name, target, order, workspace)
        steps, model = arrival
        # The places stay; the master gives room only to one beside it.
        widened = len(top(model)["nodes"]) > len(top(workspace)["nodes"])
        return steps + (self.resize(name, target, order, workspace) if widened else [])

    def arrival(self, workspace, name, ids, con, at, target):
        """The commands that bring `con` into `workspace` at place `at` and keep
        the places there, with the model they make: it comes in at the end, as
        the tile rule puts a new window, and is swapped back place by place, so
        the windows after it move down a place and each place keeps its size.
        None where the tile rule has no plan for it."""
        plan = foresee(workspace, self.target(name, [*ids, NEXT]))
        if plan is None:
            return None
        anchor, way, nest = plan
        model, steps = copied(workspace), after(anchor, con)
        put_after(model, anchor, con)
        if way:
            move(model, con, way)
            steps.append(f"[con_id={con}] move {way}")
        if nest:
            enclose(model, con, nest)
            steps.append(f"[con_id={con}] split h, {layout_command(nest)}")
        for other in reversed(ids[at:]):
            swap(model, con, other)
            steps.append(f"[con_id={con}] swap container with con_id {other}")
        return (steps, model) if trimmed(shape(model)) == target else None

    def leaving(self, workspace, node, focus=False):
        """The commands to send before and after the one that takes `node`
        away from `workspace`, so the others are in the layout as it goes and
        sway never draws the hole it leaves. Where sway's own going leaves the
        layout as it was, that is all. Else the window is first swapped down,
        place by place, to the place the layout gives up, so every other place
        keeps the size sway keeps for it, and what is left is put in order
        after: the others move up a place, as when the window in the last
        place goes in plain sway. With `focus`, the focus that leaves with it
        goes to the window that takes its place: sway would put it where the
        window was, which may be a container."""
        name, con = workspace["name"], node["id"]
        order = self.ordered(tiled(workspace))
        ids = [other for other in order if other != con]
        if node["type"] != "con" or self.tiling(name) is None or not ids or covered(workspace):
            return [], []
        self.farewell(node)
        rest, target = copied(workspace), self.target(name, ids)
        take_out(rest, con)
        heir = [f"[con_id={self.taker(name, order, con, node['rect'], workspace['rect'])}] focus"] if focus else []

        def sized(model):
            # sway gives the room of a container beside the master to the master too.
            return self.resize(name, target, ids, workspace) if len(top(model)["nodes"]) < len(top(workspace)["nodes"]) else []
        if trimmed(shape(rest)) == target and not wrapped(rest):
            return [], sized(rest) + heir
        model, before = copied(workspace), []
        for other in order[order.index(con) + 1:]:
            swap(model, con, other)
            before.append(f"[con_id={con}] swap container with con_id {other}")
        take_out(model, con)
        steps = reconcile(model, target)
        if steps is None:
            return [], (assemble(rest, target) or []) + self.resize(name, target, ids, workspace) + heir
        return before, steps + sized(model) + heir

    def switch(self, mode):
        """`floating toggle`, `enable` or `disable` of the focused window, or
        `move scratchpad` for `scratchpad`: the workspace it leaves is put in
        order, or it takes the place of a new window, in the same step."""
        tree = self.sway.tree()
        node, source = focused_window(tree)
        native = "move scratchpad" if mode == "scratchpad" else f"floating {mode}"
        floating = node["type"] == "floating_con"
        joins = floating and mode in ("toggle", "disable")
        quits = not floating and mode in ("toggle", "enable", "scratchpad")
        if source is None or node["nodes"] or not (joins or quits):
            return self.sway.command(native)
        con = node["id"]
        if joins:
            self.sway.command(f"[con_id={con}] floating disable", *self.entering(source, con))
        else:
            before, after = self.leaving(source, node, focus=mode == "scratchpad")
            self.sway.command(*before, f"[con_id={con}] {'floating enable' if mode != 'scratchpad' else native}", *after)
        return self.sync()

    def close(self):
        """`kill` of the focused window. Where the hole it leaves is not the
        layout without it, sway would draw that hole before the daemon could
        fill it, so the window goes to the scratchpad and the others to their
        places in the same step, and it is closed from there, the focus going
        to the window that takes its place. A window still there a moment
        later asks first or will not close, and comes back."""
        tree = self.sway.tree()
        node, source = focused_window(tree)
        if source is None or node["nodes"] or node["type"] != "con" or self.tiling(source["name"]) is None:
            return self.sway.command("kill")
        # A size set by the mouse sends no event: read it as it is now.
        self.measure_all(tree, exact=True)
        con, rest = node["id"], copied(source)
        take_out(rest, con)
        ids = [other for other in self.ordered(tiled(source)) if other != con]
        if not ids or covered(source):
            return self.sway.command("kill")
        target = self.target(source["name"], ids)
        # sway gives the room of a window beside the master to the master too.
        regrouped = self.sizing(source["name"], target, ids) and len(top(rest)["nodes"]) < len(top(source)["nodes"])
        if trimmed(shape(rest)) == target and not regrouped:
            # The close event hands the focus on.
            return self.sway.command("kill")
        before, after = self.leaving(source, node, focus=True)
        self.sway.command(*before, f"[con_id={con}] move scratchpad", *after, f"[con_id={con}] kill")
        threading.Timer(CLOSING, self.sway.tick, [f"{ACT} show {con}"]).start()
        return self.sync()

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
            geometry, rule = self.cascading(workspace)
            commands += [f"[con_id={con}] mark --add {FLOATED}{con}, {placement(*geometry)}", *rule]
        else:
            commands += [f"[con_id={con}] floating disable", *self.entering(workspace, con)]
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
        if con in self.order and other in self.order:
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
            if ids := tiled(workspace):
                self.sway.command(*(assemble(workspace, ("splith", ids), focused_node(tree)["id"]) or []))
            elif workspace["layout"] in TABBED and focused_node(tree)["id"] == workspace["id"]:
                # Left tabbed or stacked by `layout` on the empty workspace: sway
                # turns a workspace's own layout only while it has the focus.
                self.sway.command("layout splith")
        self.arrange()

    def rename(self, workspace):
        old, new = self.names.get(workspace["id"]), workspace["name"]
        if old is None or old == new:
            return
        for table in (self.state["workspaces"], self.slots, self.pending, self.areas, self.ratios, self.leans, self.gaps, self.built):
            if old in table and new not in table:
                table[new] = table.pop(old)
        self.kept = {con: new if place == old else place for con, place in self.kept.items()}
        save_state(self.state)

    def restore(self, sway):
        tree = sway.tree()
        commands = [command for name in list(self.rules) for command in self.float_rule(name, None)]
        commands += [command for name in list(self.anchored) for command in self.tile_rule(name, False)]
        sway.command(*commands, *(f"[con_id={con}] opacity 1" for con in invisible(tree)), *(f"unmark {mark}" for mark in marked(tree, PLACING)))

    def recover(self):
        self.sway.command(*(f"[con_id={con}] opacity 1" for con in invisible(self.sway.tree())))

    def pause(self):
        left = self.watching - time.monotonic()
        return min(WATCH[0], left) if left > 0 else None

    def drifted(self, tree):
        """Whether a workspace the daemon tiles has changed since it last looked."""
        return any(self.tiling(ws["name"]) is not None and ws["name"] in self.built and not covered(ws)
                   and trimmed(shape(ws)) != self.built[ws["name"]] for ws in workspaces(tree))

    def handle(self, kind, event):
        change = event.get("change")
        if kind == "idle":
            if self.drifted(tree := self.sway.tree()):
                self.watching = 0.0
                self.arrange()
            else:
                self.remeasure(tree)
        elif kind == "tick":
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
                self.remeasure(self.sway.tree())
            elif self.drifted(tree := self.sway.tree()):
                self.arrange()
            else:
                self.remeasure(tree)
        elif kind == "output":
            self.arrange()
        elif kind == "workspace" and change == "focus":
            positions = self.pending.pop(event["current"]["name"], {})
            self.sway.command(*(f"[con_id={con}] {placement(*geometry)}" for con, geometry in positions.items()))
        elif kind == "workspace" and change in ("init", "move", "rename", "reload"):
            if change == "rename":
                self.rename(event["current"])
            if change == "reload":
                for table in (self.rules, self.anchored, self.sized, self.roles, self.adopted):
                    table.clear()
                self.adopt()
            self.arrange()
        elif kind == "window" and change == "focus":
            self.focus, self.refocused = [self.focus[1], event["container"]["id"]], True
            self.watching = time.monotonic() + WATCH[1]
        elif kind == "window" and change == "fullscreen_mode":
            self.arrange()
        elif kind == "window" and change in ("new", "close", "floating", "move"):
            con = event["container"]["id"]
            if change == "close":
                self.farewell(event["container"])
                name = self.where.get(con)
                room = next((ws["rect"] for ws in workspaces(self.sway.tree()) if ws["name"] == name), None)
                if self.tiling(name) is not None:
                    self.gone[name] = event["container"]
                if event["container"].get("focused") and room is not None and self.tiling(name) is not None:
                    # sway focuses the window last focused near it; the one
                    # that takes its place is the one under the mouse.
                    built = self.built.get(name)
                    ids = self.ordered(leaves(built)) if built is not None else []
                    self.heir = self.taker(name, ids, con, event["container"]["rect"], room)
            if change == "floating" and event["container"]["type"] == "con" and self.chosen(self.where.get(con)) == "float":
                self.kept[con] = self.where[con]
            elif change == "floating":
                self.kept.pop(con, None)
            if change == "new":
                # The tile rule has used the steps planned for this window.
                self.roles.clear()
                self.sync()
                if con not in self.order:
                    self.order.append(con)
            self.arrange(con, moved=change == "move")

    def adopt(self):
        """Put the layout's moves on the keys that the config binds to sway's
        own `move`, `move scratchpad` and `floating`. Bindings made at
        runtime last until sway reloads."""
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
        """A key for a move, the scratchpad or floating that the config reader
        missed: take it over once it is used."""
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
        elif action == "hide":
            self.switch("scratchpad")
        elif action == "close":
            self.close()
        elif action.startswith("float ") and action[6:] in ("toggle", "enable", "disable"):
            self.switch(action[6:])
        elif action == "show" or (action.startswith("show ") and action[5:].isdigit()):
            self.unhide(int(action[5:]) if action[5:] else None)
        elif action.startswith("default ") and action[8:] in LAYOUTS:
            self.state["layout"] = action[8:]
            save_state(self.state)
        elif action.startswith("move ") and action[5:].strip():
            argument = action[5:].strip()
            self.move_to(direction=argument) if argument in SPLITS else self.move_to(target=argument)

    def run(self):
        events = self.sway.subscribe(SUBSCRIPTIONS, self.pause)
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


def icon(name, tree, highlight):
    """The layout as a small screen of its own colours, like an application's
    icon: a launcher draws the same picture on every line and on the selected
    one, so it has to read on any theme and any selection."""
    rects = '<rect x="0.5" y="0.5" width="47" height="29" rx="4" fill="#2b2b2b" stroke="#6b6b6b"/>'
    rects += "".join(
        f'<rect x="{x + 0.75:.2f}" y="{y + 0.75:.2f}" width="{width - 1.5:.2f}" height="{height - 1.5:.2f}" rx="1" '
        f'fill="{"#f5f5f5" if leaf == highlight else "#8f8f8f"}"/>'
        for leaf, x, y, width, height in boxes(normalize(tree), 3, 3, 42, 24))
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 30">{rects}</svg>'
    path = ICONS / f"{name}.svg"
    if not path.is_file() or path.read_text(errors="replace") != svg:
        path.write_text(svg)
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


def fuzzel_version():
    """fuzzel's version as numbers, or None when it does not say."""
    import subprocess
    try:
        output = subprocess.run(["fuzzel", "--version"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    found = re.search(r"(\d+)\.(\d+)\.(\d+)", output)
    return tuple(int(part) for part in found.groups()) if found else None


def launcher(custom, count, selected):
    """A known program by its name alone gets the arguments that suit the
    menu; anything longer is the command as given. With the command comes
    how a line carries its picture, or None for a menu without pictures."""
    import shlex
    words = shlex.split(custom)
    if len(words) != 1 or words[0] not in LAUNCHERS:
        return words, None
    if words[0] == "fuzzel":
        # --no-sort came with fuzzel 1.11 and --select-index with 1.12; an
        # older fuzzel stops at either, so it gets the menu without them.
        version = fuzzel_version()
        command = ["fuzzel", "--dmenu", "--index", "--width", "45", "--lines", str(count), "--line-height", "40", "--prompt", "layout: "]
        if version is None or version >= (1, 11):
            command.append("--no-sort")
        if version is None or version >= (1, 12):
            command += ["--select-index", str(selected)]
        return command, "{entry}\0icon\x1f{icon}"
    commands = {
        # rofi draws its icons as tall as a line of text, too small for a layout.
        "rofi": (["rofi", "-dmenu", "-i", "-no-custom", "-format", "i", "-show-icons", "-p", "layout", "-selected-row", str(selected),
                  "-theme-str", "element-icon { size: 1.6em; } element-text { vertical-align: 0.5; }"], "{entry}\0icon\x1f{icon}"),
        # wofi puts the entries picked most often first, keeping the count in a
        # cache file; without one the menu keeps the order of the layouts.
        # wofi prints back the line without its picture with parse_action.
        "wofi": (["wofi", "--dmenu", "--insensitive", "--prompt", "layout", "--cache-file", "/dev/null",
                  "--allow-images", "--parse-search", "--define", "dmenu-parse_action=true", "--define", "image_size=40"], "img:{icon}:text:{entry}"),
        "tofi": (["tofi", "--prompt-text", "layout: "], None),
        "bemenu": (["bemenu", "-i", "-l", str(count), "-p", "layout"], None),
        "wmenu": (["wmenu", "-i", "-l", str(count), "-p", "layout"], None),
        "dmenu": (["dmenu", "-i", "-l", str(count), "-p", "layout"], None),
    }
    return commands[words[0]]


def picked_layout(output, names):
    output = output.strip()
    if output.isdigit():
        return names[int(output)] if int(output) < len(names) else None
    name = output.split(" — ")[0].strip()
    return name if name in names else None


def notify(message, wait=False):
    """Show `message` as a desktop notification, without waiting for it
    unless the process is about to end."""
    import shutil
    import subprocess
    def send():
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(["notify-send", "swaytiles", message], capture_output=True, timeout=5)
    if shutil.which("notify-send"):
        send() if wait else threading.Thread(target=send, daemon=True).start()


def warn(message):
    print(f"swaytiles: {message}", file=sys.stderr)
    notify(message, wait=True)


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
        entries = [icons.format(entry=entry, icon=icon(name, (layout or flat('splith'))(list(range(5))), None if layout is None else 0))
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
        if arguments[0] == "move" and (arguments[1:] in ([name] for name in SPLITS) or (arguments[1:2] == ["number"] and len(arguments) == 3)):
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

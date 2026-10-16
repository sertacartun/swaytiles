"""With `swaytiles new master` a new window takes the master's place, as in dwm."""

import os
import re
import signal

import pytest
from harness import expected

import swaytiles

LEADING = list(swaytiles.LEADING)


def newest_first(count, prefix="w"):
    return [f"{prefix}{index}" for index in range(count, 0, -1)]


@pytest.mark.parametrize("layout", LEADING)
def test_a_new_window_becomes_the_master(session, layout):
    s = session(layout, new="master")
    for count in range(1, 7):
        s.open(f"w{count}")
        assert s.shape() == expected(layout, count, newest_first(count)), f"after opening w{count}"
        assert s.focused() == f"w{count}"
    s.close("w6")
    assert s.shape() == expected(layout, 5, newest_first(5))
    s.close("w2")
    assert s.shape() == expected(layout, 4, ["w5", "w4", "w3", "w1"])
    assert s.alive


@pytest.mark.parametrize("layout", LEADING)
def test_it_is_drawn_as_the_master_from_the_first_frame(session, layout):
    s = session(layout, new="master")

    def frame():
        return {node["name"]: node["rect"] for node in swaytiles.leaf_nodes(s.workspace())}
    for count in range(1, 7):
        os.kill(s.daemon.pid, signal.SIGSTOP)
        try:
            s.open(f"w{count}", settle=False)
            s.wait(lambda: False, 0.2)
            first = frame()
        finally:
            os.kill(s.daemon.pid, signal.SIGCONT)
        drawn = s.drawn(lambda: None)
        assert frame() == first, f"w{count}: {drawn}"
        assert s.shape() == expected(layout, count, newest_first(count))


@pytest.mark.parametrize("layout", ["dwindle", "centered"])
def test_many_windows_are_drawn_as_the_master_from_the_first_frame(session, layout):
    # The whole stack moves on by one place in the rule, however long it is.
    s = session(layout, new="master").fill(12)

    def frame():
        return {node["name"]: node["rect"] for node in swaytiles.leaf_nodes(s.workspace())}
    os.kill(s.daemon.pid, signal.SIGSTOP)
    try:
        s.open("w13", settle=False)
        s.wait(lambda: False, 0.2)
        first = frame()
    finally:
        os.kill(s.daemon.pid, signal.SIGCONT)
    drawn = s.drawn(lambda: None)
    assert frame() == first, drawn
    assert s.shape() == expected(layout, 13, newest_first(13))


@pytest.mark.parametrize("layout", ["master", "wide", "centered", "dwindle"])
def test_the_new_master_takes_the_masters_size(session, layout):
    s = session(layout, new="master").fill(3)
    axis = "height" if layout == "wide" else "width"
    s.command(f"[title=^w3$] resize set {axis} 70 ppt")
    size = s.node("w3")["rect"][axis]
    s.open("w4")
    s.open("w5")
    assert s.node("w5")["rect"][axis] == size
    s.close("w5")
    assert s.node("w4")["rect"][axis] == size


@pytest.mark.parametrize("layout", ["tabbed", "stacking", "grid"])
def test_layouts_without_a_master_are_as_before(session, layout):
    s = session(layout, new="master").fill(5)
    assert s.shape() == expected(layout, 5)


def test_the_command_turns_it_on_and_off(session):
    s = session("master").fill(2)
    assert s.run("new", "master").returncode == 0
    s.settle()
    s.open("w3")
    assert s.shape() == "H[w3 V[w1 w2]]"
    assert s.chosen() == "master"
    assert s.run("new", "stack").returncode == 0
    s.settle()
    s.open("w4")
    assert s.shape() == "H[w3 V[w1 w2 w4]]"
    assert s.run("new", "first").returncode == 2


def test_the_setting_is_kept_for_every_workspace_and_restart(session):
    s = session("master", config="bindsym Mod4+F8 nop layout new master\n")
    s.key("F8")
    s.fill(2)
    s.command("workspace 2")
    s.fill(2, "x")
    assert s.shape("2") == "H[x2 x1]"
    # sway keeps the rules of every daemon that ran: none of them swaps twice.
    for count in (3, 4):
        s.stop_daemon()
        s.start_daemon()
        s.open(f"x{count}")
        assert s.shape("2") == expected("master", count, newest_first(count, "x"))
    assert s.shape("1") == "H[w2 w1]"


def test_windows_that_arrive_join_the_stack(session):
    s = session("master", new="master").fill(3)
    s.command("workspace 2")
    s.open("x1")
    s.command("[title=^x1$] move container to workspace 1")
    assert s.shape("1") == "H[w3 V[w2 w1 x1]]"
    s.command("workspace 1")
    s.command("[title=^w2$] move scratchpad")
    assert s.run("show").returncode == 0
    s.settle()
    assert s.shape("1") == "H[w3 V[w1 x1 w2]]"


def test_a_dialog_tiled_by_hand_joins_the_stack(session):
    s = session("master", new="master", config='for_window [title="^d1$"] floating enable\n').fill(2)
    s.open("d1")
    assert s.shape() == "H[w2 w1] F[d1]"
    s.command("[title=^d1$] floating disable")
    assert s.shape() == "H[w2 V[w1 d1]]"


def test_two_outputs(session):
    s = session("master", new="master", outputs=2)
    outputs = [output["name"] for output in s.tree()["nodes"] if output["name"] != "__i3"]
    s.fill(2)
    s.command(f"focus output {outputs[1]}")
    second = s.ipc.focused_workspace()
    s.fill(3, "x")
    assert s.shape(second) == "H[x3 V[x2 x1]]"
    assert s.shape("1") == "H[w2 w1]"
    # Carried across, a window joins the stack where it lands.
    s.focus("x2")
    s.command(f"[title=^x2$] move container to output {outputs[0]}")
    assert s.shape("1") == "H[w2 V[w1 x2]]"
    assert s.shape(second) == "H[x3 x1]"
    # The output with the focus takes the new window, as its master.
    s.command(f"focus output {outputs[1]}")
    s.open("x4")
    assert s.shape(second) == "H[x4 V[x3 x1]]"
    s.command(f"focus output {outputs[0]}")
    s.open("w3")
    assert s.shape("1") == "H[w3 V[w2 w1 x2]]"
    assert s.alive


@pytest.mark.parametrize("layout", ["master", "dwindle"])
def test_windows_opened_at_once(session, layout):
    s = session(layout, new="master").fill(2)
    for index in range(3, 7):
        s.open(f"w{index}", settle=False)
    s.settle()
    assert s.shape() == expected(layout, 6, newest_first(6))
    assert s.focused() == "w6"


@pytest.mark.parametrize("layout", ["centered", "dwindle"])
def test_two_outputs_each_with_a_ring(session, layout):
    s = session(layout, new="master", outputs=2)
    outputs = [output["name"] for output in s.tree()["nodes"] if output["name"] != "__i3"]
    s.fill(3)
    s.command(f"focus output {outputs[1]}")
    second = s.ipc.focused_workspace()
    s.fill(4, "x")
    assert s.shape(second) == expected(layout, 4, newest_first(4, "x"))
    assert s.shape("1") == expected(layout, 3, newest_first(3))
    # The workspace goes over to the other output with its marks.
    s.command(f"move workspace to output {outputs[0]}")
    s.open("x5")
    assert s.shape(second) == expected(layout, 5, newest_first(5, "x"))
    s.command("workspace 1")
    s.open("w4")
    assert s.shape("1") == expected(layout, 4, newest_first(4))
    assert s.alive


def stopped(s, action):
    """Run `action` with the daemon stopped, so the rule is not set again in between."""
    os.kill(s.daemon.pid, signal.SIGSTOP)
    try:
        action()
    finally:
        os.kill(s.daemon.pid, signal.SIGCONT)
    s.settle()


@pytest.mark.parametrize("layout", ["master", "tabbed-master", "wide", "centered", "dwindle", "spiral"])
def test_two_windows_before_the_daemon_looks(session, layout):
    # As `exec a; exec b` does: the second comes in by a rule used up by the first.
    s = session(layout, new="master").fill(2)
    stopped(s, lambda: (s.open("w3", settle=False), s.open("w4", settle=False)))
    assert s.shape() == expected(layout, 4, newest_first(4))
    assert s.chosen() == layout


@pytest.mark.parametrize("layout", ["master", "centered", "dwindle"])
def test_a_window_of_the_ring_closes_as_one_opens(session, layout):
    s = session(layout, new="master").fill(3)

    def swap():
        s.command("[title=^w1$] kill", settle=False)
        s.wait(lambda: s.node("w1") is None)
        s.open("w4", settle=False)
    stopped(s, swap)
    assert s.shape() == expected(layout, 3, ["w4", "w3", "w2"])
    assert s.chosen() == layout


def test_a_window_dragged_in_stays_where_it_is_dropped(session):
    s = session("master", new="master", outputs=2)
    outputs = [output["name"] for output in s.tree()["nodes"] if output["name"] != "__i3"]
    s.fill(3)
    s.command(f"focus output {outputs[1]}")
    s.open("x1")
    assert s.shape("1") == "H[w3 V[w2 w1]]"
    rect = s.node("w2")["rect"]
    s.drag("x1", rect["x"] + rect["width"] // 2, rect["y"] + rect["height"] * 3 // 4)
    assert s.shape("1") == "H[w3 V[w2 x1 w1]]"
    assert s.chosen("1") == "master"
    s.open("w4")
    assert s.shape("1") == "H[w4 V[w3 w2 x1 w1]]"


def older_copy(s):
    """Give sway the tile rule as a daemon from before `new master` set it,
    before this daemon's own, as a session where one ran keeps it."""
    sent = s.sent.read_text()
    gate = re.search(r'for_window \[all\] "[^"]*"', sent)[0]
    rule = re.search(r'for_window \[workspace="\^1\$" tiling\] "([^"]*)"', sent)[1]
    older = "; ".join(step for step in rule.split("; ") if "layout_lead" not in step and " 0" not in step[-2:])
    s.stop_daemon()
    s.command("reload")
    s.command(f'{gate}; for_window [workspace="^1$" tiling] "{older}"')
    s.start_daemon()


@pytest.mark.parametrize(("layout", "count"), [("master", 2), ("dwindle", 2), ("centered", 3)])
def test_the_master_size_holds_with_a_rule_left_by_an_older_daemon(session, layout, count):
    s = session(layout, new="master", config="bindsym Mod4+F7 resize set width 60 ppt\n").fill(count)
    older_copy(s)
    # The master, in the middle in centered, and a window beside it.
    leaves = [node["name"] for node in swaytiles.leaf_nodes(s.workspace())]
    master, other = (leaves[1], leaves[0]) if layout == "centered" else (leaves[0], leaves[-1])
    s.focus(master)
    s.key("F7")
    size = s.node(master)["rect"]["width"]
    s.close(other)

    def frame():
        return {node["name"]: node["rect"] for node in swaytiles.leaf_nodes(s.workspace())}
    os.kill(s.daemon.pid, signal.SIGSTOP)
    try:
        s.open("new", settle=False)
        s.wait(lambda: False, 0.2)
        first = frame()
    finally:
        os.kill(s.daemon.pid, signal.SIGCONT)
    drawn = s.drawn(lambda: None)
    assert frame() == first, drawn
    assert s.node("new")["rect"]["width"] == size

"""What happens on one workspace stays on it."""

import os
import signal

import pytest

import swaytiles


def test_a_choice_leaves_seen_workspaces_alone(session):
    s = session("default")
    s.command("workspace 1")
    s.open("a1")
    s.open("a2")
    s.command("workspace 4")
    s.command("workspace 2")
    s.open("b1")
    s.open("b2")
    s.open("b3")
    before = s.shape("1", exact=True)
    s.choose("master")
    assert s.shape("2") == "H[b1 V[b2 b3]]"
    assert (s.chosen("1"), s.chosen("2"), s.chosen("4")) == ("default", "master", "default")
    assert s.shape("1", exact=True) == before
    s.command("workspace 1")
    s.open("a3")
    assert s.chosen("1") == "default" and s.shape("1") == "H[a1 a2 a3]"
    s.command("workspace 4")
    s.open("d1")
    s.open("d2")
    s.open("d3")
    assert s.chosen("4") == "default" and s.shape("4") == "H[d1 d2 d3]"
    s.choose("tabbed")
    s.command("workspace 2")
    s.open("b4")
    assert s.chosen("2") == "master" and s.shape("2") == "H[b1 V[b2 b3 b4]]"
    assert s.chosen("1") == "default" and s.shape("1") == "H[a1 a2 a3]"
    s.stop_daemon()
    s.start_daemon()
    s.command("workspace 1")
    s.open("a4")
    assert (s.chosen("1"), s.chosen("2"), s.chosen("4")) == ("default", "master", "tabbed")
    assert (s.shape("1"), s.shape("2"), s.shape("4")) == ("H[a1 a2 a3 a4]", "H[b1 V[b2 b3 b4]]", "T[d1 d2 d3]")


def test_only_the_default_command_changes_the_layout_of_new_workspaces(session):
    s = session("default")
    s.command("workspace 2")
    s.open("b1")
    s.choose("master")
    s.command("workspace 3")
    s.open("c1")
    s.open("c2")
    s.open("c3")
    assert s.chosen("3") == "default" and s.shape("3") == "H[c1 c2 c3]"
    assert s.run("default", "wide").returncode == 0
    s.settle()
    assert (s.chosen("2"), s.chosen("3")) == ("master", "default") and s.shape("3") == "H[c1 c2 c3]"
    s.command("workspace 5")
    s.open("e1")
    s.open("e2")
    assert s.chosen("5") == "wide" and s.shape("5") == "V[e1 e2]"
    assert s.run("default", "nope").returncode == 2


def test_arranging_from_an_old_tree_leaves_the_focus_where_the_user_went(session):
    s = session("default")
    s.command("workspace 3")
    for title in ("c1", "c2", "c3"):
        s.open(title)
    s.stop_daemon()
    sway = swaytiles.Sway(s.env["SWAYSOCK"])
    tree = sway.tree()
    workspace = next(ws for ws in swaytiles.workspaces(tree) if ws["name"] == "3")
    ids, focused = swaytiles.tiled(workspace), swaytiles.focused_node(tree)["id"]
    s.command("workspace 1")
    sway.command(*swaytiles.assemble(workspace, swaytiles.trimmed(swaytiles.LAYOUTS["master"](ids)), focused))
    s.settle()
    assert s.shape("3") == "H[c1 V[c2 c3]]"
    assert s.focused() == "1"
    s.command("workspace 3")
    assert s.focused() == "c3"
    workspace = next(ws for ws in swaytiles.workspaces(sway.tree()) if ws["name"] == "3")
    sway.command(*swaytiles.assemble(workspace, swaytiles.trimmed(swaytiles.LAYOUTS["master-right"](ids)), focused))
    s.settle()
    assert s.shape("3") == "H[V[c2 c3] c1]"
    assert s.focused() == "c3"


@pytest.mark.parametrize("name", ["2", "web dev", "a.b[c]"])
@pytest.mark.parametrize("number", [signal.SIGKILL, signal.SIGSTOP])
def test_a_new_window_stays_on_its_workspace_while_the_daemon_is_away(session, name, number):
    s = session("default", workspaces={"1": "default", name: "master"})
    s.command("workspace 1")
    s.open("a1")
    s.command(f'workspace "{name}"')
    for title in ("b1", "b2", "b3"):
        s.open(title)
    assert s.shape(name) == "H[b1 V[b2 b3]]"
    os.killpg(s.daemon.pid, number)
    try:
        s.command("[title=^b3$] move container to workspace 1")
        s.command(f'workspace "{name}"')
        s.open("b4")
        assert s.shape("1") == "H[a1 b3]"
        assert "b4" in s.shape(name)
    finally:
        if number == signal.SIGSTOP:
            os.killpg(s.daemon.pid, signal.SIGCONT)
            s.settle()
    if number == signal.SIGSTOP:
        assert (s.shape("1"), s.shape(name)) == ("H[a1 b3]", "H[b1 V[b2 b4]]")
        s.open("b5")
        assert s.shape(name) == "H[b1 V[b2 b4 b5]]"

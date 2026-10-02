"""What happens on one workspace stays on it."""

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


def test_a_choice_becomes_the_layout_of_workspaces_not_seen_yet(session):
    s = session("default")
    s.command("workspace 2")
    s.open("b1")
    s.choose("master")
    s.command("workspace 3")
    s.open("c1")
    s.open("c2")
    s.open("c3")
    assert s.chosen("3") == "master" and s.shape("3") == "H[c1 V[c2 c3]]"


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
    swaytiles.rearrange(sway, workspace, swaytiles.trimmed(swaytiles.LAYOUTS["master"](ids)), focused)
    s.settle()
    assert s.shape("3") == "H[c1 V[c2 c3]]"
    assert s.focused() == "1"
    s.command("workspace 3")
    assert s.focused() == "c3"
    workspace = next(ws for ws in swaytiles.workspaces(sway.tree()) if ws["name"] == "3")
    swaytiles.rearrange(sway, workspace, swaytiles.trimmed(swaytiles.LAYOUTS["master-right"](ids)), focused)
    s.settle()
    assert s.shape("3") == "H[V[c2 c3] c1]"
    assert s.focused() == "c3"

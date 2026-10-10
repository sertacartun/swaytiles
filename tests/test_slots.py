"""Sizes are sway's own: a layout keeps the containers sway sizes and moves
only windows between them, so what a user sized stays as in plain sway.

Each test builds the same workspace twice, sizes it the same way, and lets a
plain sway, with the daemon stopped, show what the sizes should be."""

import pytest

CONFIG = "gaps inner 5\nbindsym Mod4+F10 kill\nbindsym Mod4+F9 move scratchpad\n"

# A layout, its windows, how they are sized by hand, and the window to close.
SIZED = {
    "dwindle": (5, ["[title=^w1$] resize set width 65 ppt", "[title=^w3$] resize set width 40 ppt"], "w2"),
    "spiral": (6, ["[title=^w1$] resize set width 60 ppt", "[title=^w2$] resize set height 35 ppt"], "w3"),
    "centered": (7, ["[title=^w3$] resize set width 15 ppt", "[title=^w1$] resize set width 55 ppt",
                     "[title=^w2$] resize set height 20 ppt"], "w2"),
    "master": (5, ["[title=^w1$] resize set width 70 ppt", "[title=^w3$] resize set height 40 ppt"], "w1"),
    "wide": (4, ["[title=^w1$] resize set height 60 ppt", "[title=^w2$] resize set width 45 ppt"], "w1"),
    "grid": (6, ["[title=^w1$] resize set width 45 ppt", "[title=^w5$] resize set height 40 ppt"], "w2"),
}


def built(session, layout, count, sizes):
    s = session(layout, config=CONFIG).fill(count)
    for command in sizes:
        s.command(command)
    # Read by the daemon as a mouse would leave them, after a focus change.
    s.focus("w1")
    s.wait(lambda: False, 0.6)
    return s


def plain(session, layout, count, sizes):
    s = built(session, layout, count, sizes)
    s.stop_daemon()
    s.releasing = True
    return s


def rects(s, *titles):
    """The places of the windows, by title, or all of them as a sorted list."""
    found = {}
    stack = [s.workspace()]
    while stack:
        node = stack.pop()
        if not node["nodes"] and node["type"] == "con":
            found[node["name"]] = tuple(node["rect"][key] for key in ("x", "y", "width", "height"))
        stack += node["nodes"]
    return {title: found[title] for title in titles} if titles else sorted(found.values())


@pytest.mark.parametrize("layout", SIZED)
def test_closing_by_key_keeps_every_size_as_sway_does(session, layout):
    # The window closes from the middle and the others move up a place: each
    # place keeps its size, and the last one's room goes to its neighbours,
    # as when the window in the last place closes in plain sway.
    count, sizes, victim = SIZED[layout]
    s, oracle = built(session, layout, count, sizes), plain(session, layout, count, sizes)
    oracle.close(f"w{count}")
    s.focus(victim)
    drawn = s.drawn(lambda: s.key("F10"))
    assert len(drawn) == 1, drawn
    assert rects(s) == rects(oracle)


def test_closing_by_key_where_sway_keeps_the_layout_is_sways_own_close(session):
    # A stack window closing leaves the layout as it was: nothing to do but
    # what sway does, its room going to the windows beside it.
    count, sizes = 5, ["[title=^w1$] resize set width 70 ppt", "[title=^w3$] resize set height 40 ppt"]
    s, oracle = built(session, "master", count, sizes), plain(session, "master", count, sizes)
    oracle.close("w3")
    s.focus("w3")
    s.key("F10")
    assert rects(s) == rects(oracle)


@pytest.mark.parametrize(("layout", "victim", "kept"), [
    ("dwindle", "w3", ("w1", "w2")),
    ("spiral", "w4", ("w1", "w2", "w3")),
    ("centered", "w4", ("w1",)),
    ("master", "w3", ("w1",)),
])
def test_a_window_closing_by_itself_leaves_the_places_before_it_alone(session, layout, victim, kept):
    count, sizes, _ = SIZED[layout]
    s = built(session, layout, count, sizes)
    before = rects(s, *kept)
    s.close(victim)
    assert rects(s, *kept) == before


def test_a_window_closing_by_itself_in_centered_keeps_the_columns(session):
    count, sizes, _ = SIZED["centered"]
    s = built(session, "centered", count, sizes)
    widths = s.columns()
    s.close("w2")
    assert s.columns() == widths


@pytest.mark.parametrize(("layout", "kept"), [
    ("dwindle", ("w1", "w2", "w3", "w4")),
    ("centered", ("w1", "w3", "w5", "w7")),
    ("master", ("w1",)),
    ("wide", ("w1",)),
])
def test_a_new_window_takes_room_only_beside_it(session, layout, kept):
    count, sizes, _ = SIZED[layout]
    s = built(session, layout, count, sizes)
    before = rects(s, *kept)
    s.open("new")
    assert rects(s, *kept) == before


def test_a_window_moved_in_takes_room_only_beside_it(session):
    count, sizes, _ = SIZED["dwindle"]
    s = built(session, "dwindle", count, sizes)
    before = rects(s, "w1", "w2", "w3")
    s.command("workspace 2")
    s.open("x")
    s.run("move", "number", "1")
    s.settle()
    assert s.shape() == "H[w1 V[w2 H[w3 V[w4 H[w5 x]]]]]"
    assert rects(s, "w1", "w2", "w3") == before


def test_a_window_hidden_and_shown_again_leaves_the_columns_as_they_were(session):
    count, sizes, _ = SIZED["centered"]
    s = built(session, "centered", count, sizes)
    widths = s.columns()
    s.focus("w2")
    s.key("F9")
    s.run("show")
    s.settle()
    assert s.columns() == widths


def test_a_window_closing_by_itself_in_the_middle_of_dwindle_leaves_the_layout_whole(session):
    # sway's `split none` takes away every container left with one child up
    # the tree, not only the one it is run in.
    s = session("dwindle", config=CONFIG).fill(4)
    s.close("w3")
    assert s.shape(exact=True) == "H[w1 V[w2 H[w4]]]"


@pytest.mark.parametrize("layout", ["tabbed", "stacking"])
def test_the_last_window_closing_in_tabs_is_no_trouble(session, layout):
    s = session(layout, config=CONFIG)
    s.open("w1")
    s.close("w1")
    s.open("w2")
    assert s.shape() == f"{'T' if layout == 'tabbed' else 'S'}[w2]"


def test_the_master_on_top_keeps_its_size_as_windows_beside_it_close_again_and_again(session):
    # Title bars count: sway leaves them out of a window's rect.
    s = session("wide", config=CONFIG)
    s.open("w1")
    s.open("w2")
    s.command("[title=^w1$] resize set height 60 ppt")
    s.focus("w1")
    s.wait(lambda: False, 0.6)
    for index in range(3, 7):
        s.close(f"w{index - 1}")
        s.open(f"w{index}")
    master, ws = s.node("w1"), s.workspace()
    height = master["rect"]["height"] + master["deco_rect"]["height"]
    assert abs(height / ws["rect"]["height"] - 0.6) < 0.01

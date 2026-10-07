"""Windows dragged with the mouse inside a workspace take their new place in the layout."""

import pytest
from harness import expected


def opened(session, layout, count=4):
    s = session(layout)
    for index in range(1, count + 1):
        s.open(f"w{index}")
    return s


def middle(s, title):
    rect = s.node(title)["rect"]
    return rect["x"] + rect["width"] // 2, rect["y"] + rect["height"] // 2


def test_a_window_dropped_left_of_the_master_becomes_the_master(session):
    s = opened(session, "master")
    rect = s.node("w1")["rect"]
    s.drag("w4", rect["x"] + 5, rect["y"] + rect["height"] // 2)
    assert s.shape() == "H[w4 V[w1 w2 w3]]"
    assert s.chosen() == "master"
    s.open("w5")
    assert s.shape() == "H[w4 V[w1 w2 w3 w5]]"


def test_a_master_dropped_into_the_stack_takes_its_place_there(session):
    s = opened(session, "master")
    rect = s.node("w3")["rect"]
    s.drag("w1", rect["x"] + rect["width"] // 2, rect["y"] + rect["height"] * 3 // 4)
    assert s.shape() == "H[w2 V[w3 w1 w4]]"
    assert s.chosen() == "master"


def test_a_window_dropped_on_the_edge_of_the_workspace_goes_last(session):
    # sway splits the workspace and reports no event.
    s = opened(session, "master")
    rect = s.node("w3")["rect"]
    s.drag("w1", rect["x"] + rect["width"] // 2, rect["y"] + rect["height"] - 5)
    assert s.wait(lambda: s.shape() == "H[w2 V[w3 w4 w1]]"), s.shape()
    assert s.chosen() == "master"


def test_a_window_dropped_where_it_was_keeps_the_layout(session):
    s = opened(session, "master")
    rect = s.node("w3")["rect"]
    s.drag("w1", rect["x"] + rect["width"] // 2, rect["y"] + 5)
    assert s.wait(lambda: s.shape(exact=True) == "H[w1 V[w2 w3 w4]]"), s.shape(exact=True)
    assert s.chosen() == "master"


def test_windows_swapped_with_the_mouse_keep_their_new_places(session):
    s = opened(session, "master")
    s.drag("w4", *middle(s, "w1"))
    assert s.shape() == "H[w4 V[w2 w3 w1]]"
    s.open("w5")
    assert s.shape() == "H[w4 V[w2 w3 w1 w5]]"
    assert s.chosen() == "master"


def test_a_tab_dropped_on_the_lower_half_of_the_tabs_goes_last(session):
    s = opened(session, "tabbed")
    x, _ = middle(s, "w4")
    s.drag("w1", x, s.node("w4")["rect"]["y"] + s.node("w4")["rect"]["height"] - 5)
    assert s.shape() == "T[w2 w3 w4 w1]"
    assert s.chosen() == "tabbed"


@pytest.mark.parametrize("layout", ["grid", "centered", "dwindle", "spiral", "wide", "tabbed-master"])
def test_a_drag_keeps_the_layout(session, layout):
    s = opened(session, layout)
    rect = s.node("w2")["rect"]
    s.drag("w4", rect["x"] + 5, rect["y"] + rect["height"] // 2)
    assert s.chosen() == layout
    assert s.shape() in {expected(layout, 4, order) for order in orders(["w1", "w2", "w3", "w4"])}


def orders(names):
    import itertools
    return [list(order) for order in itertools.permutations(names)]

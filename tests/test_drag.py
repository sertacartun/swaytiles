"""A window dragged with the mouse stays where it is dropped, as in sway: a
swap keeps the layout in the new order, any other drop lets the workspace go."""

import pytest
from harness import expected


def opened(session, layout, count=4):
    return session(layout).fill(count)


def middle(s, title):
    rect = s.node(title)["rect"]
    return rect["x"] + rect["width"] // 2, rect["y"] + rect["height"] // 2


def test_a_window_dropped_left_of_the_master_stays_there(session):
    s = opened(session, "master")
    s.releasing = True
    rect = s.node("w1")["rect"]
    s.drag("w4", rect["x"] + 5, rect["y"] + rect["height"] // 2)
    assert s.shape() == "H[w4 w1 V[w2 w3]]"
    assert s.chosen() == "default"
    s.open("w5")
    # sway opens it next to the focused window, the one dropped.
    assert s.shape() == "H[w4 w5 w1 V[w2 w3]]"


def test_a_master_dropped_into_the_stack_stays_there(session):
    s = opened(session, "master")
    s.releasing = True
    rect = s.node("w3")["rect"]
    s.drag("w1", rect["x"] + rect["width"] // 2, rect["y"] + rect["height"] * 3 // 4)
    assert s.shape() == "V[w2 w3 w1 w4]"
    assert s.chosen() == "default"


@pytest.mark.parametrize(("place", "shape"), [("bottom", "V[V[w2 w3 w4] w1]"), ("top", "V[w1 V[w2 w3 w4]]")])
def test_a_window_dropped_on_the_edge_of_the_workspace_stays_there(session, place, shape):
    # sway splits the workspace and reports no event.
    s = opened(session, "master")
    s.releasing = True
    rect = s.node("w3")["rect"]
    s.drag("w1", rect["x"] + rect["width"] // 2, rect["y"] + (rect["height"] - 5 if place == "bottom" else 5))
    assert s.wait(lambda: s.chosen() == "default", 2)
    assert s.shape() == shape


def test_a_tab_dropped_on_the_tabs_stays_there(session):
    s = opened(session, "tabbed")
    s.releasing = True
    rect = s.node("w4")["rect"]
    s.drag("w1", rect["x"] + rect["width"] // 2, rect["y"] + rect["height"] - 5)
    assert s.chosen() == "default"
    assert "w1" in s.shape() and s.shape() != "T[w1 w2 w3 w4]"


def test_a_window_dropped_inside_the_stack_keeps_the_layout(session):
    s = opened(session, "master")
    rect = s.node("w2")["rect"]
    s.drag("w4", rect["x"] + rect["width"] // 2, rect["y"] + rect["height"] * 3 // 4)
    assert s.shape() == "H[w1 V[w2 w4 w3]]"
    assert s.chosen() == "master"
    s.open("w5")
    assert s.shape() == "H[w1 V[w2 w4 w3 w5]]"


@pytest.mark.parametrize("layout", ["master", "wide", "grid", "centered", "dwindle", "spiral"])
def test_windows_swapped_with_the_mouse_keep_the_layout(session, layout):
    # sway reports no event for a swap either.
    s = opened(session, layout)
    s.drag("w4", *middle(s, "w1"))
    swapped = expected(layout, 4, ["w4", "w2", "w3", "w1"])
    assert s.wait(lambda: s.shape() == swapped, 2), s.shape()
    s.open("w5")
    assert s.shape() == expected(layout, 5, ["w4", "w2", "w3", "w1", "w5"])
    assert s.chosen() == layout

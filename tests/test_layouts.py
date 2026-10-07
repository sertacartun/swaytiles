"""Every layout builds the tree it describes, while windows come, go and switch."""

import itertools

import pytest
from harness import expected

import swaytiles

TILING = [name for name, layout in swaytiles.LAYOUTS.items() if layout is not None and name != "float"]


def unwrapped(s, name="1"):
    """No container only holds the workspace's windows: the layout starts right under the workspace."""
    workspace = s.workspace(name)
    return workspace is not None and not swaytiles.wrapped(workspace) and workspace["layout"] not in swaytiles.TABBED


@pytest.mark.parametrize("layout", TILING)
def test_windows_opening_and_closing(session, layout):
    s = session(layout)
    for count in range(1, 6):
        s.open(f"w{count}")
        assert s.shape() == expected(layout, count), f"after opening w{count}"
        assert unwrapped(s), s.shape(exact=True)
    s.close("w3")
    assert s.shape() == expected(layout, 4, ["w1", "w2", "w4", "w5"])
    s.close("w1")
    assert s.shape() == expected(layout, 3, ["w2", "w4", "w5"])
    assert unwrapped(s), s.shape(exact=True)
    assert s.alive


def tour(names):
    edges = {name: [other for other in names if other != name] for name in names}
    path, stack = [], [names[0]]
    while stack:
        if edges[stack[-1]]:
            stack.append(edges[stack[-1]].pop())
        else:
            path.append(stack.pop())
    return path[::-1]


def test_switching_between_every_pair_of_layouts(session):
    s = session("master")
    for index in range(1, 5):
        s.open(f"w{index}")
    names = [*TILING, "float", "default"]
    route = tour(names)
    assert len(route) == len(names) * (len(names) - 1) + 1
    for previous, layout in itertools.pairwise(route):
        s.choose(layout)
        if layout == "float":
            assert s.shape() == "- F[w1 w2 w3 w4]", f"{previous} -> {layout}"
        elif layout != "default":
            assert s.shape() == expected(layout, 4), f"{previous} -> {layout}"
        if layout != "float":
            assert unwrapped(s), f"{previous} -> {layout}: {s.shape(exact=True)}"
        assert s.chosen() == layout
    assert s.alive


def test_the_menu_choice_is_remembered_per_workspace(session):
    s = session("master")
    s.open("w1")
    s.open("w2")
    s.choose("tabbed")
    s.command("workspace 2")
    s.open("x1")
    s.open("x2")
    assert s.shape("2") == "H[x1 x2]"
    s.choose("wide")
    assert s.shape("2") == "V[x1 x2]"
    assert s.shape("1") == "T[w1 w2]"
    assert (s.chosen("1"), s.chosen("2")) == ("tabbed", "wide")

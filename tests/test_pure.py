"""The layout arithmetic, without sway."""

import json
from pathlib import Path

import pytest

import sway_layout as sl


def test_master_layouts():
    assert sl.LAYOUTS["master"]([1, 2, 3]) == ("splith", [1, ("splitv", [2, 3])])
    assert sl.LAYOUTS["master-right"]([1, 2, 3]) == ("splith", [("splitv", [2, 3]), 1])
    assert sl.LAYOUTS["wide"]([1, 2]) == ("splitv", [1, ("splith", [2])])
    assert sl.LAYOUTS["tabbed-master"]([1]) == ("splith", [1, ("tabbed", [])])


def test_centered_falls_back_to_master_below_three_windows():
    assert sl.LAYOUTS["centered"]([1, 2]) == sl.LAYOUTS["master"]([1, 2])
    assert sl.LAYOUTS["centered"]([1, 2, 3, 4, 5]) == ("splith", [("splitv", [3, 5]), 1, ("splitv", [2, 4])])


def test_dwindle_and_spiral_turn():
    assert sl.trimmed(sl.LAYOUTS["dwindle"]([1, 2, 3])) == ("splith", [1, ("splitv", [2, ("splith", [3])])])
    assert sl.leaves(sl.LAYOUTS["spiral"]([1, 2, 3, 4, 5])) == [1, 2, 5, 4, 3]


@pytest.mark.parametrize("count", range(1, 10))
def test_grid_rows(count):
    rows = sl.LAYOUTS["grid"](list(range(count)))[1]
    assert sum(len(row[1]) for row in rows) == count
    assert max(len(row[1]) for row in rows) - min(len(row[1]) for row in rows) <= max(len(row[1]) for row in rows)


@pytest.mark.parametrize("name", [name for name, layout in sl.LAYOUTS.items() if layout])
@pytest.mark.parametrize("count", range(1, 8))
def test_every_layout_keeps_every_window_once(name, count):
    assert sorted(sl.leaves(sl.LAYOUTS[name](list(range(count))))) == list(range(count))


def test_tree_helpers():
    tree = ("splith", [("splith", [1, ("splitv", [2])]), ("tabbed", [3])])
    assert sl.leaves(tree) == [1, 2, 3]
    assert sl.without(tree, 2) == ("splith", [("splith", [1, ("splitv", [])]), ("tabbed", [3])])
    assert sl.normalize(sl.without(tree, 2)) == ("splith", [("splith", [1]), ("tabbed", [3])])
    assert sl.loose(tree) == ("splith", [("splith", [1, 2]), ("tabbed", [3])])
    assert sl.plain(tree) == ("splith", [1, 2, ("tabbed", [3])])
    assert sl.parent_of(tree, 2) == ("splitv", [2])
    assert sl.trimmed(("splith", [("splitv", [1, 2])])) == ("splitv", [1, 2])


def test_conforming_reads_the_logical_order():
    present = ("splith", [7, ("splitv", [5, 6])])
    assert sl.conforming(sl.LAYOUTS["master"], present) == [7, 5, 6]
    assert sl.conforming(sl.LAYOUTS["wide"], present) is None
    assert sl.conforming(sl.LAYOUTS["master"], ("splith", [7, ("splitv", [("splitv", [5]), 6])])) == [7, 5, 6]
    assert sl.conforming(sl.LAYOUTS["master"], ("splith", [7, 5, 6])) is None
    assert sl.conforming(sl.LAYOUTS["master"], None) is None


def test_the_neighbour_in_a_direction():
    def leaf(con, x, y, width, height):
        return {"id": con, "type": "con", "layout": "none", "nodes": [], "focus": [],
                "rect": {"x": x, "y": y, "width": width, "height": height}, "deco_rect": {"height": 0}}
    stack = {"id": 50, "type": "con", "layout": "splitv", "nodes": [leaf(2, 600, 0, 400, 300), leaf(3, 600, 300, 400, 300)], "focus": [3, 2]}
    workspace = {"id": 0, "type": "workspace", "layout": "splith", "nodes": [leaf(1, 0, 0, 600, 600), stack], "focus": [1, 50]}
    master, top, bottom = workspace["nodes"][0], *stack["nodes"]
    assert sl.beside(workspace, bottom, "left", [1, 2, 3]) == 1
    assert sl.beside(workspace, bottom, "up", [1, 2, 3]) == 2
    assert sl.beside(workspace, master, "right", [1, 2, 3]) == 2
    assert sl.beside(workspace, master, "left", [1, 2, 3]) is None
    stack["layout"] = "tabbed"
    assert sl.beside(workspace, top, "right", [1, 2, 3]) == 3
    assert sl.beside(workspace, top, "left", [1, 2, 3]) == 1
    assert sl.beside(workspace, master, "right", [1, 2, 3]) == 3


def test_the_session_is_validated(tmp_path):
    path = tmp_path / "session.json"
    path.write_text(json.dumps({"paused": ["1", 2, None], "built": {"1": ["splith", [1, ["tabbed", [2]]]], "2": ["x", [1]]},
                                "pinned": {"1": [5]}, "kept": {"7": "3", "x": "3", "8": 4}}))
    assert sl.load_session(path) == ({"1"}, {"1": ("splith", [1, ("tabbed", [2])])}, {7: "3"})
    path.write_text("[1, 2]")
    assert sl.load_session(path) == (set(), {}, {})


def test_the_menu_understands_every_kind_of_launcher(monkeypatch):
    names = list(sl.LAYOUTS)
    assert sl.picked_layout("3\n", names) == names[3]
    assert sl.picked_layout("99", names) is None
    assert sl.picked_layout("grid — Even grid  ●\n", names) == "grid"
    assert sl.picked_layout("", names) is None
    monkeypatch.setattr(sl.shutil, "which", lambda name: name if name in ("wofi", "bemenu") else None)
    assert sl.launcher(None, 13, 2)[0][0] == "wofi"
    assert sl.launcher("walker --dmenu", 13, 2) == (["walker", "--dmenu"], False)
    monkeypatch.setattr(sl.shutil, "which", lambda name: None)
    assert sl.launcher(None, 13, 2) == (None, False)


def test_the_shipped_config_is_the_generated_one():
    shipped = (Path(__file__).resolve().parent.parent / "contrib" / "sway.conf").read_text()
    assert shipped == sl.CONFIG.format(command="sway-layout")


def test_state_is_validated(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "STATE", tmp_path / "state.json")
    (tmp_path / "state.json").write_text(json.dumps({"layout": 3, "workspaces": {"1": "grid", "2": "nope", "3": None}}))
    assert sl.load_state()["layout"] == "sway"
    assert sl.load_state()["workspaces"] == {"1": "grid"}
    sl.save_state({"layout": "grid", "workspaces": {}})
    assert json.loads((tmp_path / "state.json").read_text())["layout"] == "grid"
    assert list(tmp_path.iterdir()) == [tmp_path / "state.json"]

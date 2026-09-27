"""The layout arithmetic, without sway."""

import json

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
    workspace = {"id": 0, "type": "workspace", "layout": "splith", "nodes": [
        {"id": 7, "type": "con", "layout": "none", "nodes": []},
        {"id": 50, "type": "con", "layout": "splitv", "nodes": [
            {"id": 5, "type": "con", "layout": "none", "nodes": []},
            {"id": 6, "type": "con", "layout": "none", "nodes": []}]}]}
    assert sl.conforming(sl.LAYOUTS["master"], workspace, [5, 6, 7], set()) == [7, 5, 6]
    assert sl.conforming(sl.LAYOUTS["wide"], workspace, [5, 6, 7], set()) is None
    assert sl.conforming(sl.LAYOUTS["master"], workspace, [5, 6, 7], {6}) == [7, 5]


def test_state_is_validated(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "STATE", tmp_path / "state.json")
    (tmp_path / "state.json").write_text(json.dumps({"layout": 3, "workspaces": {"1": "grid", "2": "nope", "3": None}}))
    assert sl.load_state()["layout"] == "sway"
    assert sl.load_state()["workspaces"] == {"1": "grid"}
    sl.save_state({"layout": "grid", "workspaces": {}})
    assert json.loads((tmp_path / "state.json").read_text())["layout"] == "grid"
    assert list(tmp_path.iterdir()) == [tmp_path / "state.json"]

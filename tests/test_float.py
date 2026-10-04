"""Float workspaces cascade every window inside the output."""

MOVES = ("bindsym Mod4+F1 nop layout move left\nbindsym Mod4+F6 nop layout move number 3\n"
         "bindsym Mod4+F7 nop layout move number 1\n")


def floats(s, name="1"):
    ws = s.workspace(name)
    return ws["rect"], {node["name"]: node for node in ws["floating_nodes"]}


def inside(area, node):
    rect = node["rect"]
    return (area["x"] <= rect["x"] and rect["x"] + rect["width"] <= area["x"] + area["width"]
            and area["y"] <= rect["y"] and rect["y"] + rect["height"] <= area["y"] + area["height"])


def test_windows_cascade_inside_the_output(session):
    s = session("float")
    for index in range(1, 7):
        s.open(f"w{index}")
    area, nodes = floats(s)
    assert list(nodes) == [f"w{index}" for index in range(1, 7)]
    assert all(inside(area, node) for node in nodes.values())
    origins = {(node["rect"]["x"], node["rect"]["y"]) for node in nodes.values()}
    assert len(origins) == 6
    assert all(node.get("opacity", 1) == 1 for node in nodes.values())
    sizes = {(node["rect"]["width"], node["rect"]["height"] + node["deco_rect"]["height"]) for node in nodes.values()}
    assert sizes == {(area["width"] * 3 // 5, area["height"] * 3 // 5)}


def test_a_new_window_never_covers_another_exactly(session):
    s = session("float")
    for index in range(1, 4):
        s.open(f"w{index}")
    s.close("w2")
    s.open("w4")
    area, nodes = floats(s)
    origins = [(node["rect"]["x"], node["rect"]["y"]) for node in nodes.values()]
    assert len(set(origins)) == len(origins) == 3
    assert all(inside(area, node) for node in nodes.values())


def test_a_tiled_window_moved_in_floats_and_fits(session):
    s = session("master", workspaces={"3": "float"}, config=MOVES)
    for index in range(1, 4):
        s.open(f"w{index}")
    s.command("workspace 3")
    s.open("f1")
    s.command("workspace 1")
    s.focus("w2")
    s.key("F6")
    assert s.shape() == "H[w1 w3]"
    s.command("workspace 3")
    area, nodes = floats(s, "3")
    assert set(nodes) == {"f1", "w2"}
    assert all(inside(area, node) for node in nodes.values())


def test_a_floating_window_moved_to_a_tiled_workspace_tiles(session):
    s = session("float", workspaces={"1": "float", "2": "master"}, config=MOVES.replace("number 1", "number 2"))
    s.command("workspace 2")
    s.open("t1")
    s.command("workspace 1")
    s.open("w1")
    s.open("w2")
    s.focus("w2")
    s.key("F7")
    assert s.shape("2") == "H[t1 w2]"
    assert s.shape("1") == "- F[w1]"


def test_leaving_float_restores_the_order(session):
    s = session("master")
    for index in range(1, 5):
        s.open(f"w{index}")
    s.choose("float")
    assert s.shape() == "- F[w1 w2 w3 w4]"
    s.open("w5")
    s.choose("master")
    assert s.shape() == "H[w1 V[w2 w3 w4 w5]]"


def test_a_window_floated_by_hand_on_a_float_workspace_stays_where_it_is(session):
    s = session("float")
    s.open("w1")
    s.open("w2")
    s.command("[title=^w2$] move absolute position 10 px 10 px")
    s.open("w3")
    node = floats(s)[1]["w2"]
    assert (node["rect"]["x"], node["rect"]["y"] - node["deco_rect"]["height"]) == (10, 10)


def test_a_window_tiled_by_hand_stays_tiled(session):
    s = session("float", workspaces={"2": "master"}, config=MOVES + "bindsym Mod4+F8 floating toggle\n")
    s.open("w1")
    s.open("w2")
    s.focus("w1")
    s.key("F8")
    assert s.shape() == "w1 F[w2]"
    assert s.node("w1").get("opacity", 1) == 1
    s.open("w3")
    assert s.shape() == "w1 F[w2 w3]"
    s.stop_daemon()
    s.start_daemon()
    s.open("w4")
    assert s.shape() == "w1 F[w2 w3 w4]"
    s.command("workspace 2")
    s.open("x1")
    s.command("[title=^x1$] move container to workspace 1")
    s.command("workspace 1")
    assert s.shape() == "w1 F[w2 w3 w4 x1]"
    s.focus("w1")
    s.key("F8")
    assert s.shape().startswith("- F[")
    s.focus("w2")
    s.key("F8")
    assert s.shape() == "w2 F[w3 w4 x1 w1]"
    s.choose("float")
    area, nodes = floats(s)
    assert s.shape().startswith("- F[") and set(nodes) == {"w1", "w2", "w3", "w4", "x1"}
    assert all(inside(area, node) and node.get("opacity", 1) == 1 for node in nodes.values())

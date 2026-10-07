"""Two outputs of different sizes and scales, like a laptop with a monitor."""

import subprocess
import time

import pytest

CONFIG = ("output HEADLESS-1 resolution 1920x1200 position 0 480 scale 1.25\n"
          "output HEADLESS-2 resolution 2560x1440 position 1536 0\n"
          "workspace 10 output HEADLESS-1\nworkspace 1 output HEADLESS-2\nworkspace 3 output HEADLESS-2\n"
          "workspace 4 output HEADLESS-1\n"
          "bindsym Mod4+F1 nop layout move left\nbindsym Mod4+F2 nop layout move right\n"
          "bindsym Mod4+F3 nop layout move number 10\nbindsym Mod4+F6 nop layout move number 3\n"
          "bindsym Mod4+F4 focus left\nbindsym Mod4+F5 focus right\n")
LAYOUTS = {"1": "master", "10": "stacked-master", "3": "float", "4": "tabbed"}


def floats_fit(s, name):
    ws = s.workspace(name)
    output = next(o for o in s.tree()["nodes"] if any(w["id"] == ws["id"] for w in o["nodes"]))
    area = output["rect"]
    return all(area["x"] <= n["rect"]["x"] and n["rect"]["x"] + n["rect"]["width"] <= area["x"] + area["width"]
               and area["y"] <= n["rect"]["y"] and n["rect"]["y"] + n["rect"]["height"] <= area["y"] + area["height"]
               for n in ws["floating_nodes"])


def test_two_outputs(session):
    s = session("master", workspaces=LAYOUTS, config=CONFIG, outputs=2)
    s.command("workspace 1")
    for title in ("a1", "a2", "a3"):
        s.open(title)
    s.command("workspace 10")
    s.open("b1")
    s.open("b2")
    assert (s.shape("1"), s.shape("10")) == ("H[a1 V[a2 a3]]", "H[b1 S[b2]]")
    s.focus("a1")
    s.open("a4")
    s.focus("b1")
    s.open("b3")
    assert (s.shape("1"), s.shape("10")) == ("H[a1 V[a2 a3 a4]]", "H[b1 S[b2 b3]]")
    s.focus("a4")
    s.key("F3")
    assert (s.shape("1"), s.shape("10")) == ("H[a1 V[a2 a3]]", "H[b1 S[b2 b3 a4]]")
    s.focus("a4")
    s.key("F1")
    s.key("F1")
    assert s.shape("10") == "H[a4 S[b2 b3 b1]]"
    s.open("b4")
    assert s.shape("10") == "H[a4 S[b2 b3 b1 b4]]"
    s.focus("b2")
    s.key("F2")
    assert (s.shape("1"), s.shape("10")) == ("H[b2 V[a1 a2 a3]]", "H[a4 S[b3 b1 b4]]")
    s.focus("a2")
    s.key("F6")
    s.command("workspace 3")
    s.open("c1")
    assert s.shape("3") == "- F[a2 c1]" and floats_fit(s, "3")
    s.focus("c1")
    s.key("F1")
    assert s.shape("10") == "H[a4 S[b3 b1 b4 c1]]"
    s.command("workspace 3")
    s.command("move workspace to output left")
    assert floats_fit(s, "3")
    s.command("workspace 3")
    s.open("c2")
    assert s.shape("3") == "- F[a2 c2]" and floats_fit(s, "3")
    s.command("workspace 1")
    s.command("output HEADLESS-2 unplug")
    assert s.alive
    s.command("workspace 1")
    s.open("a5")
    assert s.shape("1") == "H[b2 V[a1 a3 a5]]"
    s.command("create_output")
    s.command("workspace 4")
    s.open("d1")
    s.command("rename workspace 4 to four")
    s.open("d2")
    s.open("d3")
    assert s.shape("four") == "T[d1 d2 d3]"
    assert s.chosen("four") == "tabbed"
    assert s.alive


def test_focus_leaves_a_float_workspace_at_its_edge(session):
    s = session("master", workspaces=LAYOUTS, config=CONFIG, outputs=2)
    s.command("workspace 10")
    s.open("b1")
    s.command("workspace 3")
    s.open("c1")
    s.open("c2")
    s.key("F4")
    assert s.focused() == "c1"
    s.key("F4")
    assert s.focused() == "b1"
    s.key("F5")
    assert s.focused() == "c1"
    s.key("F5")
    assert s.focused() == "c2"
    s.key("F5")
    assert s.focused() == "c2"


def test_a_window_carried_to_a_float_workspace_stays_where_it_lands(session):
    s = session("master", workspaces=LAYOUTS, config=CONFIG, outputs=2)
    s.command("workspace 3")
    s.open("c1")
    s.command("workspace 10")
    s.open("b1")
    subprocess.run(["wtype", "-M", "logo", "-k", "F2", "-m", "logo"], env=s.env, check=True)
    places, deadline = [], time.monotonic() + 1.5
    while time.monotonic() < deadline:
        node = s.node("b1")
        if node and node["type"] == "floating_con" and (not places or places[-1] != node["rect"]):
            places.append(node["rect"])
        time.sleep(0.01)
    assert len(places) == 1, places
    assert s.shape("3") == "- F[c1 b1]" and floats_fit(s, "3")


def test_a_window_carried_to_another_output_enters_at_the_near_edge(session):
    s = session("master", workspaces={"1": "master", "10": "master"}, config=CONFIG, outputs=2)
    s.command("workspace 1")
    s.open("a1")
    s.command("workspace 10")
    for title in ("b1", "b2", "b3"):
        s.open(title)
    assert (s.shape("10"), s.shape("1")) == ("H[b1 V[b2 b3]]", "a1")
    s.focus("b3")
    s.key("F2")
    assert (s.shape("10"), s.shape("1")) == ("H[b1 b2]", "H[b3 a1]")
    assert s.focused() == "b3"
    s.key("F1")
    assert (s.shape("10"), s.shape("1")) == ("H[b1 V[b2 b3]]", "a1")
    assert s.focused() == "b3"
    s.key("F1")
    s.key("F1")
    assert (s.shape("10"), s.shape("1")) == ("H[b3 V[b2 b1]]", "a1")
    s.focus("a1")
    s.key("F1")
    assert (s.shape("10"), s.shape("1")) == ("H[b3 V[b2 b1 a1]]", "-")
    assert s.focused() == "a1"


def test_a_window_moved_to_the_next_output_lands_in_its_place(session):
    s = session("master", workspaces={"1": "tabbed-master", "10": "default"}, config=CONFIG, outputs=2)
    s.command("workspace 1")
    s.open("a1")
    s.command("workspace 10")
    s.open("b1")
    s.open("b2")
    s.key("F2")
    assert (s.shape("1"), s.shape("10")) == ("H[b2 T[a1]]", "b1")
    assert s.focused() == "b2"
    s.key("F1")
    assert (s.shape("1"), s.shape("10")) == ("a1", "H[b1 b2]")
    s.focus("b1")
    s.key("F2")
    s.key("F2")
    assert (s.shape("1"), s.shape("10")) == ("H[b1 T[a1]]", "b2")


WRAPS = {
    "flat": "",
    "wrapped": "[title=^a1$] layout splitv; [title=^a1$] layout splith",
    "single": "[title=^a1$] split v",
    "tabbed": "[title=^a1$] layout tabbed",
    "vertical": "[title=^a1$] layout splitv",
    "nested": "[title=^a1$] layout splitv; [title=^a1$] layout splith; [title=^a2$] split v; [title=^a1$] move right; [title=^a1$] move left",
}


def wrapped(session, shape):
    s = session("default", workspaces={"1": "default", "10": "default"}, config=CONFIG, outputs=2)
    s.command("workspace 10")
    s.open("b1")
    s.command("workspace 1")
    s.open("a1")
    s.open("a2")
    if WRAPS[shape]:
        s.command(WRAPS[shape])
    s.focus("a1")
    return s


@pytest.mark.parametrize("shape", WRAPS)
def test_presses_counts_the_moves_sway_needs_to_leave_the_output(session, shape):
    import swaytiles
    s = wrapped(session, shape)
    predicted = swaytiles.presses(s.workspace("1"), s.node("a1")["id"], "left")
    s.stop_daemon()

    def left():
        s.command("[title=^a1$] move left")
        return "a1" in s.shape("10")
    assert predicted == next(count for count in range(1, 6) if left())


def test_a_window_in_a_wrapper_leaves_the_output_in_one_press(session):
    s = wrapped(session, "wrapped")
    assert s.shape("1", exact=True) == "H[H[a1 a2]]"
    s.key("F1")
    assert (s.shape("1"), s.shape("10")) == ("a2", "H[b1 a1]")
    assert s.focused() == "a1"


def test_a_window_left_alone_in_its_tabs_gets_its_border_back(session):
    s = session("master", workspaces={"1": "tabbed-master", "10": "default"},
                config="default_border pixel 5\n" + CONFIG, outputs=2)
    s.command("workspace 1")
    s.open("a1")
    s.command("workspace 10")
    s.open("b1")
    s.open("b2")
    for _ in range(2):
        s.key("F2")
        assert s.shape("1") == "H[b2 T[a1]]"
        s.key("F1")
        assert s.shape("1") == "a1"
        assert s.node("a1")["window_rect"]["y"] == 5


def test_the_workspace_a_window_leaves_is_put_in_order_in_the_same_step(session):
    s = session("master", workspaces=LAYOUTS, config=CONFIG, outputs=2)
    s.command("workspace 10")
    s.open("b1")
    s.command("workspace 1")
    for title in ("a1", "a2", "a3"):
        s.open(title)
    s.focus("a1")
    drawn = s.drawn(lambda: s.key("F1"))
    assert len(drawn) == 1, drawn
    assert (s.shape("1", exact=True), s.shape("10")) == ("H[a2 V[a3]]", "H[b1 S[a1]]")

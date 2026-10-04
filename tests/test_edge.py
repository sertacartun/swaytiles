"""The situations that broke other layout daemons."""

import signal
import threading

import pytest

CONFIG = ("for_window [title=^dlg] floating enable\n"
          "for_window [title=^bg] move container to workspace 3\n"
          "no_focus [title=^nf]\n"
          "bindsym Mod4+F5 nop layout move number 2\n")


def opened(s, count, prefix="w"):
    for index in range(1, count + 1):
        s.open(f"{prefix}{index}")


def test_a_fullscreen_stack_window_survives_a_new_window(session):
    s = session("master")
    opened(s, 3)
    s.command("[title=^w2$] fullscreen enable")
    s.open("w4")
    assert s.shape() == "H[w1 V[w2* w3 w4]]"
    assert s.focused() == "w2"
    s.command("[title=^w2$] fullscreen disable")
    assert s.shape() == "H[w1 V[w2 w3 w4]]"


@pytest.mark.parametrize("layout", ["centered", "dwindle", "spiral", "grid", "master"])
def test_fullscreen_is_never_dropped_by_a_rearrangement(session, layout):
    s = session(layout)
    opened(s, 2)
    s.command("[title=^w1$] fullscreen enable")
    s.open("w3")
    assert s.fullscreen("w1")
    s.close("w2")
    assert s.fullscreen("w1")
    s.command("[title=^w1$] fullscreen disable")
    from harness import expected
    assert s.shape() == expected(layout, 2, ["w1", "w3"])


def test_fullscreen_survives_a_menu_switch(session):
    s = session("master")
    opened(s, 3)
    s.command("[title=^w1$] fullscreen enable")
    s.choose("centered")
    assert s.fullscreen("w1")
    s.command("[title=^w1$] fullscreen disable")
    assert s.shape() == "H[w3 w1 w2]"


def test_the_master_width_set_by_hand_is_kept(session):
    s = session("master")
    opened(s, 3)
    s.command("[title=^w1$] resize set width 70 ppt")
    s.open("w4")
    assert s.width("w1") == 0.7
    s.close("w4")
    assert s.width("w1") == 0.7
    s.close("w1")
    assert s.width("w2") == 0.7
    s.choose("centered")
    s.open("w5")
    assert s.width("w2") == 0.7
    s.choose("master")
    assert s.width("w2") == 0.7


def test_equal_widths_stay_equal_across_layouts(session):
    s = session("master")
    opened(s, 3)
    s.choose("centered")
    assert s.width("w1") == 0.33
    s.choose("master")
    assert s.width("w1") == 0.5


def test_a_window_floated_for_a_moment_goes_back_to_its_place(session):
    s = session("master")
    opened(s, 4)
    s.command("[title=^w1$] floating enable")
    assert s.shape() == "H[w2 V[w3 w4]] F[w1]"
    s.command("[title=^w1$] floating disable")
    assert s.shape() == "H[w1 V[w2 w3 w4]]"
    s.command("[title=^w3$] floating enable")
    s.command("[title=^w3$] floating disable")
    assert s.shape() == "H[w1 V[w2 w3 w4]]"


def test_dialogs_float_and_join_the_stack_when_tiled(session):
    s = session("master", config=CONFIG)
    opened(s, 3)
    s.open("dlg")
    assert s.shape() == "H[w1 V[w2 w3]] F[dlg]"
    s.open("w4")
    assert s.shape() == "H[w1 V[w2 w3 w4]] F[dlg]"
    s.focus("dlg")
    s.open("w5")
    assert s.shape() == "H[w1 V[w2 w3 w4 w5]] F[dlg]"
    s.command("[title=^dlg$] floating disable")
    assert s.shape() == "H[w1 V[w2 w3 w4 w5 dlg]]"


def test_the_scratchpad_takes_and_gives_back_windows(session):
    s = session("master")
    opened(s, 4)
    s.command("[title=^w1$] move scratchpad")
    assert s.shape() == "H[w2 V[w3 w4]]"
    s.open("w5")
    assert s.shape() == "H[w2 V[w3 w4 w5]]"
    s.command("[title=^w1$] scratchpad show")
    assert s.shape() == "H[w2 V[w3 w4 w5]] F[w1]"
    s.command("[title=^w1$] floating disable")
    assert s.shape() == "H[w2 V[w3 w4 w5 w1]]"


def test_the_last_window_sent_to_the_scratchpad_does_not_pull_new_ones_in(session):
    s = session("master")
    opened(s, 3)
    s.command("[title=^w3$] move scratchpad")
    s.open("w4")
    assert s.shape() == "H[w1 V[w2 w4]]"


def shapes_during(s, action):
    seen, done = [s.shape()], threading.Event()

    def watch():
        while not done.is_set():
            if (shape := s.shape()) != seen[-1]:
                seen.append(shape)
    watcher = threading.Thread(target=watch)
    watcher.start()
    action()
    s.settle()
    done.set()
    watcher.join()
    return seen


@pytest.mark.parametrize(("layout", "before", "after"), [
    ("master", "H[w1 V[w4 w5 w6]]", "H[w1 V[w4 w5 w6 w2]]"),
    ("grid", "V[H[w1 w4] H[w5 w6]]", "V[H[w1 w4 w5] H[w6 w2]]"),
    ("centered", "H[w5 w1 V[w4 w6]]", "H[V[w5 w2] w1 V[w4 w6]]"),
    ("tabbed-master", "H[w1 T[w4 w5 w6]]", "H[w1 T[w4 w5 w6 w2]]"),
])
def test_a_hidden_window_comes_back_in_one_step(session, layout, before, after):
    s = session(layout)
    opened(s, 4)
    s.command("[title=^w2$] move scratchpad")
    s.open("w5")
    s.close("w3")
    s.open("w6")
    s.focus("w1")
    con = s.node("w2")["id"]
    assert shapes_during(s, lambda: s.run("show", str(con))) == [before, after]
    assert s.focused() == "w2"


def test_show_brings_back_the_last_hidden_window(session):
    s = session("master", config="bindsym Mod4+F6 nop layout show\n")
    opened(s, 4)
    s.command("[title=^w2$] move scratchpad")
    s.command("[title=^w4$] move scratchpad")
    s.key("F6")
    assert s.shape() == "H[w1 V[w3 w4]]"
    s.key("F6")
    assert s.shape() == "H[w1 V[w3 w4 w2]]"
    s.key("F6")
    assert s.shape() == "H[w1 V[w3 w4 w2]]"


def test_a_hidden_window_comes_back_to_a_float_slot(session):
    s = session("float")
    opened(s, 3)
    s.command("[title=^w2$] move scratchpad")
    con = s.node("w2")["id"]
    s.run("show", str(con))
    s.settle()
    boxes = [(node["rect"]["x"], node["rect"]["y"]) for node in s.workspace()["floating_nodes"]]
    assert s.shape() == "- F[w1 w3 w2]"
    assert len(set(boxes)) == 3


def test_the_master_sent_to_another_workspace(session):
    s = session("master", config=CONFIG)
    opened(s, 3)
    s.focus("w1")
    s.key("F5")
    assert s.shape() == "H[w2 w3]"
    assert s.shape("2") == "w1"
    s.command("workspace 2")
    s.open("w4")
    assert s.shape("2") == "H[w1 w4]"


def test_a_native_move_to_a_workspace_joins_its_stack(session):
    s = session("master")
    opened(s, 3)
    s.command("workspace 2")
    s.open("x1")
    s.open("x2")
    s.command("[title=^x1$] move container to workspace 1")
    assert s.shape() == "H[w1 V[w2 w3 x1]]"
    assert s.shape("2") == "x2"


def test_many_windows_opened_and_closed_quickly(session):
    s = session("master")
    for index in range(1, 9):
        s.open(f"w{index}", settle=False)
    s.settle()
    assert s.shape() == "H[w1 V[w2 w3 w4 w5 w6 w7 w8]]"
    for index in (2, 4, 6):
        s.close(f"w{index}", settle=False)
    s.settle()
    assert s.shape() == "H[w1 V[w3 w5 w7 w8]]"


def test_windows_sent_by_rule_to_a_hidden_workspace(session):
    s = session("master", config=CONFIG)
    s.open("w1")
    opened(s, 3, "bg")
    assert s.shape("3") == "H[bg1 V[bg2 bg3]]"
    assert s.shape() == "w1"


def test_config_reloads(session):
    s = session("master")
    opened(s, 3)
    s.command("reload")
    assert s.shape() == "H[w1 V[w2 w3]]"
    s.open("w4")
    assert s.shape() == "H[w1 V[w2 w3 w4]]"
    s.command("reload")
    s.command("reload")
    s.open("w5")
    assert s.shape() == "H[w1 V[w2 w3 w4 w5]]"


def test_windows_that_do_not_take_focus(session):
    s = session("master", config=CONFIG)
    opened(s, 3)
    s.focus("w1")
    s.open("nf1")
    assert s.shape() == "H[w1 V[w2 w3 nf1]]"
    s.open("w4")
    assert s.shape() == "H[w1 V[w2 w3 nf1 w4]]"


def test_the_master_closing_promotes_the_next_window(session):
    s = session("master")
    opened(s, 4)
    s.close("w1")
    assert s.shape() == "H[w2 V[w3 w4]]"
    s.close("w3")
    assert s.shape() == "H[w2 w4]"


def test_opening_next_to_a_hovered_window(session):
    s = session("master")
    opened(s, 3)
    s.focus("w1")
    s.open("w4")
    assert s.shape() == "H[w1 V[w2 w3 w4]]"
    s.focus("w2")
    s.open("w5")
    assert s.shape() == "H[w1 V[w2 w3 w4 w5]]"


def test_a_crash_leaves_nothing_broken(session):
    s = session("master")
    opened(s, 2)
    s.stop_daemon(signal.SIGKILL)
    s.start_daemon()
    s.focus("w1")
    s.open("w3")
    assert s.shape() == "H[w1 V[w2 w3]]"


def test_a_keyboard_resize_is_remembered(session):
    s = session("master", config="bindsym Mod4+F8 resize grow width 320 px\n")
    opened(s, 3)
    s.focus("w1")
    s.key("F8")
    grown = s.width("w1")
    assert grown > 0.6
    s.close("w1")
    assert s.width("w2") == grown


def test_the_height_of_the_master_in_wide(session):
    s = session("wide")
    opened(s, 3)
    s.command("[title=^w1$] resize set height 65 ppt")
    s.close("w1")
    node, ws = s.node("w2"), s.workspace()
    assert round(node["rect"]["height"] / ws["rect"]["height"], 1) == 0.6


def test_a_resize_without_any_event_is_still_kept(session):
    s = session("centered")
    opened(s, 2)
    s.command("[title=^w1$] resize set width 65 ppt")
    s.open("w3")
    assert s.width("w1") == 0.65
    s.command("[title=^w1$] resize set width 55 ppt")
    s.choose("master")
    assert s.width("w1") == 0.55

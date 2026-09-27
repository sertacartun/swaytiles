"""A window placed by hand stays where it was put until the layout is chosen again."""

import signal

import pytest

MOVES = ("bindsym Mod4+F1 nop layout move left\nbindsym Mod4+F2 nop layout move right\n"
         "bindsym Mod4+F3 nop layout move up\nbindsym Mod4+F4 nop layout move down\n")
KEYS = {"left": "F1", "right": "F2", "up": "F3", "down": "F4"}

PINNED = {
    "master:w4:left": [
        "H[w1 V[w2 w3 w4]]", "H[w1 w4 V[w2 w3]]", "H[w1 w4 V[w2 w3 w5]]", "H[w1 w4 V[w2 w3 w5 w6]]",
        "H[w1 w4 V[w3 w5 w6]]", "H[w1 V[w3 w4 w5 w6]]", "H[w1 V[w3 w4 w5 w6 w7]]"],
    "stacked-master:w4:left": [
        "H[w1 S[w2 w3 w4]]", "H[w1 w4 S[w2 w3]]", "H[w1 w4 S[w2 w3 w5]]", "H[w1 w4 S[w2 w3 w5 w6]]",
        "H[w1 w4 S[w3 w5 w6]]", "H[w1 S[w3 w4 w5 w6]]", "H[w1 S[w3 w4 w5 w6 w7]]"],
    "tabbed-master:w2:left": [
        "H[w1 T[w2 w3 w4]]", "H[w1 w2 T[w3 w4]]", "H[w1 w2 T[w3 w4 w5]]", "H[w1 w2 T[w3 w4 w5 w6]]",
        "H[w1 T[w3 w4 w5 w6]]", "H[w1 T[w3 w4 w5 w6]]", "H[w1 T[w3 w4 w5 w6 w7]]"],
    "master-right:w4:right": [
        "H[V[w2 w3 w4] w1]", "H[V[w2 w3] w4 w1]", "H[V[w2 w3 w5] w4 w1]", "H[V[w2 w3 w5 w6] w4 w1]",
        "H[V[w3 w5 w6] w4 w1]", "H[V[w3 w4 w5 w6] w1]", "H[V[w3 w4 w5 w6 w7] w1]"],
    "wide:w4:up": [
        "V[w1 H[w2 w3 w4]]", "V[w1 w4 H[w2 w3]]", "V[w1 w4 H[w2 w3 w5]]", "V[w1 w4 H[w2 w3 w5 w6]]",
        "V[w1 w4 H[w3 w5 w6]]", "V[w1 H[w3 w4 w5 w6]]", "V[w1 H[w3 w4 w5 w6 w7]]"],
    "centered:w3:up": [
        "H[w3 w1 V[w2 w4]]", "V[w3 H[w1 V[w2 w4]]]", "V[w3 H[w1 V[w2 w4 w5]]]", "V[w3 H[w1 V[w2 w4 w5 w6]]]",
        "V[w3 H[w1 V[w4 w5 w6]]]", "H[V[w4 w6] w1 V[w3 w5]]", "H[V[w4 w6] w1 V[w3 w5 w7]]"],
    "dwindle:w3:up": [
        "H[w1 V[w2 H[w3 w4]]]", "H[w1 V[w2 w3 w4]]", "H[w1 V[w2 w3 w4 w5]]", "H[w1 V[w2 w3 w4 w5 w6]]",
        "H[w1 V[w3 w4 w5 w6]]", "H[w1 V[w3 H[w4 V[w5 w6]]]]", "H[w1 V[w3 H[w4 V[w5 H[w6 w7]]]]]"],
    "spiral:w4:up": [
        "H[w1 V[w2 H[w4 w3]]]", "H[w1 V[w2 w4 w3]]", "H[w1 V[w2 w4 H[w3 w5]]]", "H[w1 V[w2 w4 H[w3 w5 w6]]]",
        "H[w1 V[w4 H[w3 w5 w6]]]", "H[w1 V[w3 H[V[w6 w5] w4]]]", "H[w1 V[w3 H[V[H[w6 w7] w5] w4]]]"],
    "grid:w4:up": [
        "V[H[w1 w2] H[w3 w4]]", "V[H[w1 w2 w4] w3]", "V[H[w1 w2 w4] H[w3 w5]]", "V[H[w1 w2 w4] H[w3 w5 w6]]",
        "V[H[w1 w4 w3] H[w5 w6]]", "V[H[w1 w4 w3] H[w5 w6]]", "V[H[w1 w4 w3] H[w5 w6 w7]]"],
    "tabbed:w3:down": [
        "T[w1 w2 w3 w4]", "V[T[w1 w2 w4] w3]", "V[T[w1 w2 w4 w5] w3]", "V[T[w1 w2 w4 w5 w6] w3]",
        "V[T[w1 w4 w5 w6] w3]", "T[w1 w3 w4 w5 w6]", "T[w1 w3 w4 w5 w6 w7]"],
    "stacking:w2:right": [
        "S[w1 w2 w3 w4]", "H[S[w1 w3 w4] w2]", "H[S[w1 w3 w4 w5] w2]", "H[S[w1 w3 w4 w5 w6] w2]",
        "S[w1 w3 w4 w5 w6]", "S[w1 w3 w4 w5 w6]", "S[w1 w3 w4 w5 w6 w7]"],
    "master:w3:up": [
        "H[w1 V[w2 w3 w4]]", "H[w1 V[w3 w2 w4]]", "H[w1 V[w3 w2 w4 w5]]", "H[w1 V[w3 w2 w4 w5 w6]]",
        "H[w1 V[w3 w4 w5 w6]]", "H[w1 V[w3 w4 w5 w6]]", "H[w1 V[w3 w4 w5 w6 w7]]"],
}


@pytest.mark.parametrize("case", PINNED)
def test_a_window_moved_by_hand_stays_put(session, case):
    layout, mover, direction = case.split(":")
    s = session(layout, config=MOVES)
    for index in range(1, 5):
        s.open(f"w{index}")
    seen = [s.shape()]
    s.focus(mover)
    s.key(KEYS[direction])
    seen.append(s.shape())
    s.focus("w1")
    s.open("w5")
    seen.append(s.shape())
    s.open("w6")
    seen.append(s.shape())
    s.close("w2")
    seen.append(s.shape())
    s.choose(layout)
    seen.append(s.shape())
    s.open("w7")
    seen.append(s.shape())
    assert seen == PINNED[case]


def test_a_restart_keeps_a_manual_placement(session):
    s = session("master", config=MOVES)
    for index in range(1, 5):
        s.open(f"w{index}")
    s.focus("w3")
    s.key("F1")
    s.key("F1")
    assert s.shape() == "H[w3 w1 V[w2 w4]]"
    s.stop_daemon()
    s.start_daemon()
    assert s.shape() == "H[w3 w1 V[w2 w4]]"
    s.open("w5")
    assert s.shape() == "H[w3 w1 V[w2 w4 w5]]"
    s.stop_daemon(signal.SIGKILL)
    s.start_daemon()
    s.open("w6")
    assert s.shape() == "H[w3 w1 V[w2 w4 w5 w6]]"


def test_a_stack_made_tabbed_by_hand_stays_tabbed(session):
    s = session("master")
    for index in range(1, 4):
        s.open(f"w{index}")
    s.focus("w3")
    s.command("layout tabbed")
    assert s.shape() == "H[w1 T[w2 w3]]"
    s.open("w4")
    assert s.shape() == "H[w1 T[w2 w3 w4]]"
    assert s.chosen() == "tabbed-master"
    s.close("w4")
    assert s.shape() == "H[w1 T[w2 w3]]"


def test_a_split_direction_changed_by_hand_is_kept(session):
    s = session("master")
    for index in range(1, 4):
        s.open(f"w{index}")
    s.focus("w1")
    s.command("layout toggle split")
    assert s.shape() == "V[w1 V[w2 w3]]"
    s.open("w4")
    assert s.shape() == "V[w1 V[w2 w3 w4]]"
    s.choose("master")
    assert s.shape() == "H[w1 V[w2 w3 w4]]"
    s.open("w5")
    assert s.shape() == "H[w1 V[w2 w3 w4 w5]]"


def test_a_native_move_counts_as_manual(session):
    s = session("master")
    for index in range(1, 4):
        s.open(f"w{index}")
    s.command("[title=^w3$] move left")
    assert s.shape() == "H[w1 w3 V[w2]]"
    s.open("w4")
    assert s.shape() == "H[w1 w3 V[w2 w4]]"

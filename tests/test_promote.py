"""nop layout master swaps the focused window with the master."""

import pytest

KEY = "bindsym Mod4+F9 nop layout master\nbindsym Mod4+F1 nop layout move left\n"


def started(session, layout, count=4, **options):
    return session(layout, config=KEY, **options).fill(count)


def test_a_stack_window_becomes_master(session):
    s = started(session, "master")
    s.focus("w3")
    s.key("F9")
    assert s.shape() == "H[w3 V[w2 w1 w4]]"
    assert s.focused() == "w3"
    s.open("w5")
    assert s.shape() == "H[w3 V[w2 w1 w4 w5]]"


def test_the_master_swaps_with_the_top_of_the_stack(session):
    s = started(session, "master")
    s.focus("w1")
    s.key("F9")
    assert s.shape() == "H[w2 V[w1 w3 w4]]"


def test_the_master_keeps_its_size(session):
    s = started(session, "master")
    s.command("[title=^w1$] resize set width 70 ppt")
    s.focus("w4")
    s.key("F9")
    assert s.width("w4") == 0.7


@pytest.mark.parametrize(("layout", "before", "after"), [
    ("tabbed-master", "H[w1 T[w2 w3 w4]]", "H[w3 T[w2 w1 w4]]"),
    ("wide", "V[w1 H[w2 w3 w4]]", "V[w3 H[w2 w1 w4]]"),
    ("centered", "H[w3 w1 V[w2 w4]]", "H[w1 w3 V[w2 w4]]"),
    ("dwindle", "H[w1 V[w2 H[w3 w4]]]", "H[w3 V[w2 H[w1 w4]]]"),
])
def test_other_layouts(session, layout, before, after):
    s = started(session, layout)
    assert s.shape() == before
    s.focus("w3")
    s.key("F9")
    assert s.shape() == after


def test_a_workspace_let_go_has_no_master(session):
    s = started(session, "master")
    s.releasing = True
    s.command("[title=^w4$] move left")
    assert s.shape() == "H[w1 w4 V[w2 w3]]"
    s.focus("w2")
    s.key("F9")
    assert s.shape() == "H[w1 w4 V[w2 w3]]"
    s.choose("master")
    s.focus("w4")
    s.key("F9")
    assert s.shape() == "H[w4 V[w2 w3 w1]]"


def test_nothing_happens_where_there_is_no_master(session):
    s = started(session, "float", count=2)
    s.focus("w2")
    s.key("F9")
    assert s.shape() == "- F[w1 w2]"
    s.choose("master")
    s.command("[title=^w2$] fullscreen enable")
    s.key("F9")
    assert s.fullscreen("w2")
    assert s.shape() == "H[w1 w2*]"
    assert s.alive


def test_the_swap_command_does_what_the_key_does(session):
    s = started(session, "master")
    s.focus("w3")
    assert s.run("swap").returncode == 0
    s.settle()
    assert s.shape() == "H[w3 V[w2 w1 w4]]"
    assert s.focused() == "w3"


def test_the_move_command_does_what_the_key_does(session):
    s = started(session, "master")
    s.focus("w3")
    assert s.run("move", "up").returncode == 0
    s.settle()
    assert s.shape() == "H[w1 V[w3 w2 w4]]"
    assert s.run("move", "number", "5").returncode == 0
    s.settle()
    assert s.shape() == "H[w1 V[w2 w4]]"
    assert s.run("move", "sideways").returncode == 2
    assert s.run("move").returncode == 2

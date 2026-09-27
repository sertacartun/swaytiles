"""Situations that broke other layout daemons: assign rules, X11 windows, global fullscreen."""

from harness import CLIENT, CLIENT_PYTHON


def x11(s, title):
    s.msg(f"exec env GDK_BACKEND=x11 GSK_RENDERER=cairo GTK_A11Y=none {CLIENT_PYTHON} {CLIENT} {title}")
    assert s.wait(lambda: s.node(title) is not None, 15), f"X11 window {title} did not map"
    s.settle()


def test_assign_sends_windows_to_a_hidden_workspace(session):
    s = session("master", config="assign [title=^as] workspace 3\n")
    s.open("w1")
    for index in range(1, 4):
        s.open(f"as{index}")
    assert s.shape("3") == "H[as1 V[as2 as3]]"
    assert s.shape() == "w1"
    s.open("w2")
    assert s.shape() == "H[w1 w2]"


def test_x11_windows_are_placed_like_wayland_ones(session):
    s = session("master", config="xwayland enable\n")
    s.open("w1")
    x11(s, "x1")
    assert s.node("x1")["shell"] == "xwayland"
    s.open("w2")
    x11(s, "x2")
    assert s.shape() == "H[w1 V[x1 w2 x2]]"
    s.close("w1")
    assert s.shape() == "H[x1 V[w2 x2]]"


def test_global_fullscreen_is_respected(session):
    s = session("centered")
    s.open("w1")
    s.open("w2")
    s.command("[title=^w1$] fullscreen enable global")
    s.open("w3")
    assert s.fullscreen("w1")
    s.command("[title=^w1$] fullscreen disable")
    assert s.shape() == "H[w3 w1 w2]"

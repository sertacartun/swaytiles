"""The daemon takes over the keys that the config binds to sway's own move."""

import subprocess
import sys

from harness import DAEMON

import swaytiles

NATIVE = ("set $mod Mod4\nset $left F1\n"
          "bindsym $mod+$left move left\nbindsym $mod+F2 move right\nbindsym $mod+F3 move up\nbindsym $mod+F4 move down\n"
          "bindsym $mod+F6 move container to workspace number 3\n"
          "bindsym $mod+F7 floating toggle\nbindsym $mod+F9 move scratchpad\nbindsym $mod+F10 kill\n")


def started(session, layout="master", count=4, config=NATIVE, **options):
    s = session(layout, config=config, **options)
    for index in range(1, count + 1):
        s.open(f"w{index}")
    return s


def test_reading_bindings_follows_variables_blocks_and_includes(tmp_path):
    (tmp_path / "more").mkdir()
    (tmp_path / "more" / "a.conf").write_text("bindsym $mod+Shift+$left move left\nbindsym $mod+x move right\n")
    (tmp_path / "more" / "b.conf").write_text("bindsym $mod+x exec foot\ninclude ../config\n")
    text = ("set $mod Mod4\nset $left h\n# bindsym $mod+c move up\n"
            "bindsym --to-code $mod+k \\\n    move up\n"
            "bindsym {\n    $mod+j move down\n}\n"
            'mode "resize" {\n    bindsym $mod+r move left\n}\n'
            "include more/*.conf\n")
    (tmp_path / "config").write_text(text)
    assert swaytiles.bindings(text, tmp_path) == [
        ("bindsym --to-code Mod4+k", "move up"),
        ("bindsym Mod4+j", "move down"),
        ("bindsym Mod4+Shift+h", "move left"),
        ("bindsym Mod4+x", "move right"),
        ("bindsym Mod4+x", "exec foot"),
        ("bindsym --to-code Mod4+k", "move up"),
        ("bindsym Mod4+j", "move down"),
    ]


def test_the_default_sway_config_gives_up_its_move_keys():
    text = "set $mod Mod4\nset $left h\nset $down j\nset $up k\nset $right l\n"
    text += "".join(f"    bindsym $mod+Shift+{key} move {way}\n" for way in ("left", "down", "up", "right")
                    for key in (f"${way}", way.capitalize()))
    text += "".join(f"    bindsym $mod+Shift+{number % 10} move container to workspace number {number}\n" for number in range(1, 11))
    text += "    bindsym $mod+Shift+minus move scratchpad\n    bindsym $mod+$left focus left\n"
    text += 'mode "resize" {\n    bindsym $left resize shrink width 10px\n    bindsym Return mode "default"\n}\nbindsym $mod+r mode "resize"\n'
    taken = {prefix: swaytiles.following(command) for prefix, command in swaytiles.bindings(text, "/nowhere") if swaytiles.following(command)}
    assert len(taken) == 19
    assert taken["bindsym Mod4+Shift+minus"] == "nop layout hide"
    assert taken["bindsym Mod4+Shift+h"] == "nop layout move left"
    assert taken["bindsym Mod4+Shift+Right"] == "nop layout move right"
    assert taken["bindsym Mod4+Shift+0"] == "nop layout move number 10"


def test_only_plain_moves_are_taken_over():
    assert swaytiles.following("move left") == "nop layout move left"
    assert swaytiles.following("move container to  workspace number 3") == "nop layout move number 3"
    assert swaytiles.following("move window to workspace web") == "nop layout move web"
    assert swaytiles.following("move  scratchpad") == "nop layout hide"
    assert swaytiles.following("floating toggle") == "nop layout float toggle"
    assert swaytiles.following("kill") == "nop layout close"
    for command in ("move left 20 px", "move left; focus left", "move container to workspace next", "floating toggle, resize set 50 ppt",
                    "[app_id=foot] floating toggle", "[app_id=foot] kill",
                    "move container to workspace number", "move workspace to output left", "nop layout move left"):
        assert swaytiles.following(command) is None, command


def test_the_configs_move_keys_follow_the_layout(session):
    s = started(session)
    s.focus("w3")
    s.key("F3")
    assert s.shape() == "H[w1 V[w3 w2 w4]]"
    s.key("F1")
    assert s.shape() == "H[w3 V[w1 w2 w4]]"
    s.key("F6")
    assert s.shape() == "H[w1 V[w2 w4]]"
    assert s.shape("3") == "w3"


def test_a_reload_keeps_the_keys(session):
    s = started(session)
    s.command("reload")
    s.focus("w3")
    s.key("F3")
    assert s.shape() == "H[w1 V[w3 w2 w4]]"


def test_the_keys_go_back_when_the_daemon_stops(session):
    s = started(session)
    s.stop_daemon()
    s.focus("w4")
    s.key("F1")
    assert s.shape() == "H[w1 w4 V[w2 w3]]"
    s.releasing = True


def test_a_later_binding_for_the_same_keys_wins(session):
    s = started(session, config=NATIVE + "bindsym Mod4+F3 focus left\n")
    s.focus("w3")
    s.key("F3")
    assert s.shape() == "H[w1 V[w2 w3 w4]]"
    assert s.focused() == "w1"


def test_no_keys_leaves_the_bindings_alone(session):
    s = started(session)
    s.stop_daemon()
    with open(s.errors, "a") as errors:
        s.daemon = subprocess.Popen([sys.executable, str(DAEMON), "--no-keys"], env=s.env, stdout=subprocess.DEVNULL,
                                    stderr=errors, start_new_session=True)
    assert s.wait(s.locked, 5)
    s.settle()
    s.focus("w4")
    s.key("F1")
    assert s.shape() == "H[w1 w4 V[w2 w3]]"
    s.releasing = True


def test_a_key_bound_at_runtime_is_taken_over_once_used(session):
    s = started(session, config="")
    s.command("bindsym Mod4+F8 move left")
    s.focus("w1")
    s.key("F8")
    assert s.shape() == "H[w1 V[w2 w3 w4]]"
    s.focus("w3")
    s.key("F8")
    assert s.shape() == "H[w3 V[w2 w1 w4]]"


def test_floating_a_window_by_key_puts_its_layout_in_order_in_one_step(session):
    s = started(session)
    s.focus("w1")
    drawn = s.drawn(lambda: s.key("F7"))
    assert len(drawn) == 1, drawn
    assert s.shape() == "H[w2 V[w3 w4]] F[w1]"
    drawn = s.drawn(lambda: s.key("F7"))
    assert len(drawn) == 1, drawn
    assert s.shape() == "H[w2 V[w3 w4 w1]]"


def test_hiding_a_window_by_key_puts_its_layout_in_order_in_one_step(session):
    s = started(session)
    s.focus("w1")
    drawn = s.drawn(lambda: s.key("F9"))
    assert len(drawn) == 1, drawn
    assert s.shape() == "H[w2 V[w3 w4]]"
    s.run("show")
    assert s.shape() == "H[w2 V[w3 w4 w1]]"


def test_closing_a_window_by_key_puts_its_layout_in_order_in_one_step(session):
    from harness import expected
    s = started(session, "dwindle")
    s.focus("w2")
    drawn = s.drawn(lambda: s.key("F10"))
    # Hidden, the others in their places and closed, in one message: sway never draws the hole.
    assert len(drawn) == 1 and drawn[0].endswith("kill"), drawn
    assert s.shape() == expected("dwindle", 3, ["w1", "w3", "w4"])


def test_closing_the_last_window_by_key_is_left_to_sway(session):
    s = started(session, "dwindle")
    drawn = s.drawn(lambda: s.key("F10"))
    # sway's own kill: the hole it leaves is the layout without it.
    assert drawn == ["kill"]
    assert s.node("w4") is None


def test_a_window_that_will_not_close_comes_back(session):
    from harness import expected
    s = started(session, "dwindle", count=2)
    s.open("stubborn")
    s.open("w3")
    s.focus("stubborn")
    s.key("F10")
    assert s.shape() == expected("dwindle", 3)
    assert s.wait(lambda: s.shape() == expected("dwindle", 4, ["w1", "w2", "w3", "stubborn"]), 3), s.shape()
    assert s.focused() == "stubborn"


def test_closing_by_key_focuses_the_window_sways_own_close_would(session):
    s = started(session, "spiral", count=6)
    for title in ("w5", "w1", "w3"):
        s.focus(title)
    s.key("F10")
    assert s.focused() == "w5"

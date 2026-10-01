"""A layout gives way to changes made by hand and takes over again when they fit it."""

import signal

from harness import expected

import swaytiles

MOVES = ("bindsym Mod4+F1 nop layout move left\nbindsym Mod4+F2 nop layout move right\n"
         "bindsym Mod4+F3 nop layout move up\nbindsym Mod4+F4 nop layout move down\n"
         "bindsym Mod4+F5 layout tabbed\nbindsym Mod4+F6 splitv\nbindsym Mod4+F7 layout toggle split\n")


def opened(session, layout, count=4, **options):
    s = session(layout, config=MOVES, **options)
    for index in range(1, count + 1):
        s.open(f"w{index}")
    return s


def test_layout_moves_swap_with_the_neighbour(session):
    s = opened(session, "master")
    s.focus("w4")
    s.key("F1")
    assert s.shape() == "H[w4 V[w2 w3 w1]]"
    s.focus("w1")
    s.key("F3")
    assert s.shape() == "H[w4 V[w2 w1 w3]]"
    s.key("F3")
    s.key("F3")
    assert s.shape() == "H[w4 V[w1 w2 w3]]"
    s.focus("w4")
    s.key("F2")
    assert s.shape() == "H[w1 V[w4 w2 w3]]"
    s.key("F1")
    s.key("F1")
    assert s.shape() == "H[w4 V[w1 w2 w3]]"
    s.open("w5")
    assert s.shape() == "H[w4 V[w1 w2 w3 w5]]"
    assert s.focused() == "w5"


def test_layout_moves_reorder_tabs_before_leaving_them(session):
    s = opened(session, "tabbed-master")
    s.focus("w3")
    s.key("F2")
    assert s.shape() == "H[w1 T[w2 w4 w3]]"
    s.focus("w2")
    s.key("F1")
    assert s.shape() == "H[w2 T[w1 w4 w3]]"
    s.open("w5")
    assert s.shape() == "H[w2 T[w1 w4 w3 w5]]"


def test_layout_moves_in_every_layout_keep_it(session):
    s = opened(session, "master", count=5)
    for layout in ("master-right", "wide", "centered", "dwindle", "spiral", "grid", "stacked-master"):
        s.choose(layout)
        for title in ("w5", "w1"):
            for key in ("F1", "F2", "F3", "F4"):
                s.focus(title)
                s.key(key)
                assert s.chosen() == layout and not s.paused(), (layout, title, key)
        moved = s.shape()
        s.choose(layout)
        assert s.shape() == moved, layout


def test_a_native_move_that_breaks_the_layout_pauses_it(session):
    s = opened(session, "master", count=3)
    s.pausing = True
    s.command("[title=^w3$] move left")
    assert s.shape() == "H[w1 w3 w2]"
    assert s.paused() == {"1"} and s.chosen() == "master"
    s.open("w4")
    assert s.shape() == "H[w1 w3 w4 w2]"
    s.close("w3")
    s.close("w4")
    assert s.paused() == set()
    s.open("w5")
    assert s.shape() == "H[w1 V[w2 w5]]"


def test_a_native_move_that_fits_the_layout_is_kept(session):
    s = opened(session, "master")
    s.command("[title=^w4$] move up")
    assert s.shape() == "H[w1 V[w2 w4 w3]]"
    s.open("w5")
    assert s.shape() == "H[w1 V[w2 w4 w3 w5]]"
    s.close("w1")
    assert s.shape() == "H[w2 V[w4 w3 w5]]"


def test_a_layout_key_pauses_at_once(session):
    s = opened(session, "master", count=3)
    s.pausing = True
    s.focus("w1")
    s.key("F7")
    assert s.paused() == {"1"} and s.chosen() == "master"
    before = s.shape()
    s.open("w4")
    assert "w4" in s.shape() and s.shape() != "H[w1 V[w2 w3 w4]]"
    s.choose("master")
    assert s.paused() == set()
    assert s.shape() == "H[w1 V[w2 w3 w4]]"
    assert before != s.shape()


def test_a_stack_made_tabbed_by_key_becomes_tabbed_master(session):
    s = opened(session, "master", count=3)
    s.focus("w3")
    s.key("F5")
    assert s.shape() == "H[w1 T[w2 w3]]"
    assert s.chosen() == "tabbed-master" and s.paused() == set()
    s.open("w4")
    assert s.shape() == "H[w1 T[w2 w3 w4]]"
    s.close("w4")
    assert s.shape() == "H[w1 T[w2 w3]]"


def test_a_style_change_that_reorders_the_windows_pauses(session):
    s = opened(session, "master", count=3)
    s.pausing = True
    s.focus("w3")
    s.key("F7")
    assert s.chosen() == "master" and s.paused() == {"1"}
    assert s.shape() == "H[w1 H[w2 w3]]"


def test_a_tabbed_workspace_split_by_key_pauses(session):
    s = opened(session, "tabbed", count=3)
    s.pausing = True
    s.key("F7")
    assert s.chosen() == "tabbed" and s.paused() == {"1"}
    assert s.shape() == "H[w1 w2 w3]"


def test_two_windows_split_by_key_become_wide(session):
    s = opened(session, "master", count=2)
    s.focus("w1")
    s.key("F7")
    assert s.chosen() == "wide" and s.paused() == set()
    s.open("w3")
    assert s.shape() == expected("wide", 3)


def test_a_split_key_on_a_single_window_changes_nothing(session):
    s = opened(session, "master", count=3)
    s.focus("w2")
    s.key("F6")
    s.open("w4")
    assert s.shape() == "H[w1 V[w2 w3 w4]]"
    assert s.chosen() == "master" and s.paused() == set()


def test_a_tabbed_stack_split_by_command_becomes_master(session):
    s = opened(session, "tabbed-master", count=3)
    s.focus("w2")
    s.command("layout splitv")
    s.open("w4")
    assert s.shape() == "H[w1 V[w2 w3 w4]]"
    assert s.chosen() == "master"


def test_a_change_by_command_that_fits_nothing_pauses(session):
    s = opened(session, "master", count=3)
    s.pausing = True
    s.focus("w1")
    s.command("layout toggle split")
    s.open("w4")
    assert s.paused() == {"1"}
    assert s.shape() == "V[w1 V[w2 w3 w4]]"
    s.choose("master")
    assert s.shape() == "H[w1 V[w2 w3 w4]]"


def test_a_pause_survives_a_restart(session):
    s = opened(session, "master", count=3)
    s.pausing = True
    s.command("[title=^w3$] move left")
    s.stop_daemon()
    s.start_daemon()
    assert s.paused() == {"1"}
    assert s.shape() == "H[w1 w3 w2]"
    s.stop_daemon(signal.SIGKILL)
    s.start_daemon()
    s.open("w4")
    assert s.shape() == "H[w1 w3 w4 w2]"


def test_a_window_dragged_into_the_stack_keeps_its_place(session):
    s = opened(session, "master")
    s.focus("w2")
    s.command("workspace 2")
    s.open("x1")
    s.open("x2")
    s.command("[title=^x1$] move container to workspace 1")
    assert s.shape() == "H[w1 V[w2 x1 w3 w4]]"
    s.open("x3")
    s.command("workspace 1")
    s.open("w5")
    assert s.shape() == "H[w1 V[w2 x1 w3 w4 w5]]"


def test_a_window_dropped_where_it_does_not_fit_joins_the_stack(session):
    s = opened(session, "master", count=3)
    s.focus("w1")
    s.command("workspace 2")
    s.open("x1")
    s.command("[title=^x1$] move container to workspace 1")
    assert s.shape() == "H[w1 V[w2 w3 x1]]"
    assert s.paused() == set()


def test_the_menu_marks_a_paused_workspace(session, tmp_path):
    s = opened(session, "master", count=3)
    s.pausing = True
    s.command("[title=^w3$] move left")
    listing = tmp_path / "listing"
    script = tmp_path / "launcher.sh"
    script.write_text(f'cat > "{listing}"\necho "grid — Even grid"\n')
    result = s.run("menu", "--launcher", f"sh {script}")
    s.settle()
    assert result.returncode == 0, result.stderr
    lines = listing.read_text().splitlines()
    assert lines[list(swaytiles.LAYOUTS).index("master")].endswith("● paused")
    assert "\0" not in listing.read_text()
    assert s.chosen() == "grid" and s.paused() == set()
    assert s.shape() == expected("grid", 3)


def test_the_menu_asks_which_launcher_to_use(session, tmp_path):
    s = opened(session, "master", count=1)
    fake = tmp_path / "fuzzel"
    fake.write_text("#!/bin/sh\necho 3\n")
    fake.chmod(0o755)
    result = s.run("menu", env={"PATH": str(tmp_path)})
    assert result.returncode == 1
    assert "--launcher" in result.stderr
    assert s.chosen() == "master"


def test_the_config_command_prints_the_bindings(session):
    s = opened(session, "master", count=1)
    result = s.run("config")
    assert result.returncode == 0
    assert result.stdout == swaytiles.CONFIG.format(command="swaytiles")
    assert s.run("nonsense").returncode == 2


def test_the_menu_runs_fuzzel_by_name_and_reads_its_index(session, tmp_path):
    s = opened(session, "master", count=3)
    record = tmp_path / "record"
    fake = tmp_path / "fuzzel"
    fake.write_text(f'#!/bin/sh\necho "$@" > "{record}.args"\n/usr/bin/cat > "{record}.input"\necho {list(swaytiles.LAYOUTS).index("wide")}\n')
    fake.chmod(0o755)
    result = s.run("menu", "--launcher", "fuzzel", env={"PATH": str(tmp_path)})
    s.settle()
    assert result.returncode == 0, result.stderr
    assert f"--select-index {list(swaytiles.LAYOUTS).index('master')}" in (tmp_path / "record.args").read_text()
    assert "\0icon\x1f" in (tmp_path / "record.input").read_text()
    assert s.chosen() == "wide"
    assert s.shape() == expected("wide", 3)

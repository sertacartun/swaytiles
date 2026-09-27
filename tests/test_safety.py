"""Only one daemon per sway, a broken state file, bogus requests, crashes."""

import json
import subprocess
import sys

from harness import DAEMON


def test_a_second_daemon_refuses_to_start(session):
    s = session("master")
    second = subprocess.run([sys.executable, str(DAEMON)], env=s.env, capture_output=True, text=True, timeout=15)
    assert second.returncode == 1
    assert "another daemon" in second.stderr
    assert s.alive


def test_a_broken_state_file_is_repaired(session):
    s = session("master")
    s.stop_daemon()
    s.state_file.write_text('{"layout": "master", "workspaces": {"1": ["bad"], "2": "spiral"}, "new": 5}')
    s.start_daemon()
    s.open("w1")
    state = json.loads(s.state_file.read_text())
    assert state["workspaces"]["1"] == "master"
    assert state["workspaces"]["2"] == "spiral"
    s.stop_daemon()
    s.state_file.write_text("not json")
    s.start_daemon()
    s.open("w2")
    assert s.alive


def test_bogus_requests_are_ignored(session):
    s = session("master")
    s.open("w1")
    s.open("w2")
    for payload in ("layout bogus", "layout ", "layout new-master", "layout:sync 99", "garbage"):
        s.msg("-t", "send_tick", payload)
    subprocess.run([sys.executable, str(DAEMON), "nonsense"], env=s.env)
    s.settle()
    assert s.chosen() == "master"
    s.open("w3")
    assert s.shape() == "H[w1 V[w2 w3]]"
    assert s.alive


def test_the_daemon_leaves_sway_clean_when_stopped(session):
    s = session("float")
    s.open("w1")
    s.open("w2")
    s.stop_daemon()
    tree = json.dumps(s.tree())
    assert "_layout_after_" not in tree
    assert '"opacity": 0' not in tree.replace('"opacity": 0.', "")
    s.open("w3")
    assert s.node("w3")["type"] == "con"
    s.start_daemon()

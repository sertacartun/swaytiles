"""swaytiles --wait starts before sway and serves one sway session after another."""

import subprocess
import sys

from harness import DAEMON, Session, expected


def attached(s):
    assert s.wait(s.locked, 10)
    s.settle()


def test_the_waiting_daemon_follows_sway_across_sessions(tmp_path):
    s = Session(tmp_path, "master")
    environment = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "XDG_RUNTIME_DIR": str(s.base), "SWAYSOCK": str(s.base / "gone.sock"),
                   "XDG_STATE_HOME": str(s.state), "XDG_CACHE_HOME": str(s.cache)}
    s.start_daemon = lambda: attached(s)
    with open(s.errors, "a") as errors:
        s.daemon = subprocess.Popen([sys.executable, str(DAEMON), "--wait"], env=environment, stdout=subprocess.DEVNULL,
                                    stderr=errors, start_new_session=True)
    try:
        assert not s.wait(lambda: not s.alive, 2.5)
        s.start()
        s.fill(3)
        assert s.shape() == expected("master", 3)
        s.msg("exit")
        s.sway.wait(10)
        assert s.wait(lambda: not s.locked(), 5)
        assert s.alive
        s.start()
        s.fill(3, "v")
        assert s.shape() == expected("master", 3, ["v1", "v2", "v3"])
        assert s.alive
        assert not s.stderr().strip()
    finally:
        s.stop()


def test_waiting_gives_way_to_a_daemon_that_already_runs(session):
    s = session("master")
    second = subprocess.run([sys.executable, str(DAEMON), "--wait"], env=s.env, capture_output=True, text=True, timeout=20)
    assert second.returncode == 2
    assert s.alive

"""install.sh, in a scratch home, with a stand-in for systemctl."""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(home, *arguments, session=True, sudo=False):
    """Run install.sh with `home` as $HOME; systemctl only records what it
    is asked, and has a user session to talk to when `session` is true."""
    fake = home / "fake-bin"
    fake.mkdir(exist_ok=True)
    systemctl = fake / "systemctl"
    systemctl.write_text(f'#!/bin/sh\necho "$@" >> "{home}/systemctl.log"\n'
                         f'[ "$2" = show-environment ] && exit {0 if session else 1}\nexit 0\n')
    systemctl.chmod(0o755)
    environment = {"HOME": str(home), "PATH": f"{fake}:{os.environ['PATH']}", **({"SUDO_USER": "someone"} if sudo else {})}
    return subprocess.run(["sh", str(ROOT / "install.sh"), *arguments], env=environment,
                          capture_output=True, text=True, timeout=30)


def calls(home):
    log = home / "systemctl.log"
    return [line for line in log.read_text().splitlines() if line != "--user show-environment"] if log.exists() else []


def test_installing_puts_the_files_in_place_and_starts_the_service(tmp_path):
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr
    library = tmp_path / ".local/lib/swaytiles"
    assert (library / "swaytiles.py").read_text() == (ROOT / "swaytiles.py").read_text()
    assert list((library / "__pycache__").glob("swaytiles.*.pyc"))
    launcher = tmp_path / ".local/bin/swaytiles"
    assert launcher.read_text() == (ROOT / "contrib/swaytiles").read_text()
    assert os.access(launcher, os.X_OK)
    assert (tmp_path / ".config/systemd/user/swaytiles.service").read_text() == (ROOT / "contrib/swaytiles.service").read_text()
    assert calls(tmp_path) == ["--user daemon-reload", "--user enable swaytiles.service", "--user restart swaytiles.service"]
    assert f"bindsym $mod+m exec {launcher} swap" in result.stdout


def test_installing_again_updates_the_files_and_restarts_the_daemon(tmp_path):
    assert run(tmp_path).returncode == 0
    (tmp_path / ".local/lib/swaytiles/swaytiles.py").write_text("old")
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / ".local/lib/swaytiles/swaytiles.py").read_text() == (ROOT / "swaytiles.py").read_text()
    assert calls(tmp_path).count("--user restart swaytiles.service") == 2


def test_without_a_user_session_it_installs_and_says_to_start_it_from_sway(tmp_path):
    result = run(tmp_path, session=False)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / ".local/bin/swaytiles").exists()
    assert not (tmp_path / ".config/systemd/user/swaytiles.service").exists()
    assert calls(tmp_path) == []
    assert "without its #" in result.stdout
    assert f"# exec {tmp_path / '.local/bin/swaytiles'}" in result.stdout


def test_uninstalling_stops_the_service_and_removes_every_file(tmp_path):
    assert run(tmp_path).returncode == 0
    result = run(tmp_path, "--uninstall")
    assert result.returncode == 0, result.stderr
    assert calls(tmp_path)[-1] == "--user disable --now swaytiles.service"
    assert not (tmp_path / ".local/lib/swaytiles").exists()
    assert not (tmp_path / ".local/bin/swaytiles").exists()
    assert not (tmp_path / ".config/systemd/user/swaytiles.service").exists()


def test_an_unknown_argument_changes_nothing(tmp_path):
    result = run(tmp_path, "--nonsense")
    assert result.returncode == 2
    assert "usage" in result.stderr
    assert not (tmp_path / ".local").exists()


def test_under_sudo_it_refuses_and_changes_nothing(tmp_path):
    result = run(tmp_path, sudo=True)
    assert result.returncode == 1
    assert "without sudo" in result.stderr
    assert not (tmp_path / ".local").exists()

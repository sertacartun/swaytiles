"""Starts the daemon as ~/.local/bin/swaytiles does, by importing it, and
writes every command message it sends to sway to $SWAYTILES_SENT, one per
line: each message is one transaction, so a test can count what sway draws."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import swaytiles

SENT = open(os.environ["SWAYTILES_SENT"], "a", buffering=1)
send = swaytiles.Sway.command


def command(self, *commands):
    joined = "; ".join(part for part in commands if part)
    if joined:
        SENT.write(joined + "\n")
    return send(self, *commands)


swaytiles.Sway.command = command
READY = Path(os.environ.get("SWAYTILES_READY", os.devnull))
arrange = swaytiles.Daemon.arrange


def arranged(self, *arguments, **options):
    # The first arrangement has set the rules for new windows: the daemon is ready.
    arrange(self, *arguments, **options)
    if not READY.exists():
        READY.touch()


swaytiles.Daemon.arrange = arranged
sys.exit(swaytiles.cli())

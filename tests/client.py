"""A bare GTK 4 window titled after its first argument, used as a test window.
One titled stubborn… will not close when asked."""

import sys

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

app = Gtk.Application()


def activate(application):
    window = Gtk.ApplicationWindow(application=application, title=sys.argv[1])
    if sys.argv[1].startswith("stubborn"):
        # Like an editor asking about unsaved work: it does not close when asked.
        window.connect("close-request", lambda *_: True)
    window.present()


app.connect("activate", activate)
app.run([])

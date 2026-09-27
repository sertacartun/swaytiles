"""A bare GTK 4 window titled after its first argument, used as a test window."""

import sys

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

app = Gtk.Application()
app.connect("activate", lambda application: Gtk.ApplicationWindow(application=application, title=sys.argv[1]).present())
app.run([])

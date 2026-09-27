"""A demo window: a coloured card with a large number, for the README recordings."""

import sys

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

COLOURS = ["#88C0D0", "#A3BE8C", "#EBCB8B", "#D08770", "#B48EAD", "#81A1C1", "#BF616A", "#8FBCBB"]
number = int(sys.argv[1])
colour = COLOURS[(number - 1) % len(COLOURS)]

css = Gtk.CssProvider()
css.load_from_string(f"""
window {{ background: #3B4252; }}
.card {{ background: {colour}; border-radius: 10px; margin: 14px; }}
.number {{ color: #2E3440; font-family: "Fira Sans"; font-weight: 800; font-size: 84px; }}
.name {{ color: #2E3440; font-family: "Fira Sans"; font-weight: 500; font-size: 17px; opacity: 0.75; }}
""")


def activate(application):
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
    window = Gtk.ApplicationWindow(application=application, title=f"Window {number}")
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.FILL, halign=Gtk.Align.FILL, hexpand=True, vexpand=True)
    card.add_css_class("card")
    inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER, vexpand=True)
    big = Gtk.Label(label=str(number))
    big.add_css_class("number")
    small = Gtk.Label(label=f"Window {number}")
    small.add_css_class("name")
    inner.append(big)
    inner.append(small)
    card.append(inner)
    window.set_child(card)
    window.present()


app = Gtk.Application()
app.connect("activate", activate)
app.run([])

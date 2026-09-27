# sway-layout

Per-workspace tiling layouts for [sway](https://swaywm.org): master and
stack, centred master, dwindle, spiral, grid, tabs and a floating
workspace. The layout is chosen per workspace from a menu and remembered.

It is a single Python file with no dependencies, driven over sway's IPC
socket. New windows are placed by sway rules the daemon sets up, so they
appear in their final place instead of jumping there.

## Layouts

| Layout | What it looks like |
| --- | --- |
| `master` | Master left, stack right |
| `master-right` | Master right, stack left |
| `wide` | Master on top, stack below |
| `tabbed-master` | Master left, stack as tabs |
| `stacked-master` | Master left, stack as stacked titles |
| `centered` | Master centred, stack on both sides |
| `dwindle` | Splits right, then down, shrinking |
| `spiral` | Clockwise spiral inwards |
| `grid` | Even grid |
| `tabbed` | Tabs, one window visible |
| `stacking` | Stacked titles, one window visible |
| `float` | All windows floating, cascaded inside the output |
| `sway` | The daemon leaves the workspace alone |

## What it respects

- **Your own arrangement.** A window you move out of the layout by hand
  stays where you put it. New windows join the stack around it, and
  choosing the layout again from the menu tidies everything up.
- **Tabs and splits you set by hand.** Turn the stack into tabs with
  sway's own `layout tabbed` and it stays tabbed. If the result matches
  another layout, the workspace switches to that layout.
- **The master size.** Resize the master with the keyboard or the mouse
  and it keeps that size when windows open and close, when the master
  closes and when you switch between layouts.
- **Fullscreen.** A workspace with a fullscreen window is left untouched
  until the window leaves fullscreen.
- **Floating windows.** Dialogs float as usual. A window you float for a
  moment goes back to its place when you tile it again.
- **Restarts.** Manual arrangements survive a restart or a crash of the
  daemon.
- **Several outputs.** Workspaces can move between outputs and outputs
  can come and go. Floating windows are fitted to the output they end up
  on.

## Install

```sh
uv tool install git+https://github.com/USER/sway-layout   # or: pipx install .
mkdir -p ~/.config/systemd/user
cp contrib/sway-layout.service ~/.config/systemd/user/
```

Then add the lines from [`contrib/sway.conf`](contrib/sway.conf) to your
sway config and restart sway. The menu needs
[fuzzel](https://codeberg.org/dnkl/fuzzel).

Errors go to the journal: `journalctl --user -u sway-layout`.

## Commands

```sh
sway-layout            # run the daemon
sway-layout menu       # pick a layout for the focused workspace
sway-layout grid       # set a layout for the focused workspace
```

`nop layout move left|right|up|down` and `nop layout move number N`
bindings move the focused window the way the layout expects, including
across outputs and into floating workspaces.

## Files

- `~/.local/state/sway-layout.json`: the layout of every workspace.
- `$XDG_RUNTIME_DIR/sway-layout.*`: the lock and the manual arrangements
  of the running session.

## Tests

The tests start a headless sway with its own sockets, so they never touch
your session. They need `sway`, `wtype` and GTK 4 for Python
(`python-gobject`), which draws the test windows.

```sh
uv run pytest
```

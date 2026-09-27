# sway-layout

Per-workspace tiling layouts for [sway](https://swaywm.org): master and
stack, centred master, dwindle, spiral, grid, tabs and a floating
workspace. The layout is chosen per workspace from a menu and remembered.

It is a single Python file with no dependencies, driven over sway's IPC
socket. New windows are placed by sway rules the daemon sets up, so they
appear in their final place instead of jumping there.

## Why another one

Other sway layout daemons tend to fight the user: they flicker while
rearranging, pull dialogs and scratchpad windows into the layout, undo
tabs set by hand, reset sizes, drop fullscreen windows, or break with a
second monitor. sway-layout is built around the opposite rule: a
workspace is either fully arranged by its layout or left entirely to
you, and anything you do by hand wins. Every case above is covered by
the headless test suite.

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

- **Changes made by hand.** Moving a window, splitting or changing a
  container's layout with sway's own keys, dragging with the mouse or
  running `swaymsg` never gets undone:
  - If the result still fits the layout, the layout keeps going with
    the new order. Dragging a window into the stack, even from another
    output, keeps it where you dropped it.
  - If only a container's style changed and the result is another
    layout, the workspace switches to it. Turning the stack of `master`
    into tabs makes it `tabbed-master`.
  - Otherwise the layout pauses on that workspace and sway places new
    windows as usual. The menu shows the layout as paused. The layout
    comes back by itself once the windows fit it again, or when you pick
    it from the menu.
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

1. Install the program and the user service:

   ```sh
   uv tool install git+https://github.com/USER/sway-layout   # or: pipx install .
   mkdir -p ~/.config/systemd/user
   cp contrib/sway-layout.service ~/.config/systemd/user/
   ```

2. Write the sway bindings to a file of their own:

   ```sh
   sway-layout config > ~/.config/sway/sway-layout.conf
   ```

   It starts the daemon and binds `$mod+Shift+t` to the menu, `$mod+m`
   to swap with the master, and `$mod+Shift+` direction keys and numbers
   to moves that follow the layout. Change the keys there if you like.
   It uses `$mod`, as the default sway config defines it.

3. Include it at the end of your sway config and restart sway:

   ```
   include ~/.config/sway/sway-layout.conf
   ```

   Nothing else in your config is changed. The bindings in this file
   replace earlier bindings for the same keys, which sway accepts
   silently.

The menu uses the first of fuzzel, rofi, wofi, tofi, bemenu, wmenu and
dmenu that is installed. fuzzel and rofi also show a picture of each
layout. Any other dmenu-style program works too:
`sway-layout menu --launcher "walker --dmenu"`.

Errors go to the journal: `journalctl --user -u sway-layout`.

## Commands

```sh
sway-layout            # run the daemon
sway-layout menu       # pick a layout for the focused workspace
sway-layout grid       # set a layout for the focused workspace
sway-layout config     # print the sway bindings
```

`nop layout move left|right|up|down` swaps the focused window with its
neighbour in that direction, so the layout always stays whole. Inside
tabs it reorders the tabs first. At the edge of the workspace it moves
the window to the next output. On a workspace without a layout it is
sway's own `move`. `nop layout move number N` sends the window to a
workspace, where it joins the stack or floats, as that workspace's
layout says. `nop layout master` swaps the focused window with the
master; on the master itself it swaps with the top of the stack. Sizes
stay where they are.

## Uninstall

Remove the `include` line from your sway config first, because the
bindings in it do nothing without the daemon. Then:

```sh
systemctl --user disable --now sway-layout.service
rm ~/.config/systemd/user/sway-layout.service ~/.config/sway/sway-layout.conf
uv tool uninstall sway-layout
```

## Files

- `~/.local/state/sway-layout.json`: the layout of every workspace.
- `$XDG_RUNTIME_DIR/sway-layout.*`: the lock, the paused workspaces and
  the last arrangement of the running session.

## Tests

The tests start a headless sway with its own sockets, so they never touch
your session. They need `sway`, `wtype` and GTK 4 for Python
(`python-gobject`), which draws the test windows.

```sh
uv run pytest
```

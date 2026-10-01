# Details

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
  On a `float` workspace it works the other way round: a window you tile
  by hand stays tiled until you float it again or pick `float` from the
  menu.
- **Restarts.** Manual arrangements survive a restart or a crash of the
  daemon.
- **Several outputs.** Workspaces can move between outputs and outputs
  can come and go. Floating windows are fitted to the output they end up
  on.

## Commands

```sh
swaytiles                 # run the daemon for this sway session
swaytiles --wait          # run it for every sway session, as the service does
swaytiles menu            # pick a layout for the focused workspace
swaytiles grid            # set a layout for the focused workspace
swaytiles swap            # swap the focused window with the master
swaytiles move left       # move the focused window: left, right, up, down
swaytiles move number 3   # send the focused window to a workspace
swaytiles config          # print lines for the sway config
```

`swaytiles move left|right|up|down` swaps the focused window with its
neighbour in that direction, so the layout always stays whole. Inside
tabs it reorders the tabs first. At the edge of the workspace it moves
the window to the next output. On a workspace without a layout it is
sway's own `move`. `swaytiles move number N` sends the window to a
workspace, where it joins the stack or floats, as that workspace's
layout says. `swaytiles swap` swaps the focused window with the
master; on the master itself it swaps with the top of the stack. Sizes
stay where they are.

Bindings inside a `mode` block are not taken over, and neither is a
binding that does more than move (`move left; focus left`). For those,
bind `exec swaytiles move left` yourself, or `nop layout move left`,
which the daemon reads from sway's binding events without a process
being started. `nop layout master` is the same for `swaytiles swap`.

## Setup

- **Move keys.** swaytiles reads your sway config and, while it runs,
  rebinds the keys bound to sway's own `move left`, `move right`,
  `move up`, `move down` and `move container to workspace …`. Your
  config file is never edited, and when swaytiles stops the keys are
  sway's own moves again. To turn this off, add `--no-keys` to the
  `ExecStart` line of the service.
- **Menu program.** The menu uses the first of fuzzel, rofi, wofi, tofi,
  bemenu, wmenu and dmenu that is installed. fuzzel and rofi also show a
  picture of each layout. Any other dmenu-style program works too:
  `swaytiles menu --launcher "walker --dmenu"`.
- **The service.** It runs `swaytiles --wait`, which waits for sway and
  serves one sway session after another. Errors go to the journal:
  `journalctl --user -u swaytiles`.
- **Without systemd.** Skip the service and add
  `exec ~/.local/bin/swaytiles` to your sway config.
- **Just trying.** `python3 swaytiles.py &` in a terminal starts it for
  the running session, `python3 swaytiles.py grid` arranges the focused
  workspace, and `kill %1` stops it.

## Files

- `~/.local/state/swaytiles.json`: the layout of every workspace.
- `$XDG_RUNTIME_DIR/swaytiles.*`: the lock, the paused workspaces and
  the last arrangement of the running session.

## Tests

The tests start a headless sway with its own sockets, so they never touch
your session. They need `sway`, `wtype` and GTK 4 for Python
(`python-gobject`), which draws the test windows.

```sh
uv run pytest
```

The animations in the README are recorded the same way, in a headless
sway, with `uv run python docs/record.py`. That also needs `grim`,
ImageMagick and the Fira Sans font.

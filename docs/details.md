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
  - Otherwise the workspace switches to `default` and sway places new
    windows as usual, with a notification that says so. The layout comes
    back when you pick it from the menu.
- **Sizes.** Whatever you resize, with the keyboard or the mouse, keeps
  its size as windows open and close, as in plain sway: windows move
  between the layout's places, and each place keeps the size you gave
  it. The master also keeps its size when windows come and go beside it,
  when it closes and when you switch between layouts.
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
swaytiles menu --launcher fuzzel   # pick a layout, in the menu program you name
swaytiles grid            # set a layout for the focused workspace
swaytiles default grid    # set the layout of workspaces opened from now on
swaytiles swap            # swap the focused window with the master
swaytiles move left       # move the focused window: left, right, up, down
swaytiles move number 3   # send the focused window to a workspace
swaytiles show            # bring the last hidden window back from the scratchpad
swaytiles show 42         # bring back the hidden window with con_id 42
swaytiles config          # print lines for the sway config
```

`swaytiles move left|right|up|down` swaps the focused window with its
neighbour in that direction, so the layout always stays whole. Inside
tabs it reorders the tabs first. At the edge of the workspace it moves
the window to the next output and the focus goes with it. There it
comes in at the edge it crosses, beside the window you last focused on
that workspace (or the one at that edge in the same column or row as
it), otherwise at the near end, as in tabs and stacked titles. On a
workspace without a layout it is sway's own `move`. `swaytiles move
number N` sends the window to a workspace, where it joins the stack or
floats, as that workspace's layout says. `swaytiles swap` swaps the focused window with the
master; on the master itself it swaps with the top of the stack. Sizes
stay where they are.

`swaytiles show` takes a window out of the scratchpad and puts it on
the focused workspace where a new window would open, tiled into the
layout, or in the next cascade slot on a float workspace. It is one
step, so the window is not drawn in sway's own place first. sway's
`scratchpad show` keeps the window floating; bind `nop layout show`
instead to get it back into the layout.

A layout is chosen for one workspace and stays on it. A workspace that
is opened for the first time gets the layout set with `swaytiles
default LAYOUT`; until that is used it is left to sway. Workspaces that
have been open before keep the layout they had.

Bindings inside a `mode` block are not taken over, and neither is a
binding that does more than move (`move left; focus left`). For those,
bind `exec swaytiles move left` yourself, or `nop layout move left`,
which the daemon reads from sway's binding events without a process
being started. `nop layout master` is the same for `swaytiles swap`,
`nop layout show` for `swaytiles show`, `nop layout close` for `kill`,
`nop layout hide` for `move scratchpad` and `nop layout float toggle`
for `floating toggle`.

## Setup

- **Move keys.** swaytiles reads your sway config and, while it runs,
  rebinds the keys bound to sway's own `move left`, `move right`,
  `move up`, `move down`, `move container to workspace …`, `move
  scratchpad` and `floating toggle|enable|disable`. Your
  config file is never edited, and when swaytiles stops the keys are
  sway's own moves again. To turn this off, add `--no-keys` to the
  `ExecStart` line of the service.
- **Closing.** The key bound to `kill` is taken over the same way, so a
  window closed from the middle of a layout leaves no hole for sway to
  draw: the others take their places as it goes, and the focus goes to
  the window that takes its place, the one under the mouse. A window that asks
  before it closes, or will not, comes back after a second. A window
  that closes by itself leaves its hole for a moment.
- **Menu program.** `swaytiles menu --launcher NAME` opens the menu in
  fuzzel, rofi, wofi, tofi, bemenu, wmenu or dmenu, whichever you name.
  fuzzel, rofi and wofi also show a picture of each layout, a small
  screen in colours of its own that reads on any theme and on the
  selected line. Any other dmenu-style program works with its whole
  command:
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

- `~/.local/state/swaytiles.json`: the layout of every workspace and
  the one for new workspaces.
- `$XDG_RUNTIME_DIR/swaytiles.*`: the lock, and the last arrangement of
  the running session with the windows kept tiled on float workspaces.

## Tests

The tests start a headless sway with its own sockets, so they never touch
your session. They need `sway`, `wtype` and GTK 4 for Python
(`python-gobject`), which draws the test windows.

```sh
uv run pytest
```

They run side by side, one per core up to eight, in about two minutes;
`-n 0` runs them one at a time.

The animations in the README are recorded the same way, in a headless
sway, with `uv run python docs/record.py`. That also needs `grim`,
ImageMagick and the Fira Sans font.

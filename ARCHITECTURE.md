# Architecture

How swaytiles works inside, and why it is built this way. The code is
one file, `swaytiles.py`, using only the Python standard library.

## The pieces

```
sway ──events──▶ Daemon.handle ──▶ arrange ──▶ one IPC command per step ──▶ sway
  ▲
  └── tick "layout grid" ── swaytiles grid / swaytiles menu
```

- **The daemon** (`swaytiles` with no arguments) runs for the whole sway
  session. It subscribes to sway's window, workspace, output, binding,
  tick and shutdown events and reacts to each one in turn, on a single
  thread. The user service runs `swaytiles --wait`, which starts before
  sway, waits for a socket that answers, and after sway ends waits for
  the next one, so nothing in the sway config has to start it.
- **The command line** (`swaytiles menu`, `swaytiles LAYOUT`, `swaytiles
  default LAYOUT`, `swaytiles swap`, `swaytiles move`) never touches windows. It sends sway a tick with the payload `layout NAME`,
  sway passes it to every subscriber, and the daemon applies it. The
  daemon stays the only process that changes the tree.
- **`Sway`** is a small client for sway's IPC protocol over the UNIX
  socket, with no dependencies. Reading the tree this way takes about
  0.2 ms, against 2.6 ms for a `swaymsg` process.
- **Keys** are the user's own bindings. `exec swaytiles swap` sends a
  tick with the payload `layout:act master`. A binding can also say `nop
  layout move left`: sway runs it as a no-op and reports it in a binding
  event, which the daemon reads and carries out without a process being
  started.
- **Move keys** are taken over at runtime. `Daemon.adopt` reads the
  config sway loaded (`get_config` for the main file, the files it
  includes from disk), keeps the last binding for each key outside modes,
  and for those that are exactly sway's own `move DIRECTION` or `move
  container to workspace X` sends `bindsym KEYS nop layout move …` with
  the flags the config gave; `move scratchpad` becomes `nop layout hide`
  and `floating toggle|enable|disable` becomes `nop layout float …`. A runtime `bindsym` replaces the config's
  binding and lasts until sway reloads, so `adopt` runs again on the
  reload event, and `give_back` binds the original commands when the
  daemon stops. A move key the reader missed is taken over the first
  time it is used, from its binding event. The config file is never
  edited.

Only one daemon runs per sway session: it holds an `flock` on
`$XDG_RUNTIME_DIR/swaytiles.<socket>.lock` and exits with code 2 if
another one has it, which tells the service not to restart it.

## Memory

`~/.local/bin/swaytiles` is a small launcher (`contrib/swaytiles`) that
imports the module from `~/.local/lib/swaytiles`, so Python compiles it
once (at install, with `py_compile`) and reuses the cached bytecode. Run directly as a script, the file
was compiled at every start and the daemon kept about 5 MB from that.
Modules only the menu, the config command or an error need (`subprocess`,
`tempfile`, `shutil`, `shlex`, `traceback`) are imported where they are
used. Together this took the daemon from about 15 MB to about 6 MB of
its own memory.

## Layouts are pure functions

A layout is a function from the window order to a tree:

```python
master([7, 8, 9])  ==  ("splith", [7, ("splitv", [8, 9])])
```

A tree is either a window id or a `(layout, children)` pair, where the
layout is `splith`, `splitv`, `tabbed`, `stacked` or `float`. `LAYOUTS`
maps each name to its function, and `None` for `default`, which leaves the
workspace alone.

sway's own tree is read into the same form with `shape()`. A few helpers
make the two comparable, since sway wraps and nests containers in ways
that look different but mean the same:

| Helper | What it removes |
| --- | --- |
| `normalize` | empty containers |
| `trimmed` | single-child wrappers at the top |
| `loose` | every single-child split container |
| `plain` | a split nested in a split of the same direction, which looks the same on screen |
| `outline` | `plain(trimmed(tree))`: what the user sees |
| `bare` | the container styles, leaving only the nesting |

All of this runs without sway and is tested in `tests/test_pure.py`.

## State

State is split by how long it should live:

| Where | What | Lifetime |
| --- | --- | --- |
| `~/.local/state/swaytiles.json` | the layout of every workspace and the default for new ones | forever |
| `$XDG_RUNTIME_DIR/swaytiles.<socket>.json` | the last tree built on each workspace (`built`), windows tiled by hand on float workspaces (`kept`) | this sway session; survives a daemon restart |
| memory | the window order, master sizes, float slots, focus history | this daemon |

Window ids are only meaningful inside one sway session, which is why
anything holding them lives in the runtime file, named after the socket.
Both files are written atomically (a temporary file, then `os.replace`)
and checked field by field when read, so a broken file costs the saved
state and nothing else.

## The window order

`Daemon.order` is the list the layouts are fed: the first window is the
master, the rest are the stack in order. New windows are appended. Moves
and swaps edit the list, and a tree changed by hand is read back into it
with `conforming()`, which finds the order that makes the layout produce
the tree the user built, if there is one.

## One pass: `arrange`

Almost every event ends in `arrange()`, which looks at the whole tree and
brings every workspace in line:

1. Give unseen workspaces the default layout, which only `swaytiles
   default LAYOUT` changes. Choosing a layout changes one workspace.
2. `placed`: note which workspace each window is on, and whether the
   event was a window arriving from another workspace.
3. `release`: tile again the windows the daemon had floated, on
   workspaces that are no longer float workspaces.
4. Update the order: drop closed windows, append new ones.
5. `inspect` the workspaces for changes made by hand (see below).
6. `anchors`: point the placement rules at the last window of each
   workspace (see below).
7. For each workspace: `float_all` on float workspaces, `shape_up` on
   tiled ones. Workspaces with a fullscreen window are skipped.
8. `record` the tree now on screen as `built`, save the runtime file, and
   `sync` if anything was moved.

## New windows appear in place

A daemon that reacts to the `new` event moves a window that sway has
already drawn somewhere else, which shows as a jump. swaytiles instead
sets sway rules so that sway puts the window in the right place before
the first frame:

- **Tiled workspaces.** The last window of each managed workspace carries
  a hidden mark, `_layout_after_<hex of the workspace name>`. A rule
  `for_window [workspace="^NAME$" tiling]` moves every new window to that
  mark, so it opens right after the last window of the stack. The mark
  name is held in a sway variable, `$layout_tile_<hex>`, so the daemon
  switches the rule off by setting the variable to `_layout_off` instead
  of removing the rule, which sway cannot do. The mark travels with its
  window, so the rule first takes it off a window that is no longer on
  the workspace (`elsewhere`): a window moved away while the daemon is
  busy, stopped or gone does not draw new windows after it.
- **The next window's place.** The anchor is not always the last window:
  `foresee` works out how the next window reaches its place in the target
  with the steps the rule can take, trying each plan on the model of the
  workspace (see below), simplest first: go right after a window, pass a
  window with a `move` (which keeps both sizes), turn the workspace or
  leave a container with a `move`, and nest in a container of its own.
  The plan is kept in a mark and in
  `$layout_role_<hex>`, `$layout_way_<hex>` and `$layout_inner_<hex>`:
  sway replaces variables only in a command's arguments, so the rule
  cannot hold whole commands, and the role mark it gives the window
  switches the steps on. A lone window takes beforehand the orientation
  the workspace will have with two, which shows nothing. Where the window
  comes in at the master's level, the master is marked
  `_layout_sized_<hex>` and the rule ends with `resize set` to its saved
  share. So in every layout up to 8 windows sway draws a new window in
  its place from the first frame, and the daemon has nothing left to do.
  The exceptions are a grid whose rows are dealt out again as a window
  comes in, from 4 to 5 windows and from 9 to 10: sway draws one frame
  before the daemon rebuilds it.
- **Used once.** sway keeps the rules of every daemon that ran since it
  last read its config, and runs them all. Each step uses itself up: the
  role is set back to none, so an older copy of the rule does nothing
  more.
- **The gate.** sway runs `for_window` rules again when a window's marks,
  title or app id change, for every window that has not matched them yet.
  A global rule gives each new window a short-lived `_layout_fresh` mark,
  every window already open is marked once when the gate is installed,
  and the tile rule only acts on windows with that mark. Without the gate,
  retitling an old window would move it.
- **Float workspaces.** A second rule floats a new window, hides it with
  `opacity 0` and gives it its cascade size and position, again through
  variables (`$layout_float_<hex>_w` and so on) that the daemon updates
  for the next window. `reveal` sets the opacity back to 1 once sway
  reports the new size.
- **Telling its own moves apart.** sway reports `new` before it runs the
  rules, so a rule's `move` arrives after `new`. On `new` the daemon sends
  a tick `layout:sync N` and ignores moves inside a workspace until the
  tick comes back.

On exit, `restore` sets the variables back so the rules do nothing,
removes the marks and makes hidden windows visible again.

## Shaping a workspace

`shape_up` compares the target tree with the one on screen. When they
differ, `assemble` builds the target right under the workspace. It
gathers the windows with `move to mark`, which empties the containers
they were in, and sway removes them. A `layout` command on a window right
under the workspace would wrap all the windows in a new container, so the
workspace's own layout is turned with a `move` across it instead: sway
puts the window first and gives the workspace the move's orientation
(`workspace_rejigger`). A tabbed or stacked layout is one container under
the workspace. A workspace whose windows sit in a container of their own,
left by an older version or by hand, is rebuilt the same way. Building
always from scratch is one path for every case; the sizes of the stack's
windows go back to even, and the master keeps its saved share.

The whole build is sent as one IPC message, so sway applies it as one
transaction and draws no half-built state. `resize`, which gives the
master its saved share of the workspace in `ppt`, goes in the same
message.

**Master size.** `measure` reads the master's share after every binding
and every arrangement that matches the target, and `farewell` reads it
from the close event of the master, so the next master takes the same
size. A window sway has just put next to the master took its room from
all of them alike, so `measure` reads the share from the room the others
have, and a lone master keeps the share it had.

## The model of sway

Some steps go in one message with a command whose result the daemon
cannot read first: the tile rule's plan, the windows left behind by one
that moves away, the window that comes back. So the daemon works them
out on a copy of the workspace's tree in sway's own form, with the few
commands it uses: put a window after another (`move to mark`), take one
out (a container left empty goes, one left with a single child stays),
nest one (`split h, layout`), and `move <direction>` as
`container_move_in_direction` does it: pass a window beside it, leave its
container at the edge, turn the workspace around the others
(`workspace_rejigger`), cross to the next output, then flatten a split
left holding only a split across it. `foresee` tries its plans on it,
`leaving` and `settling` build from it, and `presses` counts with it how
many of sway's own moves take a window to the next output.

## Changes made by hand

sway emits no event for `split` or `layout` commands, and a mouse drag
looks the same as a keyboard move. So the daemon does not try to guess
what the user did. It compares results.

`inspect` compares `built`, the last tree the daemon saw, with the tree
on screen, both with added and removed windows left out. If their
outlines are the same, nothing was changed by hand. If they differ,
`adapt` decides:

- The new tree still fits the layout: accept it, read the new order.
- Only styles changed (`bare` trees are equal), and the result is another
  layout with the same window order: switch to it. Tabbing the stack of
  `master` gives `tabbed-master`.
- Anything else: let the workspace go. Its layout becomes `default`, the
  tile rule is switched off, a notification says so, and sway places new
  windows as it normally would. Picking a layout from the menu takes it
  over again.

So a window dragged with the mouse stays where it is dropped, as in sway:
swapped with another one (dropped on its middle) or dropped between two
windows of the stack it keeps the layout in a new order, and dropped
anywhere else it lets the workspace go. Keyboard moves (`nop layout
move`) always keep the layout.

Bindings whose command contains `split` or `layout` trigger an
`arrange` at once, since no other event follows them. sway emits no
event for the same commands sent with `swaymsg`, nor for a window
swapped with the mouse or dropped on a workspace's edge. Pressing on a
window to drag it focuses it, so for a few seconds after every focus
change the daemon reads the tree five times a second (`WATCH`) and
arranges when a tiled workspace differs from `built`.

Windows arriving from another workspace are exempt: if their drop point
fits the layout they keep it, otherwise they join the end of the stack.

## Moves

`nop layout move <direction>` on a managed workspace never uses sway's
own `move`, whose result depends on the nesting and would often break the
layout:

1. `beside` finds the target: inside tabs or a stack, the next tab;
   otherwise the visible window nearest in that direction, preferring
   one that overlaps at least half the window's side, and then the one
   earlier in the order.
2. `exchange` swaps the two in the order and with sway's `swap
   container`, which keeps the sizes.
3. At the edge, `neighbour` finds the workspace on the next output and
   the window moves there by name. It is recorded as `carried`, until the
   next look at the tree, so it
   does not keep a drop point. If the layout gives the edge it comes in
   through to the master alone (`facing`), it becomes the master, as
   sway's own `move` enters at the near edge: moving right into
   `master` makes it the master. Otherwise, and on a move to a
   workspace by name or number, it joins the end of the stack. A move
   in a direction takes the focus along, like sway's own.

A window that leaves a workspace's layout leaves a hole sway would draw
before the daemon fills it. So wherever the daemon takes the window away
itself, `leaving` adds the steps that put the others in order, worked
out on the model of the workspace without the window, to the same command: a move
to another workspace, `nop layout hide` (`move scratchpad`) and `nop
layout float` (`floating toggle`). A window floated back takes the place
of a new one in the same command (`joining`). A window closed or moved
by sway itself still shows one frame with the hole.

`nop layout show` (`swaytiles show`) brings a window back from the
scratchpad. sway would first tile it next to the focused window, so the
daemon sends `scratchpad show`, `floating disable`, the move after the
last window and the steps of `assemble` as one command,
and records the result as `built`.

`nop layout master` is the same swap, with the master. On workspaces
without a layout, a move is sway's own, except that `presses` predicts
when sway would send the window to another output and the daemon does
it in one step, so it can be floated first when the target is a float
workspace.

## Float workspaces

- Windows are cascaded from two steps above the centre of the output
  down as far as it has room (`stairs`, `cascade`, `free_slot`), and a
  new window never covers another one exactly.
- Positions on a workspace that is not visible are applied when it is
  shown (`pending`).
- Windows are refitted when the workspace moves to an output of a
  different size.
- A window the user tiles by hand is remembered in `kept` and left tiled.
- sway never moves focus from a floating window to another output, so
  on a float workspace a `focus <direction>` binding with no window that
  way is followed by `focus output <direction>` (`escape`). A window
  focus event arriving just before the binding means sway already moved
  the focus, and nothing more is done.

## Staying safe

- A workspace with a fullscreen window is not touched until the window
  leaves fullscreen (`covered`).
- An exception while handling an event is logged to the journal, windows
  hidden by the daemon are made visible (`recover`), and the daemon goes
  on with the next event.
- sway's `shutdown` event or a lost IPC connection ends the daemon
  normally. A crash exits with an error, and the service starts it again.
- `sway reload` clears the rules, so the daemon forgets which ones it set
  and installs them again.

## sway behaviour it relies on

These were measured on sway 1.12 or read in its source:

- No event is emitted for `split` and `layout` commands; a binding event
  follows every `bindsym`, after its command has run.
- Keyboard moves and a mouse drop on a window's edge emit
  `window::move`; a mouse swap or a drop on a workspace's edge emits
  nothing. Only pressing on an unfocused window emits `window::focus`.
- `new` is emitted before `for_window` rules run.
- Variables in a `for_window` command are expanded when the rule is
  defined, unless written `$$name`.
- Only an invalid command aborts a command list; a failing criterion does
  not.
- After `swap container`, the tree's focus lists are stale, so the focused
  window is found by its `focused` flag.
- The workspace node always reports `fullscreen_mode` 1, so fullscreen is
  read from its children.
- `get_config` returns the main config file only, without includes.
- `focus <direction>` from a floating window only looks at other floating
  windows on the same workspace.
- Each step of the tile rule whose mark is not there makes sway log
  "No matching node" at error level, a few lines per new window.

## Tests

`tests/harness.py` starts a headless sway (`WLR_BACKENDS=headless`) with
its own runtime, state and cache directories, the daemon, and small GTK 4
windows as clients (`tests/client.py`). Keys are typed with `wtype`. The
tests read the tree and compare shapes such as `H[w1 V[w2 w3]]`; after
every test, `conftest.py` checks that the daemon logged no error and
let no workspace go (switched it to `default`) unless the test expected it.
The daemon is started through `tests/daemon.py`, which imports it as the
installed launcher does and writes every command message it sends to a
file, so `drawn` can count the transactions an action costs.

| File | Covers |
| --- | --- |
| `test_pure.py` | the layout arithmetic, without sway |
| `test_layouts.py` | every layout while windows open, close and switch |
| `test_manual.py` | changes made by hand, letting a workspace go, the menu and the config command |
| `test_drag.py` | mouse drags, through a virtual pointer (`tests/pointer.py`) |
| `test_promote.py` | swapping with the master |
| `test_float.py` | float workspaces |
| `test_outputs.py` | two outputs of different sizes and scales |
| `test_edge.py`, `test_compat.py` | situations that broke other layout daemons |
| `test_safety.py` | one daemon per session, broken state, crashes |

## Known limits

- When a window closes, or leaves its workspace by a command the daemon
  did not send, sway lays out the remaining windows once before the
  daemon does, so one extra frame may show.
- sway reports no event for a window dropped on a workspace's edge or
  swapped with the mouse, nor for `layout` or `split` sent with `swaymsg`.
  Pressing on a window to drag it focuses it, so the daemon looks at the
  tree for a few seconds after every focus change; anything else is
  noticed at the next event.
- A new floating window takes the next slot in the cascade rather than a
  slot freed by a closed window.
- A grid whose rows are dealt out again as a window comes in (4 to 5
  windows, 9 to 10) shows sway's own placement for one frame.

# swaytiles

Tiling layouts for [sway](https://swaywm.org), one per workspace: master
and stack, centred master, dwindle, spiral, grid, tabs and floating.
Anything you arrange by hand wins over the layout.




https://github.com/user-attachments/assets/06af8569-7a35-4198-a915-d71e2e0efaaf




## Install

```sh
git clone https://github.com/sertacartun/swaytiles
cd swaytiles
./install.sh
```

The script copies swaytiles into `~/.local` and starts it as a systemd
user service. To update, run `git pull` and `./install.sh` again.

swaytiles now runs, and starts with every sway session. Your keys for
moving windows follow the layout. Add two lines to your sway config,
with keys you like, and reload sway (the script prints them too):

```
bindsym $mod+Shift+t exec ~/.local/bin/swaytiles menu --launcher fuzzel
bindsym $mod+m exec ~/.local/bin/swaytiles swap
```

The first opens the layout menu in the program named after `--launcher`:
fuzzel, rofi, wofi, tofi, bemenu, wmenu or dmenu. The second swaps the
focused window with the master.

## Uninstall

```sh
./install.sh --uninstall
```

Then remove the two lines from your sway config.

## More

- [Layouts](docs/layouts.md): each one with a demo.
- [Details](docs/details.md): what swaytiles leaves alone (changes made
  by hand, sizes, fullscreen, dialogs, several outputs), the commands,
  the move keys, the service, running without systemd.
- [Architecture](ARCHITECTURE.md): how it works.

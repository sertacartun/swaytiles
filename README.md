# swaytiles

Tiling layouts for [sway](https://swaywm.org), one per workspace: master
and stack, centred master, dwindle, spiral, grid, tabs and floating.
Anything you arrange by hand wins over the layout.


https://github.com/user-attachments/assets/b0c3f7c7-6b3b-4294-8846-ea12e4dd858a


## Install

```sh
git clone https://github.com/sertacartun/swaytiles
cd swaytiles
mkdir -p ~/.local/bin ~/.config/systemd/user
cp swaytiles.py ~/.local/bin/swaytiles
chmod +x ~/.local/bin/swaytiles
cp contrib/swaytiles.service ~/.config/systemd/user/
systemctl --user enable --now swaytiles.service
```

swaytiles now runs, and starts with every sway session. Your keys for
moving windows follow the layout. Add two lines to your sway config,
with keys you like, and reload sway:

```
bindsym $mod+Shift+t exec ~/.local/bin/swaytiles menu --launcher fuzzel
bindsym $mod+m exec ~/.local/bin/swaytiles swap
```

The first opens the layout menu in the program named after `--launcher`:
fuzzel, rofi, wofi, tofi, bemenu, wmenu or dmenu. The second swaps the
focused window with the master.

## Uninstall

```sh
systemctl --user disable --now swaytiles.service
rm ~/.config/systemd/user/swaytiles.service ~/.local/bin/swaytiles
```

Then remove the two lines from your sway config.

## More

- [Layouts](docs/layouts.md): each one with a demo.
- [Details](docs/details.md): what swaytiles leaves alone (changes made
  by hand, sizes, fullscreen, dialogs, several outputs), the commands,
  the move keys, the service, running without systemd.
- [Architecture](ARCHITECTURE.md): how it works.

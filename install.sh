#!/bin/sh
# Installs swaytiles for the current user, or updates it: run it again
# after a `git pull`. `./install.sh --uninstall` removes it.
set -eu
cd "$(dirname "$0")"

bin=$HOME/.local/bin
lib=$HOME/.local/lib/swaytiles
units=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user

# A user service needs systemd and a session bus to talk to.
service() {
    command -v systemctl >/dev/null && systemctl --user show-environment >/dev/null 2>&1
}

# Under sudo it would install into root's home, not the user's.
if [ -n "${SUDO_USER:-}" ]; then
    echo "install.sh: run it as your own user, without sudo" >&2
    exit 1
fi

case "${1:-}" in
    "") ;;
    --uninstall)
        if service && [ -e "$units/swaytiles.service" ]; then
            systemctl --user disable --now swaytiles.service
        fi
        rm -f "$units/swaytiles.service" "$bin/swaytiles"
        rm -rf "$lib"
        echo "swaytiles is removed. Remove its lines from your sway config too."
        exit 0
        ;;
    *)
        echo "usage: ./install.sh [--uninstall]" >&2
        exit 2
        ;;
esac

if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    echo "install.sh: swaytiles needs Python 3.10 or newer as python3" >&2
    exit 1
fi

install -D -m 644 swaytiles.py "$lib/swaytiles.py"
install -D -m 755 contrib/swaytiles "$bin/swaytiles"
python3 -m py_compile "$lib/swaytiles.py"

if service; then
    install -D -m 644 contrib/swaytiles.service "$units/swaytiles.service"
    systemctl --user daemon-reload
    systemctl --user enable swaytiles.service
    # restart, not start: a running daemon picks up the new version.
    systemctl --user restart swaytiles.service
    echo "swaytiles is installed and runs as a user service."
else
    echo "swaytiles is installed. There is no systemd user session, so start it from"
    echo "your sway config: add the last line below too, without its #."
fi

echo
echo "Add these lines to your sway config, with keys you like, and reload sway:"
echo
"$bin/swaytiles" config

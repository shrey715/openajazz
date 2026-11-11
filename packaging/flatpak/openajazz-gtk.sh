#!/bin/sh
# Flatpak entry point. Settings live in ~/.config/openajazz, shared with the CLI and TUI.
export OPENAJAZZ_CONFIG_DIR="$HOME/.config/openajazz"
export PYTHONPATH="/app/lib/python3.13/site-packages${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m openajazz.gui "$@"

"""openajazz desktop app (GTK 4 + libadwaita).  Run with `openajazz-gtk`."""

from __future__ import annotations

import argparse
import sys

NEEDS_GTK = ("The desktop app needs GTK 4 and libadwaita for Python (PyGObject). "
             "The easiest way is the Flatpak from https://github.com/shrey715/openajazz/releases, "
             "or install your distro's python3-gi, gir1.2-gtk-4.0 and gir1.2-adw-1 packages.")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="openajazz-gtk", description="openajazz desktop app")
    p.add_argument("-n", "--demo", action="store_true", help="demo mode: send nothing to a keyboard")
    p.add_argument("--device", help="hidraw node (default: auto-detect)")
    p.add_argument("-v", "--verbose", action="store_true", help="print every packet sent")
    p.add_argument("--page", default="light",
                   choices=["light", "rgb", "remap", "macro", "music", "settings"])
    args, rest = p.parse_known_args(argv)
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw  # noqa: F401
    except (ImportError, ValueError):
        sys.exit(NEEDS_GTK)
    from .app import App
    return App(args.demo, args.device, args.page, args.verbose).run([sys.argv[0], *rest])

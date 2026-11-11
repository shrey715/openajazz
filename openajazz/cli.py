"""Command-line front end (rich-formatted) for the AK820 Max Plus driver."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.prompt import Confirm
from rich.table import Table
from rich.text import Text

from . import __version__, control, keys, state
from .protocol import DeviceError, Keyboard, find_interfaces

console = Console()
err_console = Console(stderr=True)  # packet logs stay out of --json output

NAMED_COLORS = {
    "off": (0, 0, 0), "black": (0, 0, 0), "white": (255, 255, 255),
    "red": (255, 0, 0), "green": (0, 255, 0), "blue": (0, 0, 255),
    "yellow": (255, 255, 0), "cyan": (0, 255, 255), "magenta": (255, 0, 255),
    "purple": (128, 0, 255), "orange": (255, 128, 0), "pink": (255, 105, 180),
}
SLEEP_OPTIONS = {"5": 1, "15": 2, "30": 3, "never": 4}
LINK_STATES = keys.POWER_STATES  # kept for older imports

# re-exported for the TUI and older callers
parse_macro_events = control.parse_macro_events


def parse_color(s: str) -> tuple[int, int, int]:
    s = s.strip().lower()
    if s in NAMED_COLORS:
        return NAMED_COLORS[s]
    h = s.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        raise argparse.ArgumentTypeError(f"bad colour '{s}' (use RRGGBB, RGB or a name)")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


# -- rendering --------------------------------------------------------------

def render_keyboard(colors: dict[int, tuple[int, int, int]] | None = None,
                    highlight: set[int] | None = None, title="AK820 Max Plus") -> Panel:
    """Draw the 75% layout; keys tinted by LED colour or highlighted by code."""
    rows: dict[int, list] = {}
    for code, legend, led, row, col in keys.PHYSICAL_KEYS:
        rows.setdefault(row, []).append((col, code, legend, led))
    lines = []
    for row in sorted(rows):
        line = Text()
        for _, code, legend, led in sorted(rows[row]):
            width = 9 if legend == "Space" else 6 if len(legend) > 3 else 4
            label = f"{legend:^{width}}"
            if colors is not None:
                r, g, b = colors.get(led, (0, 0, 0))
                fg = "black" if (r * 299 + g * 587 + b * 114) > 128000 else "white"
                style = f"{fg} on rgb({r},{g},{b})"
            elif highlight and code in highlight:
                style = "bold black on bright_yellow"
            else:
                style = "white on grey23"
            line.append(label, style=style)
            line.append(" ")
        lines.append(line)
    return Panel(Group(*lines), title=title, box=box.ROUNDED, expand=False)


def packet_logger(report: bytes):
    err_console.print(Text("→ " + report.rstrip(b"\x00").hex(" "), style="dim cyan"))


def ok(msg: str):
    console.print(f"[bold green]✓[/] {msg}")


def emit_json(data):
    print(json.dumps(data, indent=2))


def open_keyboard(args) -> Keyboard:
    return Keyboard(args.device, dry_run=args.dry_run,
                    log=packet_logger if (args.verbose or args.dry_run) else None)


class Session:
    """Opens the keyboard and the state file; saves state on success."""

    def __init__(self, args):
        self.args = args
        self.st = state.load()

    def __enter__(self):
        self.kb = open_keyboard(self.args)
        return self.kb, self.st

    def __exit__(self, exc_type, *exc):
        self.kb.close()
        if exc_type is None and not self.args.dry_run:
            state.save(self.st)


# -- info commands ----------------------------------------------------------

def cmd_devices(args):
    found = find_interfaces()
    if args.json:
        emit_json([{"path": i.path, "connection": i.connection, "interface": i.interface,
                    "control": i.is_control} for i in found])
        return
    table = Table(title="Detected AK820 interfaces", box=box.SIMPLE_HEAVY)
    for col in ("hidraw", "connection", "usb interface", "role"):
        table.add_column(col)
    for i in found:
        table.add_row(i.path, i.connection, i.interface,
                      "[bold green]control[/]" if i.is_control else "[dim]input[/]")
    if not found:
        console.print("[red]No AK820 Max Plus found.[/] Is the cable or dongle plugged in?")
    else:
        console.print(table)


def show_status(kb: Keyboard, as_json=False):
    st = kb.status()
    if as_json:
        emit_json(None if st is None else
                  {"battery": st.battery, "power": st.power, "power_name": st.power_name,
                   "device": kb.path})
        return
    if st is None:
        console.print(Panel("[red]No reply to status query[/] "
                            "(asleep, or switched off behind the dongle?)", title=kb.path))
        return
    rows = [Text.assemble(("Power:   ", "bold"), st.power_name)]
    if st.online and st.battery is not None:
        colour = "green" if st.battery > 50 else "yellow" if st.battery > 20 else "red"
        rows.append(Text.assemble(("Battery: ", "bold"), (f"{st.battery:3d}%", colour)))
        rows.append(ProgressBar(total=100, completed=st.battery, width=30,
                                complete_style=colour))
    title = f"Status · {kb.path}" + (" · simulated" if kb.dry_run else "")
    console.print(Panel(Group(*rows), title=title, expand=False))


def cmd_status(args):
    with open_keyboard(args) as kb:
        show_status(kb, args.json)


def cmd_modes(args):
    if args.json:
        emit_json([{"mode": m, "name": n, "slug": keys.SLUG_BY_MODE.get(m),
                    "brightness": bool(a & keys.ATTR_BRIGHTNESS),
                    "speed": bool(a & keys.ATTR_SPEED),
                    "direction": bool(a & keys.ATTR_DIRECTION),
                    "color": bool(a & keys.ATTR_COLOR)}
                   for m, (n, a) in keys.LIGHT_MODES.items()])
        return
    table = Table(title="Lighting effects", box=box.SIMPLE_HEAVY)
    for col in ("#", "slug", "name", "bright", "speed", "dir", "colour"):
        table.add_column(col, justify="left" if col in ("slug", "name") else "center")
    tick = lambda attr, bit: "[green]●[/]" if attr & bit else "[dim]·[/]"
    for num, (name, attr) in keys.LIGHT_MODES.items():
        table.add_row(str(num), keys.SLUG_BY_MODE.get(num, ""), name,
                      tick(attr, keys.ATTR_BRIGHTNESS), tick(attr, keys.ATTR_SPEED),
                      tick(attr, keys.ATTR_DIRECTION), tick(attr, keys.ATTR_COLOR))
    console.print(table)
    swatches = Text("colours: ")
    for i, (name, rgb) in enumerate(keys.PALETTE):
        style = "bold" if rgb is None else "black on rgb({},{},{})".format(*rgb)
        swatches.append(f" {i} {name} ", style=style)
        swatches.append(" ")
    console.print(swatches)
    console.print("[dim]directions: left-to-right (0), right-to-left (1)[/]")


def cmd_keys(args):
    if args.json:
        emit_json({"keys": keys.HID_USAGES, "media": keys.MEDIA_CODES,
                   "aliases": keys.ALIASES})
        return
    table = Table(title="Key names", box=box.SIMPLE, show_lines=False)
    table.add_column("keys (for remap sources/targets, custom colours, macros)", max_width=70)
    table.add_column("media (remap targets only)", max_width=40)
    table.add_row(", ".join(keys.HID_USAGES), ", ".join(keys.MEDIA_CODES))
    console.print(table)
    console.print("[dim]aliases: " + ", ".join(f"{a}→{b}" for a, b in keys.ALIASES.items()) + "[/]")


# -- lighting ---------------------------------------------------------------

def cmd_light(args):
    mode = keys.effect_mode(args.effect)
    if mode not in keys.LIGHT_MODES:
        console.print(f"[yellow]Mode {mode} isn't in the vendor list; sending anyway.[/]")
    if mode in (keys.MUSIC_MODE, keys.SCREEN_MODE):
        raise ValueError("music/screen modes are driven by the PC; use `openajazz music`")
    with Session(args) as (kb, st):
        prev = st["light"]
        light = {"mode": mode,
                 "brightness": args.brightness or prev["brightness"],
                 "speed": args.speed or prev["speed"],
                 "direction": keys.direction_value(args.direction) if args.direction
                 else prev["direction"],
                 "color": keys.palette_index(args.color) if args.color else prev["color"]}
        control.apply_light(kb, st, light)
    name = keys.LIGHT_MODES.get(mode, ("?", 0))[0]
    ok(f"{name}: brightness {light['brightness']}, speed {light['speed']}, "
       f"{'right-to-left' if light['direction'] else 'left-to-right'}, "
       f"{keys.PALETTE[light['color']][0]}")


def cmd_lights(args):
    with Session(args) as (kb, st):
        if args.action == "off":
            control.lights_off(kb, st)
            on = False
        elif args.action == "on":
            control.lights_on(kb, st)
            on = True
        else:
            on = control.toggle_lights(kb, st)
    ok("lights on, previous lighting restored" if on else "lights off")


def build_colors(args, base=None) -> dict[int, tuple[int, int, int]]:
    colors = dict(base or {})
    if args.all:
        colors = {led: args.all for _, _, led, _, _ in keys.PHYSICAL_KEYS}
    for spec in args.keys:
        name, _, col = spec.partition("=")
        code = keys.key_code(name)
        if code not in keys.LED_BY_CODE:
            raise KeyError(f"'{name}' is not a key on this keyboard")
        colors[keys.LED_BY_CODE[code]] = parse_color(col)
    return colors


def cmd_custom(args):
    with Session(args) as (kb, st):
        base = None if args.fresh else state.colors_from_json(st["colors"])
        colors = build_colors(args, base)
        console.print(render_keyboard(colors))
        control.upload_custom(kb, st, colors, args.brightness)
    ok(f"custom per-key colours stored ({len(colors)} keys)")


def _stop_like_ctrl_c(*_):
    # `music --stop` and systemd send SIGTERM: stop cleanly and restore the lighting
    raise KeyboardInterrupt


def cmd_music(args):
    from . import service

    if args.stop:
        stopped = service.stop_background()
        ok("music sync stopped, lighting restored" if stopped else "music sync wasn't running")
        return
    if args.background:
        argv = service.music_argv(args.style, args.color, args.sensitivity, args.fps, args.device,
                                    args.dry_run)
        try:
            pid = service.start_background(argv)
        except RuntimeError as e:
            raise ValueError(str(e))
        ok(f"music sync running in the background (pid {pid}). "
           "Close the terminal whenever; stop it with [bold]openajazz music --stop[/]")
        return

    import signal
    from .music import AudioError, BANDS, Visualizer

    color = parse_color(args.color) if args.color else None
    signal.signal(signal.SIGTERM, _stop_like_ctrl_c)
    with Session(args) as (kb, st):
        latest = {"frame": {}, "levels": [0.0] * BANDS}

        def on_frame(frame, levels):
            latest["frame"], latest["levels"] = frame, levels

        errors = []
        try:
            viz = Visualizer(None if args.dry_run else kb, args.style, color, args.fps,
                             args.sensitivity, on_frame=on_frame, on_error=errors.append)
            viz.start()
        except AudioError as e:
            raise ValueError(str(e))
        try:
            if args.quiet:
                print(f"music sync running ({args.style})", flush=True)
                while viz.running:
                    time.sleep(0.5)
            else:
                console.print(f"[bold]music sync[/] · {args.style} · Ctrl-C to stop "
                              "[dim](or run with --background)[/]")
                with Live(console=console, refresh_per_second=15) as live:
                    while viz.running:
                        bars = Text("".join(" ▁▂▃▄▅▆▇█"[min(8, int(l * 8.99))]
                                            for l in latest["levels"]), style="bold magenta")
                        live.update(Group(render_keyboard(latest["frame"], title="live"), bars))
                        time.sleep(1 / 15)
        except KeyboardInterrupt:
            pass
        finally:
            viz.stop()
        if errors:
            raise ValueError(str(errors[0]))
        if not st.get("lights_off"):
            control.lights_on(kb, st)  # put the stored lighting back right away
    if not args.quiet:
        ok("music sync stopped, lighting restored")


def cmd_autostart(args):
    from . import service

    try:
        if args.what == "music":
            service.stop_background()  # one stream at a time
            argv = service.music_argv(args.style, args.color, args.sensitivity, args.fps,
                                      args.device)
            service.enable_autostart(argv)
            ok(f"music sync now starts at login, and it's running now "
               f"([dim]{service.UNIT_PATH}[/]). Turn off with [bold]openajazz autostart off[/]")
        elif args.what == "off":
            ok("autostart removed" if service.disable_autostart() else "autostart wasn't on")
        else:
            bg = service.running_pid()
            console.print(f"autostart: {service.autostart_status()}")
            console.print(f"background run: {'pid ' + str(bg) if bg else 'none'}")
    except (RuntimeError, subprocess.CalledProcessError) as e:
        detail = getattr(e, "stderr", "") or str(e)
        raise ValueError(detail.strip())


# -- settings / keys --------------------------------------------------------

def cmd_sleep(args):
    with Session(args) as (kb, st):
        s = st["settings"]
        control.apply_settings(kb, st, SLEEP_OPTIONS[args.minutes], s["response"], s["fn"])
    ok(f"sleep timer: {args.minutes}{'' if args.minutes == 'never' else ' min'}")


def cmd_response(args):
    with Session(args) as (kb, st):
        s = st["settings"]
        control.apply_settings(kb, st, s["sleep"], args.gear, s["fn"])
    ok(f"key response time: gear {args.gear}")


def cmd_fn_switch(args):
    with Session(args) as (kb, st):
        s = st["settings"]
        control.apply_settings(kb, st, s["sleep"], s["response"], args.mode == "fn")
    ok(f"Fn switch: {args.mode} [dim](no visible effect seen on hardware so far)[/]")


def cmd_remap(args):
    src, dst = keys.key_code(args.src), keys.target_code(args.dst)
    console.print(render_keyboard(highlight={src}))
    with Session(args) as (kb, st):
        control.remap(kb, st, 1 if args.fn else 0, src, dst)
    ok(f"{'Fn+' if args.fn else ''}{keys.code_name(src)} → {keys.code_name(dst)}")


def cmd_restore(args):
    src = keys.key_code(args.src)
    with Session(args) as (kb, st):
        control.restore_key(kb, st, 1 if args.fn else 0, src)
    ok(f"{'Fn+' if args.fn else ''}{keys.code_name(src)} back to default "
       f"({len(st['remaps'])} remaps and {len(st['macros'])} macros re-sent)")


def cmd_macro(args):
    src = keys.key_code(args.src)
    if not args.events:
        raise ValueError("macro needs at least one event")
    raw = " ".join(args.events)
    events = control.parse_macro_events(args.events, args.delay)
    table = Table(title=f"Macro on {'Fn+' if args.fn else ''}{keys.code_name(src)} "
                        f"(repeat ×{args.repeat})", box=box.SIMPLE)
    for col in ("#", "key", "action", "delay"):
        table.add_column(col)
    for i, (code, release, delay) in enumerate(events, 1):
        table.add_row(str(i), keys.code_name(code),
                      "[red]release[/]" if release else "[green]press[/]", f"{delay} ms")
    console.print(table)
    with Session(args) as (kb, st):
        control.set_macro(kb, st, 1 if args.fn else 0, src, raw, args.repeat)
    ok("macro stored")


def cmd_reset_keys(args):
    with Session(args) as (kb, st):
        control.reset_keys(kb, st)
    ok("key assignments reset to default")


def cmd_factory_reset(args):
    if not (args.yes or Confirm.ask("[red]Restore factory settings? All keyboard "
                                    "data will be erased[/]", default=False)):
        return
    with Session(args) as (kb, st):
        control.factory_reset(kb, st)
    ok("factory reset sent")


def cmd_raw(args):
    data = bytes.fromhex("".join(args.hex))
    with open_keyboard(args) as kb:
        resp = kb.raw(data, reply=args.reply)
    if args.reply:
        console.print(resp.hex(" ") if resp else "[yellow]no reply[/]")


UDEV_RULE = """\
# openajazz: let the logged-in user open the config interface (USB interface 0)
# of supported AJAZZ keyboards. The typing interfaces stay root-only, so nothing
# can read keystrokes through this rule.
#   AK820 Max Plus: wired 1a2c:a036, 2.4G dongle 1a2c:8fff
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="1a2c", ATTRS{idProduct}=="a036|8fff", DEVPATH=="*:1.0/*", TAG+="uaccess"
"""
UDEV_PATH = "/etc/udev/rules.d/70-openajazz.rules"


def cmd_udev(args):
    import subprocess
    if not args.install:
        console.print(Panel(UDEV_RULE.strip(), title=UDEV_PATH, expand=False))
        console.print("install it with [bold]openajazz udev --install[/] (asks for sudo), "
                      "then replug the keyboard or dongle")
        return
    subprocess.run(["sudo", "tee", UDEV_PATH], input=UDEV_RULE.encode(),
                   stdout=subprocess.DEVNULL, check=True)
    subprocess.run(["sudo", "udevadm", "control", "--reload"], check=True)
    subprocess.run(["sudo", "udevadm", "trigger", "--subsystem-match=hidraw"], check=True)
    ok(f"wrote {UDEV_PATH}; replug the keyboard or dongle if it still says permission denied")


def cmd_gui(args):
    from .gui import main as gui_main
    argv = (["--demo"] if args.dry_run else []) + (["--device", args.device] if args.device else [])
    raise SystemExit(gui_main(argv))


def cmd_tui(args):
    from .tui import run
    run(args.device, args.dry_run)


# -- argument parsing -------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="openajazz",
        description="Unofficial control for AJAZZ keyboards (AK820 Max Plus). Run with no command for the TUI.")
    p.add_argument("--device", help="hidraw node (default: auto-detect)")
    p.add_argument("-n", "--dry-run", action="store_true",
                   help="simulate: print packets, send nothing, don't touch saved state")
    p.add_argument("-v", "--verbose", action="store_true", help="show packets sent")
    p.add_argument("--json", action="store_true", help="JSON output (status/devices/modes/keys)")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", metavar="command")

    def add(name, func, help, aliases=()):
        s = sub.add_parser(name, help=help, aliases=list(aliases))
        s.set_defaults(func=func)
        return s

    add("tui", cmd_tui, "full-screen interface (default)")
    add("gui", cmd_gui, "desktop app (GTK 4; the Flatpak is the easy way to get it)")
    add("devices", cmd_devices, "list detected interfaces", ["detect"])
    add("status", cmd_status, "battery and power state", ["info"])
    add("modes", cmd_modes, "list lighting effects and colours", ["effects"])
    add("keys", cmd_keys, "list key and media names")
    s = add("udev", cmd_udev, "show or install the Linux permission rule")
    s.add_argument("--install", action="store_true", help="write it to /etc/udev/rules.d (sudo)")

    s = add("light", cmd_light, "set a lighting effect")
    s.add_argument("effect", help="slug (breathing), number (8) or name prefix")
    s.add_argument("-b", "--brightness", type=int, help="1..5")
    s.add_argument("-s", "--speed", type=int, help="1..5")
    s.add_argument("-d", "--direction", help="left-to-right | right-to-left")
    s.add_argument("-c", "--color", help="red green blue yellow magenta cyan white rainbow")

    s = add("lights", cmd_lights, "turn all LEDs off / back on")
    s.add_argument("action", choices=["on", "off", "toggle"])

    s = add("custom", cmd_custom, "store per-key colours (custom mode)")
    s.add_argument("keys", nargs="*", metavar="KEY=COLOR", help="e.g. esc=red w=00ff00")
    s.add_argument("-a", "--all", type=parse_color, help="base colour for every key")
    s.add_argument("-b", "--brightness", type=int)
    s.add_argument("--fresh", action="store_true",
                   help="start from black instead of your last custom colours")

    s = add("music", cmd_music, "audio-reactive lighting (needs openajazz[music])")
    s.add_argument("--style", choices=["spectrum", "pulse", "wave"], default="spectrum")
    s.add_argument("--color", help="single colour instead of rainbow")
    s.add_argument("--sensitivity", type=float, default=1.0)
    s.add_argument("--fps", type=int, default=30)
    s.add_argument("--background", action="store_true", help="keep running after the terminal closes")
    s.add_argument("--stop", action="store_true", help="stop a background run")
    s.add_argument("--quiet", action="store_true", help="no live display (for services)")

    s = add("autostart", cmd_autostart, "start music sync at login (systemd user service)")
    s.add_argument("what", choices=["music", "off", "status"])
    s.add_argument("--style", choices=["spectrum", "pulse", "wave"], default="spectrum")
    s.add_argument("--color", help="single colour instead of rainbow")
    s.add_argument("--sensitivity", type=float, default=1.0)
    s.add_argument("--fps", type=int, default=30)

    s = add("sleep", cmd_sleep, "auto-sleep timer")
    s.add_argument("minutes", choices=list(SLEEP_OPTIONS))
    s = add("response", cmd_response, "key response time gear (1 fastest)")
    s.add_argument("gear", type=int, choices=[1, 2, 3])
    s = add("fn-switch", cmd_fn_switch, "F-row default layer (experimental)")
    s.add_argument("mode", choices=["normal", "fn"])

    s = add("remap", cmd_remap, "assign a key or media function to a key")
    s.add_argument("src")
    s.add_argument("dst")
    s.add_argument("--fn", action="store_true", help="Fn layer")
    s = add("restore", cmd_restore, "put one key back to its default")
    s.add_argument("src")
    s.add_argument("--fn", action="store_true", help="Fn layer")

    s = add("macro", cmd_macro, "store a keyboard macro on a key (options before SRC)")
    s.add_argument("src")
    s.add_argument("events", nargs=argparse.REMAINDER,
                   help="'+a' press, '-a' release, 'a' tap, '@ms' delay")
    s.add_argument("--fn", action="store_true", help="Fn layer")
    s.add_argument("-r", "--repeat", type=int, default=1)
    s.add_argument("--delay", type=int, default=10, help="default delay ms")

    add("reset-keys", cmd_reset_keys, "restore default key assignments")
    s = add("factory-reset", cmd_factory_reset, "restore factory settings")
    s.add_argument("-y", "--yes", action="store_true")

    s = add("raw", cmd_raw, "send cmd+args after the AA55CC33 header")
    s.add_argument("hex", nargs="+")
    s.add_argument("--reply", action="store_true")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if not args.command:
            cmd_tui(args)
            return 0
        args.func(args)
    except (DeviceError, KeyError, ValueError, OSError) as e:
        if args.json:
            emit_json({"error": str(e)})
        else:
            console.print(f"[bold red]error:[/] {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""openajazz desktop app (GTK 4 + libadwaita)."""

from __future__ import annotations

import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from .. import __version__, control, effects, keys, patterns, state  # noqa: E402
from ..protocol import DeviceError, Keyboard, find_interfaces  # noqa: E402
from .board import KeyboardView  # noqa: E402

APP_ID = "io.github.shrey715.openajazz"
UDEV_CMD = ("curl -fsSL https://raw.githubusercontent.com/shrey715/openajazz/main/udev/"
            "70-openajazz.rules | sudo tee /etc/udev/rules.d/70-openajazz.rules "
            "&& sudo udevadm control --reload && sudo udevadm trigger")
SWATCHES = {
    "red": (255, 40, 60), "orange": (255, 128, 0), "yellow": (255, 220, 0),
    "green": (0, 230, 110), "cyan": (0, 220, 255), "blue": (40, 90, 255),
    "purple": (150, 70, 255), "pink": (255, 80, 180), "white": (255, 255, 255), "off": (0, 0, 0),
}
PATTERN_LABELS = {
    "solid": "Solid fill", "rainbow-horizontal": "Rainbow across", "rainbow-vertical": "Rainbow down",
    "rainbow-diagonal": "Rainbow diagonal", "gradient-horizontal": "Gradient across",
    "gradient-vertical": "Gradient down", "highlight-gaming": "WASD highlight", "random": "Random",
}
PAGES = [  # name, title, icon
    ("light", "Lighting", "display-brightness-symbolic"),
    ("rgb", "Per-key", "applications-graphics-symbolic"),
    ("remap", "Remap", "input-keyboard-symbolic"),
    ("macro", "Macros", "media-record-symbolic"),
    ("music", "Music", "audio-x-generic-symbolic"),
    ("settings", "Settings", "emblem-system-symbolic"),
]
HINTS = {
    "light": "Live preview, approximate", "rgb": "Click or drag to paint",
    "remap": "Click the key you want to change", "macro": "Click the key that should run the macro",
    "music": "", "settings": "",
}
LEGEND = {code: legend for code, legend, _, _, _ in keys.PHYSICAL_KEYS}
LED_OF = {code: led for code, _, led, _, _ in keys.PHYSICAL_KEYS}
ARROW_WORDS = {"↑": "Up", "↓": "Down", "←": "Left", "→": "Right"}

CSS = """
.board-card { padding: 14px; border-radius: 16px; background: #08090b; }
.swatch { min-width: 26px; min-height: 26px; padding: 0; border-radius: 999px;
          box-shadow: inset 0 0 0 1px alpha(white, 0.15); }
.swatch:checked { box-shadow: 0 0 0 2px @window_bg_color, 0 0 0 4px @window_fg_color; }
.rainbow { background-image: conic-gradient(#f2555a, #f5b942, #4ade80, #38bdf8, #6366f1, #d946ef, #f2555a); }
.effect-tile { padding: 6px; border-radius: 12px; }
.effect-tile label { font-size: 0.86em; }
.hint { font-size: 0.85em; }
.levels { border-radius: 10px; background: #08090b; }
""" + "".join(
    f".swatch-{name} {{ background: rgb({r},{g},{b}); }}\n" for name, (r, g, b) in SWATCHES.items()
) + "".join(
    f".palette-{i} {{ background: rgb({r},{g},{b}); }}\n"
    for i, (_, rgb) in enumerate(keys.PALETTE) if rgb for r, g, b in [rgb]
)


def key_label(layer: int, code: int) -> str:
    legend = LEGEND.get(code, keys.code_name(code))
    return ("Fn+" if layer else "") + ARROW_WORDS.get(legend, legend)


def rgba_to_rgb(rgba: Gdk.RGBA) -> tuple[int, int, int]:
    return round(rgba.red * 255), round(rgba.green * 255), round(rgba.blue * 255)


def rgb_to_rgba(rgb) -> Gdk.RGBA:
    c = Gdk.RGBA()
    c.parse("rgb({},{},{})".format(*rgb))
    return c


def toggle_group(options, active=None) -> Adw.ToggleGroup:
    group = Adw.ToggleGroup()
    for name, label, icon in options:
        t = Adw.Toggle(name=name, label=label)
        if icon:
            t.set_icon_name(icon)
        group.add(t)
    if active is not None:
        group.set_active_name(active)
    return group


def row_with(title: str, widget: Gtk.Widget, subtitle: str = "") -> Adw.ActionRow:
    row = Adw.ActionRow(title=title, subtitle=subtitle)
    widget.set_valign(Gtk.Align.CENTER)
    row.add_suffix(widget)
    return row


class Window(Adw.ApplicationWindow):
    def __init__(self, app, dry_run=False, device=None, page="light", verbose=False):
        super().__init__(application=app, title="openajazz", default_width=1180, default_height=860)
        self.dry_run, self.device_path = dry_run, device
        self.packet_log = (lambda r: print("->", r.rstrip(bytes(1)).hex(" "), flush=True)) if verbose else None
        self.kb: Keyboard | None = None
        self.lock = threading.Lock()
        self.st = state.load()
        light = dict(self.st["light"])
        if light["mode"] in (keys.MUSIC_MODE, keys.SCREEN_MODE):
            light["mode"] = 7
        self.light = light
        self.paint = {led: tuple(state.colors_from_json(self.st["colors"]).get(led, (0, 0, 0)))
                      for _, _, led, _, _ in keys.PHYSICAL_KEYS}
        self.brush, self.brush2, self.tool = (255, 92, 138), (61, 123, 255), "paint"
        self.selected: set[int] = set()
        self.undo_stack: list[dict] = []
        self.redo_stack: list[dict] = []
        self.live_rgb = False
        self.remap_src = self.macro_src = None
        self.presses: list[tuple[int, float]] = []
        self.music_frame: dict = {}
        self.music_levels = [0.0] * 16
        self.music_rgb = (255, 92, 138)
        self.visualizer = None
        self.t0 = time.monotonic()
        self.page = page
        self._apply_light_id = 0

        css = Gtk.CssProvider()
        css.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.build()
        self.stack.set_visible_child_name(page)
        GLib.timeout_add(33, self.tick)
        GLib.timeout_add_seconds(5, self.poll_status)
        GLib.timeout_add_seconds(3, self.auto_connect)
        self.connect_keyboard(quiet=True)

    # -- layout -------------------------------------------------------------

    def build(self):
        self.toasts = Adw.ToastOverlay()
        view = Adw.ToolbarView()
        self.toasts.set_child(view)
        self.set_content(self.toasts)

        self.stack = Adw.ViewStack()
        header = Adw.HeaderBar()
        self.switcher = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        header.set_title_widget(self.switcher)

        self.device_label = Gtk.Label(label="Not connected")
        dev_box = Gtk.Box(spacing=6)
        dev_box.append(Gtk.Image.new_from_icon_name("input-keyboard-symbolic"))
        dev_box.append(self.device_label)
        self.device_button = Gtk.MenuButton(child=dev_box, tooltip_text="Keyboard")
        pop = Gtk.Popover()
        pbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=10,
                       margin_bottom=10, margin_start=10, margin_end=10)
        self.device_detail = Gtk.Label(label="", wrap=True, max_width_chars=32, xalign=0)
        pbox.append(self.device_detail)
        reconnect = Gtk.Button(label="Reconnect")
        reconnect.connect("clicked", lambda *_: self.connect_keyboard())
        demo = Gtk.Button(label="Demo mode (send nothing)")
        demo.connect("clicked", lambda *_: self.start_demo())
        pbox.append(reconnect)
        pbox.append(demo)
        pop.set_child(pbox)
        self.device_button.set_popover(pop)
        header.pack_start(self.device_button)

        menu = Gio.Menu()
        menu.append("About openajazz", "win.about")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu))
        about = Gio.SimpleAction.new("about", None)
        about.connect("activate", lambda *_: self.show_about())
        self.add_action(about)

        self.lights_button = Gtk.ToggleButton(icon_name="display-brightness-symbolic",
                                              tooltip_text="Lights on or off")
        self.lights_button.set_active(not self.st.get("lights_off"))
        self.lights_button.connect("toggled", self.on_lights_toggled)
        header.pack_end(self.lights_button)

        self.battery_box = Gtk.Box(spacing=4, visible=False)
        self.battery_icon = Gtk.Image.new_from_icon_name("battery-level-100-symbolic")
        self.battery_label = Gtk.Label()
        self.battery_box.append(self.battery_icon)
        self.battery_box.append(self.battery_label)
        header.pack_end(self.battery_box)
        view.add_top_bar(header)

        self.banner = Adw.Banner(title="Keyboard not found. Plug in the cable or the 2.4G dongle.",
                                 button_label="Try demo")
        self.banner.connect("button-clicked", lambda *_: self.start_demo())
        view.add_top_bar(self.banner)

        bar = Adw.ViewSwitcherBar(stack=self.stack)
        view.add_bottom_bar(bar)
        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 1100sp"))
        bp.add_setter(self.switcher, "visible", False)
        bp.add_setter(bar, "reveal", True)
        self.add_breakpoint(bp)

        scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        clamp = Adw.Clamp(maximum_size=1120, tightening_threshold=900)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin_top=18,
                       margin_bottom=24, margin_start=18, margin_end=18)
        board_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, css_classes=["board-card"])
        self.board = KeyboardView(interactive=True)
        self.board.set_size_request(-1, 260)
        self.board.on_key = self.on_board_key
        self.hint = Gtk.Label(css_classes=["dim-label", "hint"])
        board_card.append(self.board)
        board_card.append(self.hint)
        body.append(board_card)

        for name, title, icon in PAGES:
            page = getattr(self, f"build_{name}")()
            self.stack.add_titled_with_icon(page, name, title, icon)
        self.stack.connect("notify::visible-child-name", self.on_page_changed)
        body.append(self.stack)
        clamp.set_child(body)
        scroller.set_child(clamp)
        view.set_content(scroller)

        for name, accel, fn in (("undo", "<Control>z", self.undo), ("redo", "<Control><Shift>z", self.redo)):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", lambda *_a, f=fn: f())
            self.add_action(act)
            self.get_application().set_accels_for_action(f"win.{name}", [accel])

    # -- device -------------------------------------------------------------

    def toast(self, text: str):
        self.toasts.add_toast(Adw.Toast.new(text))

    def connect_keyboard(self, quiet=False):
        if self.kb and not self.kb.dry_run:
            self.kb.close()
        try:
            self.kb = Keyboard(self.device_path, dry_run=self.dry_run, log=self.packet_log)
        except DeviceError as e:
            self.kb = None
            self.set_device_state(None, str(e))
            if not quiet:
                self.toast(str(e))
            return
        if self.dry_run:
            self.set_device_state("Demo mode", "Nothing is sent to a keyboard.")
        else:
            kind = next((i.connection for i in find_interfaces() if i.path == self.kb.path), "")
            self.set_device_state(f"AK820 Max Plus · {kind}" if kind else "AK820 Max Plus",
                                  f"Connected on {self.kb.path}")
        self.poll_status()

    def start_demo(self):
        self.dry_run = True
        self.connect_keyboard()

    def set_device_state(self, name, detail):
        self.device_label.set_label(name or "Not connected")
        self.device_detail.set_label(detail)
        self.banner.set_revealed(name is None)
        if name is None:
            self.battery_box.set_visible(False)

    def auto_connect(self):
        if self.kb is None and not self.dry_run:
            self.connect_keyboard(quiet=True)
        return True

    def run(self, label, fn, after=None, quiet=False):
        """Do one keyboard operation off the UI thread, then save state and report."""
        if self.kb is None:
            self.toast("Connect the keyboard first, or use demo mode")
            return

        def work():
            try:
                with self.lock:
                    fn()
                err = None
            except (DeviceError, OSError, ValueError) as e:
                err = e
            GLib.idle_add(self.finish, label, err, after, quiet)

        threading.Thread(target=work, daemon=True).start()

    def finish(self, label, err, after, quiet):
        if err:
            self.toast(f"{label}: {err}")
            return False
        if not self.dry_run:
            state.save(self.st)
        if not quiet:
            self.toast(label + (" (demo)" if self.dry_run else ""))
        if after:
            after()
        return False

    def poll_status(self):
        if self.kb is None or (self.visualizer and self.visualizer.running):
            return True

        def work():
            try:
                with self.lock:
                    st = self.kb.status()
            except (DeviceError, OSError):
                st = "lost"
            GLib.idle_add(self.show_status, st)

        threading.Thread(target=work, daemon=True).start()
        return True

    def show_status(self, st):
        if st == "lost" and not self.dry_run:
            self.kb = None
            self.set_device_state(None, "Lost the keyboard.")
        elif st and st != "lost" and st.online and st.battery is not None:
            level = min(100, round(st.battery / 10) * 10)
            charging = "-charging" if st.on_charger else ""
            self.battery_icon.set_from_icon_name(f"battery-level-{level}{charging}-symbolic")
            self.battery_label.set_label(f"{st.battery}%")
            self.battery_box.set_tooltip_text(st.power_name)
            self.battery_box.set_visible(True)
        return False

    def on_lights_toggled(self, button):
        if self.kb is None:
            return
        on = button.get_active()
        if on == (not self.st.get("lights_off")):
            return
        self.run("Lights on" if on else "Lights off",
                 lambda: control.lights_on(self.kb, self.st) if on else control.lights_off(self.kb, self.st))

    # -- board ----------------------------------------------------------------

    def on_page_changed(self, *_):
        self.page = self.stack.get_visible_child_name()
        self.hint.set_label(HINTS.get(self.page, ""))
        if self.page == "light" and self.light["mode"] in effects.REACTIVE:
            self.hint.set_label("This effect reacts to typing. Click keys to try it.")
        self.refresh_marks()

    def on_board_key(self, code, led, drag):
        if self.page == "light":
            self.presses.append((led, time.monotonic() - self.t0))
            del self.presses[:-12]
        elif self.page == "rgb":
            self.paint_key(led, drag)
        elif self.page == "remap" and not drag:
            self.remap_src = code
            self.refresh_remaps()
        elif self.page == "macro" and not drag:
            self.pick_macro_key(code)

    def refresh_marks(self):
        marks = {}
        if self.page == "rgb":
            marks = {led: "selected" for led in self.selected}
        elif self.page in ("remap", "macro"):
            table, kind = (self.st["remaps"], "remapped") if self.page == "remap" else (self.st["macros"], "macro")
            layer = self.layer_switch(self.page).get_active()
            for key in table:
                lay, src = map(int, key.split(":"))
                if lay == int(layer):
                    marks[LED_OF.get(src)] = kind
            pick = self.remap_src if self.page == "remap" else self.macro_src
            if pick is not None:
                marks[LED_OF[pick]] = "picked"
        self.board.marks = marks

    def tick(self):
        t = time.monotonic() - self.t0
        if self.page == "rgb":
            frame = dict(self.paint)
        elif self.page in ("remap", "macro"):
            frame = {led: (26, 28, 36) for led in self.paint}
        elif self.page == "music":
            frame = self.music_frame
            self.levels.queue_draw()
        else:
            frame = effects.frame(**self.light, t=t, presses=self.presses,
                                  custom=state.colors_from_json(self.st["colors"]))
        self.board.label_alpha = 0.55 if self.page in ("light", "settings", "music") else 0.85
        self.board.show(frame)
        if self.page == "light" and int(t * 30) % 2 == 0:
            for mode, view in self.minis:
                view.show(effects.frame(**{**self.light, "mode": mode}, t=t,
                                        custom=state.colors_from_json(self.st["colors"])))
        return True

    # -- lighting -------------------------------------------------------------

    def build_light(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        group = Adw.PreferencesGroup(title="Effects")
        self.gallery = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.SINGLE, homogeneous=True,
                                   min_children_per_line=3, max_children_per_line=6,
                                   column_spacing=8, row_spacing=8)
        self.minis = []
        for mode, (name, attr) in keys.LIGHT_MODES.items():
            if mode in (keys.MUSIC_MODE, keys.SCREEN_MODE):
                continue
            tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, css_classes=["card", "effect-tile"])
            mini = KeyboardView(labels=False, glow=False)
            mini.set_size_request(150, 64)
            tile.append(mini)
            label = "Your design" if mode == keys.CUSTOM_MODE else name
            if mode in effects.REACTIVE:
                label += "\n<small>reacts to typing</small>"
            lab = Gtk.Label(label=label, use_markup=True, xalign=0, ellipsize=3, lines=2)
            tile.append(lab)
            child = Gtk.FlowBoxChild(child=tile)
            child.mode = mode
            self.gallery.append(child)
            self.minis.append((mode, mini))
            if mode == self.light["mode"]:
                self.gallery.select_child(child)
        self.gallery.connect("child-activated", self.on_effect_activated)
        group.add(self.gallery)
        box.append(group)

        tune = Adw.PreferencesGroup(title="Tune")
        self.scales = {}
        for key, title in (("brightness", "Brightness"), ("speed", "Speed")):
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1, keys.MAX_LEVEL, 1)
            scale.set_digits(0)
            scale.set_draw_value(True)
            scale.set_size_request(240, -1)
            scale.set_value(self.light[key])
            scale.connect("value-changed", self.on_scale, key)
            self.scales[key] = row_with(title, scale)
            tune.add(self.scales[key])
        self.direction = toggle_group([("0", "Left to right", "go-next-symbolic"),
                                       ("1", "Right to left", "go-previous-symbolic")],
                                      str(self.light["direction"]))
        self.direction.connect("notify::active-name", self.on_direction)
        self.direction_row = row_with("Direction", self.direction)
        tune.add(self.direction_row)
        palette = Gtk.Box(spacing=6)
        self.palette_buttons = []
        first = None
        for i, (name, rgb) in enumerate(keys.PALETTE):
            b = Gtk.ToggleButton(tooltip_text=name, css_classes=["swatch", f"palette-{i}" if rgb else "rainbow"])
            if first:
                b.set_group(first)
            first = first or b
            b.set_active(i == self.light["color"])
            b.connect("toggled", self.on_palette, i)
            palette.append(b)
            self.palette_buttons.append(b)
        self.palette_row = row_with("Colour", palette)
        tune.add(self.palette_row)
        box.append(tune)
        self.sync_light_controls()
        return box

    def sync_light_controls(self):
        attr = keys.LIGHT_MODES[self.light["mode"]][1]
        custom = self.light["mode"] == keys.CUSTOM_MODE
        self.scales["brightness"].set_sensitive(bool(attr & keys.ATTR_BRIGHTNESS) or custom)
        self.scales["speed"].set_sensitive(bool(attr & keys.ATTR_SPEED))
        self.direction_row.set_sensitive(bool(attr & keys.ATTR_DIRECTION))
        self.palette_row.set_sensitive(bool(attr & keys.ATTR_COLOR))

    def on_effect_activated(self, flowbox, child):
        if child.mode == keys.CUSTOM_MODE and not any(max(c) for c in self.paint.values()):
            self.toast("No design yet. Paint one in Per-key, then press Upload design")
            self.stack.set_visible_child_name("rgb")
            return
        self.light["mode"] = child.mode
        self.sync_light_controls()
        self.on_page_changed()
        self.apply_light_soon()

    def on_scale(self, scale, key):
        self.light[key] = int(round(scale.get_value()))
        self.apply_light_soon()

    def on_direction(self, group, _):
        self.light["direction"] = int(group.get_active_name() or 0)
        self.apply_light_soon()

    def on_palette(self, button, index):
        if button.get_active():
            self.light["color"] = index
            self.apply_light_soon()

    def apply_light_soon(self):
        if self._apply_light_id:
            GLib.source_remove(self._apply_light_id)
        self._apply_light_id = GLib.timeout_add(300, self._apply_light)

    def _apply_light(self):
        self._apply_light_id = 0
        light = dict(self.light)
        name = keys.LIGHT_MODES[light["mode"]][0]
        if light["mode"] == keys.CUSTOM_MODE:
            colors = state.colors_from_json(self.st["colors"])
            fn = lambda: control.upload_custom(self.kb, self.st, colors, light["brightness"])
        else:
            fn = lambda: control.apply_light(self.kb, self.st, light)
        if self.kb is not None:
            self.run(name, fn, after=lambda: self.lights_button.set_active(True), quiet=True)
        return False

    # -- per-key --------------------------------------------------------------

    def build_rgb(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        tools = Adw.PreferencesGroup(title="Paint")
        self.tool_group = toggle_group([("paint", "Paint", "document-edit-symbolic"),
                                        ("pick", "Pick colour", "color-select-symbolic"),
                                        ("select", "Select", "edit-select-all-symbolic")], "paint")
        self.tool_group.connect("notify::active-name", lambda g, _: setattr(self, "tool", g.get_active_name()))
        tools.add(row_with("Tool", self.tool_group, "Pick copies a key's colour; select limits patterns"))

        self.brush_button = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False))
        self.brush_button.set_rgba(rgb_to_rgba(self.brush))
        self.brush_button.connect("notify::rgba", lambda b, _: setattr(self, "brush", rgba_to_rgb(b.get_rgba())))
        swatches = Gtk.Box(spacing=6)
        for name, rgb in SWATCHES.items():
            b = Gtk.Button(tooltip_text=name, css_classes=["swatch", f"swatch-{name}"])
            b.connect("clicked", lambda _b, c=rgb: self.brush_button.set_rgba(rgb_to_rgba(c)))
            swatches.append(b)
        swatches.append(self.brush_button)
        tools.add(row_with("Brush", swatches))
        self.brush2_button = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False))
        self.brush2_button.set_rgba(rgb_to_rgba(self.brush2))
        self.brush2_button.connect("notify::rgba", lambda b, _: setattr(self, "brush2", rgba_to_rgb(b.get_rgba())))
        tools.add(row_with("Second colour", self.brush2_button, "Used by gradients and the WASD highlight"))
        hist = Gtk.Box(spacing=6)
        for icon, fn, tip in (("edit-undo-symbolic", self.undo, "Undo (Ctrl+Z)"),
                              ("edit-redo-symbolic", self.redo, "Redo (Ctrl+Shift+Z)")):
            b = Gtk.Button(icon_name=icon, tooltip_text=tip)
            b.connect("clicked", lambda *_a, f=fn: f())
            hist.append(b)
        tools.add(row_with("History", hist))
        box.append(tools)

        pats = Adw.PreferencesGroup(title="Patterns", description="Fills the whole board, or only the selected keys")
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=6, row_spacing=6,
                           max_children_per_line=8)
        for name in patterns.PATTERNS:
            b = Gtk.Button(label=PATTERN_LABELS.get(name, name), css_classes=["pill"])
            b.connect("clicked", lambda _b, n=name: self.fill_pattern(n))
            flow.append(b)
        pats.add(flow)
        clear = Gtk.Button(label="Clear selection", css_classes=["flat"])
        clear.connect("clicked", lambda *_: (self.selected.clear(), self.refresh_marks()))
        pats.set_header_suffix(clear)
        box.append(pats)

        self.presets_group = Adw.PreferencesGroup(title="Saved designs")
        self.preset_entry = Adw.EntryRow(title="Save current design as…", show_apply_button=True)
        self.preset_entry.connect("apply", self.save_preset)
        self.presets_group.add(self.preset_entry)
        self.preset_rows = []
        box.append(self.presets_group)
        self.refresh_presets()

        send = Adw.PreferencesGroup(title="Send to keyboard")
        self.live_row = Adw.SwitchRow(title="Live preview on keyboard",
                                      subtitle="Mirrors every stroke without saving")
        self.live_row.connect("notify::active", self.on_live)
        send.add(self.live_row)
        upload = Gtk.Button(label="Upload design", css_classes=["suggested-action", "pill"],
                            halign=Gtk.Align.CENTER, margin_top=12)
        upload.connect("clicked", lambda *_: self.upload_design())
        send.add(upload)
        box.append(send)
        return box

    def paint_key(self, led, drag):
        if self.tool == "paint":
            if not drag:
                self.snapshot()
            self.paint[led] = tuple(self.brush)
            self.after_paint()
        elif self.tool == "pick" and not drag:
            self.brush_button.set_rgba(rgb_to_rgba(self.paint[led]))
            self.tool_group.set_active_name("paint")
        elif self.tool == "select":
            if drag:
                self.selected.add(led)
            else:
                self.selected ^= {led}
            self.refresh_marks()

    def after_paint(self):
        if self.live_rgb and self.kb is not None:
            colors = dict(self.paint)
            threading.Thread(target=lambda: self._send_live(colors), daemon=True).start()

    def _send_live(self, colors):
        with self.lock:
            try:
                self.kb.send_frame(colors)
            except (DeviceError, OSError, AttributeError):
                pass

    def snapshot(self):
        self.undo_stack.append(dict(self.paint))
        del self.undo_stack[:-60]
        self.redo_stack.clear()

    def undo(self):
        if self.undo_stack:
            self.redo_stack.append(dict(self.paint))
            self.paint.update(self.undo_stack.pop())
            self.after_paint()

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append(dict(self.paint))
            self.paint.update(self.redo_stack.pop())
            self.after_paint()

    def fill_pattern(self, name):
        self.snapshot()
        colors = patterns.generate(name, self.brush, self.brush2)
        for led in (self.selected or list(self.paint)):
            self.paint[led] = colors[led]
        self.after_paint()

    def refresh_presets(self):
        for row in self.preset_rows:
            self.presets_group.remove(row)
        self.preset_rows = []
        for name in state.list_presets():
            row = Adw.ActionRow(title=name, activatable=True)
            row.connect("activated", lambda _r, n=name: self.load_preset(n))
            delete = Gtk.Button(icon_name="user-trash-symbolic", css_classes=["flat"],
                                valign=Gtk.Align.CENTER, tooltip_text="Delete")
            delete.connect("clicked", lambda _b, n=name: self.delete_preset(n))
            row.add_suffix(delete)
            self.presets_group.add(row)
            self.preset_rows.append(row)

    def save_preset(self, entry):
        try:
            slug = state.save_preset(entry.get_text(), self.paint)
        except (OSError, ValueError) as e:
            self.toast(str(e))
            return
        entry.set_text("")
        self.refresh_presets()
        self.toast(f"Saved '{slug}'")

    def load_preset(self, name):
        self.snapshot()
        self.paint.update({led: tuple(c) for led, c in state.load_preset(name).items()})
        self.after_paint()
        self.toast(f"Loaded '{name}'. Upload to store it")

    def delete_preset(self, name):
        try:
            (state.PRESET_DIR / f"{name}.json").unlink()
        except OSError:
            pass
        self.refresh_presets()

    def on_live(self, row, _):
        if self.kb is None:
            row.set_active(False)
            return
        self.live_rgb = row.get_active()
        colors = dict(self.paint)
        if self.live_rgb:
            self.run("Live preview on", lambda: (self.kb.enter_live_mode(), self.kb.send_frame(colors)), quiet=True)
        else:
            self.run("Live preview off", lambda: control.lights_on(self.kb, self.st)
                     if not self.st.get("lights_off") else self.kb.lights_off(), quiet=True)

    def upload_design(self):
        colors = dict(self.paint)
        brightness = self.light["brightness"]

        def done():
            self.light["mode"] = keys.CUSTOM_MODE
            self.live_row.set_active(False)
            for child in self.minis_children():
                if child.mode == keys.CUSTOM_MODE:
                    self.gallery.select_child(child)
            self.sync_light_controls()
        self.run("Design stored on the keyboard",
                 lambda: control.upload_custom(self.kb, self.st, colors, brightness), after=done)

    def minis_children(self):
        child = self.gallery.get_first_child()
        while child:
            yield child
            child = child.get_next_sibling()

    # -- remap ----------------------------------------------------------------

    def layer_switch(self, page):
        return self.remap_fn if page == "remap" else self.macro_fn

    def build_remap(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        group = Adw.PreferencesGroup(title="Change a key")
        self.remap_key_row = Adw.ActionRow(title="Key", subtitle="Click one on the board")
        group.add(self.remap_key_row)
        self.remap_fn = Adw.SwitchRow(title="Fn layer")
        self.remap_fn.connect("notify::active", lambda *_: self.refresh_remaps())
        group.add(self.remap_fn)
        self.targets = list(keys.HID_USAGES) + list(keys.MEDIA_CODES)
        names = Gtk.StringList.new([("media: " if t in keys.MEDIA_CODES else "key: ") + t for t in self.targets])
        self.target_row = Adw.ComboRow(title="New function", model=names, enable_search=True)
        self.target_row.set_expression(Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
        group.add(self.target_row)
        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_top=12)
        restore = Gtk.Button(label="Restore default")
        restore.connect("clicked", lambda *_: self.restore_key())
        apply = Gtk.Button(label="Apply", css_classes=["suggested-action"])
        apply.connect("clicked", lambda *_: self.apply_remap())
        buttons.append(restore)
        buttons.append(apply)
        group.add(buttons)
        box.append(group)

        self.remap_list = Adw.PreferencesGroup(title="Your remaps")
        reset = Gtk.Button(label="Reset all", css_classes=["destructive-action", "flat"])
        reset.connect("clicked", lambda *_: self.confirm("Reset all keys?",
                      "Every remap and macro goes back to the default.", "Reset",
                      lambda: self.run("All keys reset", lambda: control.reset_keys(self.kb, self.st),
                                       after=self.keys_changed)))
        self.remap_list.set_header_suffix(reset)
        self.remap_rows = []
        box.append(self.remap_list)
        self.refresh_remaps()
        return box

    def list_rows(self, group, attr, entries, button_label, on_click):
        for row in getattr(self, attr):
            group.remove(row)
        rows = []
        for title, subtitle, key in entries:
            row = Adw.ActionRow(title=title, subtitle=subtitle)
            b = Gtk.Button(label=button_label, valign=Gtk.Align.CENTER, css_classes=["flat"])
            b.connect("clicked", lambda _b, k=key: on_click(k))
            row.add_suffix(b)
            group.add(row)
            rows.append(row)
        if not entries:
            row = Adw.ActionRow(title="Nothing here yet", css_classes=["dim-label"])
            group.add(row)
            rows.append(row)
        setattr(self, attr, rows)

    def refresh_remaps(self):
        layer = int(self.remap_fn.get_active())
        if self.remap_src is not None:
            cur = self.st["remaps"].get(f"{layer}:{self.remap_src}")
            self.remap_key_row.set_subtitle(key_label(layer, self.remap_src)
                                            + (f", currently {keys.code_name(cur)}" if cur is not None else ""))
        entries = []
        for key, dst in self.st["remaps"].items():
            lay, src = map(int, key.split(":"))
            entries.append((key_label(lay, src), f"now does {keys.code_name(dst)}", (lay, src)))
        self.list_rows(self.remap_list, "remap_rows", entries, "Restore", self.restore_entry)
        self.refresh_marks()

    def apply_remap(self):
        if self.remap_src is None:
            self.toast("Click a key on the board first")
            return
        name = self.targets[self.target_row.get_selected()]
        dst = keys.target_code(name)
        layer, src = int(self.remap_fn.get_active()), self.remap_src
        self.run(f"{key_label(layer, src)} now does {name}",
                 lambda: control.remap(self.kb, self.st, layer, src, dst), after=self.keys_changed)

    def restore_key(self):
        if self.remap_src is None:
            self.toast("Click a key on the board first")
            return
        self.restore_entry((int(self.remap_fn.get_active()), self.remap_src))

    def restore_entry(self, entry):
        layer, src = entry
        self.run(f"{key_label(layer, src)} restored",
                 lambda: control.restore_key(self.kb, self.st, layer, src), after=self.keys_changed)

    def keys_changed(self):
        self.refresh_remaps()
        self.refresh_macros()

    # -- macro ----------------------------------------------------------------

    def build_macro(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        group = Adw.PreferencesGroup(title="Record a macro",
                                     description="+key presses, -key releases, key taps it, @ms waits after")
        self.macro_key_row = Adw.ActionRow(title="Key", subtitle="Click one on the board")
        group.add(self.macro_key_row)
        self.macro_fn = Adw.SwitchRow(title="Fn layer")
        self.macro_fn.connect("notify::active", lambda *_: self.refresh_macros())
        group.add(self.macro_fn)
        self.macro_entry = Adw.EntryRow(title="Events, e.g. +lctrl c -lctrl")
        self.macro_entry.connect("changed", lambda *_: self.preview_macro())
        group.add(self.macro_entry)
        self.macro_preview = Adw.ActionRow(title="Sequence", subtitle="")
        group.add(self.macro_preview)
        self.macro_repeat = Adw.SpinRow.new_with_range(1, 255, 1)
        self.macro_repeat.set_title("Repeat")
        group.add(self.macro_repeat)
        store = Gtk.Button(label="Store macro", css_classes=["suggested-action"],
                           halign=Gtk.Align.END, margin_top=12)
        store.connect("clicked", lambda *_: self.apply_macro())
        group.add(store)
        box.append(group)
        self.macro_list = Adw.PreferencesGroup(title="Your macros")
        self.macro_rows = []
        box.append(self.macro_list)
        self.refresh_macros()
        return box

    def pick_macro_key(self, code):
        self.macro_src = code
        m = self.st["macros"].get(f"{int(self.macro_fn.get_active())}:{code}")
        if m:
            self.macro_entry.set_text(m["events"])
            self.macro_repeat.set_value(m["repeat"])
        self.refresh_macros()

    def preview_macro(self):
        try:
            events = control.parse_macro_events(self.macro_entry.get_text().split())
        except (KeyError, ValueError) as e:
            self.macro_preview.set_subtitle(GLib.markup_escape_text(str(e)))
            return
        parts = []
        for code, release, delay in events:
            color = "#fca5a5" if release else "#86efac"
            word = "release" if release else "press"
            parts.append(f"<span foreground='{color}'>{word} {GLib.markup_escape_text(keys.code_name(code))}</span>"
                         f" <span alpha='60%'>{delay}ms</span>")
        self.macro_preview.set_subtitle(",  ".join(parts))

    def refresh_macros(self):
        layer = int(self.macro_fn.get_active())
        if self.macro_src is not None:
            self.macro_key_row.set_subtitle(key_label(layer, self.macro_src))
        entries = []
        for key, m in self.st["macros"].items():
            lay, src = map(int, key.split(":"))
            sub = m["events"] + (f"  (repeat {m['repeat']})" if m["repeat"] > 1 else "")
            entries.append((key_label(lay, src), GLib.markup_escape_text(sub), (lay, src)))
        self.list_rows(self.macro_list, "macro_rows", entries, "Remove", self.restore_entry)
        self.refresh_marks()

    def apply_macro(self):
        raw = self.macro_entry.get_text().strip()
        try:
            events = control.parse_macro_events(raw.split())
        except (KeyError, ValueError) as e:
            self.toast(str(e))
            return
        if self.macro_src is None or not events:
            self.toast("Click a key and type at least one event")
            return
        layer, src, repeat = int(self.macro_fn.get_active()), self.macro_src, int(self.macro_repeat.get_value())
        self.run(f"Macro on {key_label(layer, src)} stored",
                 lambda: control.set_macro(self.kb, self.st, layer, src, raw, repeat), after=self.keys_changed)

    # -- music ----------------------------------------------------------------

    def build_music(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        group = Adw.PreferencesGroup(title="Music sync",
                                     description="The keyboard follows the sound while this window is open")
        self.music_source = Adw.ComboRow(title="Listen to",
                                         model=Gtk.StringList.new(["What's playing", "Microphone"]))
        group.add(self.music_source)
        self.music_style = toggle_group([("spectrum", "Spectrum", None), ("pulse", "Pulse", None),
                                         ("wave", "Wave", None)], "spectrum")
        self.music_style.connect("notify::active-name", self.on_music_style)
        group.add(row_with("Style", self.music_style))
        self.music_colour = toggle_group([("rainbow", "Rainbow", None), ("single", "Single colour", None)],
                                         "rainbow")
        self.music_colour.connect("notify::active-name", self.on_music_colour)
        group.add(row_with("Colour", self.music_colour))
        self.music_pick = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False))
        self.music_pick.set_rgba(rgb_to_rgba(self.music_rgb))
        self.music_pick.connect("notify::rgba", self.on_music_colour)
        pick_box = Gtk.Box(spacing=6)
        for name, rgb in SWATCHES.items():
            if name == "off":
                continue
            b = Gtk.Button(tooltip_text=name, css_classes=["swatch", f"swatch-{name}"])
            b.connect("clicked", lambda _b, c=rgb: self.music_pick.set_rgba(rgb_to_rgba(c)))
            pick_box.append(b)
        pick_box.append(self.music_pick)
        self.music_pick_row = row_with("Pick", pick_box)
        self.music_pick_row.set_visible(False)
        group.add(self.music_pick_row)
        self.music_sens = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1, 5, 1)
        self.music_sens.set_value(3)
        self.music_sens.set_size_request(240, -1)
        self.music_sens.connect("value-changed", self.on_music_sens)
        group.add(row_with("Sensitivity", self.music_sens))
        self.music_button = Gtk.Button(label="Start", css_classes=["suggested-action", "pill"],
                                       halign=Gtk.Align.CENTER, margin_top=12)
        self.music_button.connect("clicked", lambda *_: self.toggle_music())
        group.add(self.music_button)
        box.append(group)
        levels = Adw.PreferencesGroup(title="Levels")
        self.levels = Gtk.DrawingArea(css_classes=["levels"])
        self.levels.set_size_request(-1, 120)
        self.levels.set_draw_func(self.draw_levels)
        levels.add(self.levels)
        box.append(levels)
        return box

    def music_rgb_now(self):
        if self.music_colour.get_active_name() != "single":
            return None
        return rgba_to_rgb(self.music_pick.get_rgba())

    def on_music_colour(self, *_):
        self.music_pick_row.set_visible(self.music_colour.get_active_name() == "single")
        if self.visualizer:
            self.visualizer.renderer.color = self.music_rgb_now()

    def on_music_style(self, group, _):
        if self.visualizer:
            self.visualizer.renderer.style = group.get_active_name()

    def on_music_sens(self, scale):
        if self.visualizer:
            self.visualizer.analyzer.sensitivity = 0.4 + 0.3 * scale.get_value()

    def toggle_music(self):
        if self.visualizer and self.visualizer.running:
            self.stop_music()
            return
        from ..music import AudioError, Visualizer
        source = "mic" if self.music_source.get_selected() == 1 else "monitor"
        kb = None if (self.kb is None or self.dry_run) else self.kb
        try:
            self.visualizer = Visualizer(kb, self.music_style.get_active_name(), self.music_rgb_now(), 30,
                                         0.4 + 0.3 * self.music_sens.get_value(), source=source,
                                         on_frame=self.on_music_frame,
                                         on_error=lambda e: GLib.idle_add(self.music_failed, e))
            with self.lock:
                self.visualizer.start()
        except (AudioError, DeviceError, OSError) as e:
            self.visualizer = None
            self.toast(str(e))
            return
        self.music_button.set_label("Stop")
        self.music_button.set_css_classes(["destructive-action", "pill"])

    def on_music_frame(self, frame, levels):
        self.music_frame, self.music_levels = frame, levels

    def music_failed(self, error):
        self.toast(f"Music sync stopped: {error}")
        self.stop_music()
        return False

    def stop_music(self):
        if self.visualizer:
            self.visualizer.stop()
            self.visualizer = None
        self.music_frame = {}
        self.music_levels = [0.0] * 16
        self.music_button.set_label("Start")
        self.music_button.set_css_classes(["suggested-action", "pill"])
        if self.kb is not None and not self.st.get("lights_off"):
            self.run("Lighting restored", lambda: control.lights_on(self.kb, self.st), quiet=True)

    def draw_levels(self, area, cr, width, height):
        n = len(self.music_levels)
        w = width / n
        for i, lv in enumerate(self.music_levels):
            h = max(2, height * lv)
            cr.set_source_rgb(1.0, 0.36, 0.54)
            cr.rectangle(i * w + w * 0.2, height - h, w * 0.6, h)
            cr.fill()

    # -- settings -------------------------------------------------------------

    def build_settings(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        s = self.st["settings"]
        group = Adw.PreferencesGroup(title="Keyboard")
        self.sleep_row = Adw.ComboRow(title="Sleep after",
                                      model=Gtk.StringList.new(["5 minutes", "15 minutes", "30 minutes", "Never"]))
        self.sleep_row.set_selected(s["sleep"] - 1)
        self.response_row = Adw.ComboRow(title="Key response",
                                         model=Gtk.StringList.new(["Gear 1 (fastest)", "Gear 2", "Gear 3"]))
        self.response_row.set_selected(s["response"] - 1)
        self.fn_row = Adw.SwitchRow(title="Fn switch", subtitle="No visible effect seen in hardware tests so far",
                                    active=s["fn"])
        for row, sig in ((self.sleep_row, "notify::selected"), (self.response_row, "notify::selected"),
                         (self.fn_row, "notify::active")):
            row.connect(sig, lambda *_: self.apply_settings())
            group.add(row)
        box.append(group)

        perms = Adw.PreferencesGroup(title="Linux permissions",
                                     description="If the app can't open the keyboard, run this once in a terminal, "
                                                 "then replug the keyboard or dongle")
        copy = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER,
                          css_classes=["flat"], tooltip_text="Copy command")
        copy.connect("clicked", lambda *_: (self.get_clipboard().set(UDEV_CMD), self.toast("Copied")))
        row = Adw.ActionRow(title="udev rule", subtitle=GLib.markup_escape_text(UDEV_CMD), subtitle_selectable=True)
        row.add_suffix(copy)
        perms.add(row)
        box.append(perms)

        danger = Adw.PreferencesGroup(title="Reset")
        factory = Gtk.Button(label="Restore factory settings", css_classes=["destructive-action"],
                             halign=Gtk.Align.START)
        factory.connect("clicked", lambda *_: self.confirm(
            "Restore factory settings?", "Everything stored on the keyboard gets erased.", "Restore",
            lambda: self.run("Factory reset done", lambda: control.factory_reset(self.kb, self.st),
                             after=self.keys_changed)))
        danger.add(factory)
        box.append(danger)
        return box

    def apply_settings(self):
        sleep, response = self.sleep_row.get_selected() + 1, self.response_row.get_selected() + 1
        fn = self.fn_row.get_active()
        if self.kb is not None:
            self.run("Settings saved", lambda: control.apply_settings(self.kb, self.st, sleep, response, fn))

    # -- dialogs --------------------------------------------------------------

    def confirm(self, heading, body, action_label, on_yes):
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("yes", action_label)
        dialog.set_response_appearance("yes", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", lambda _d, r: on_yes() if r == "yes" else None)
        dialog.present(self)

    def show_about(self):
        Adw.AboutDialog(application_name="openajazz", application_icon=APP_ID, version=__version__,
                        developer_name="Shreyas Deb", license_type=Gtk.License.MIT_X11,
                        website="https://github.com/shrey715/openajazz",
                        issue_url="https://github.com/shrey715/openajazz/issues",
                        comments="Unofficial control for AJAZZ keyboards. Not affiliated with AJAZZ.",
                        ).present(self)

    def do_close_request(self):
        if self.visualizer:
            self.stop_music()
        return False


class App(Adw.Application):
    def __init__(self, dry_run=False, device=None, page="light", verbose=False):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.opts = (dry_run, device, page, verbose)
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.PREFER_DARK)

    def do_activate(self):
        win = self.props.active_window or Window(self, *self.opts)
        win.present()

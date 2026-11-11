"""Full-screen Textual interface for the AK820 Max Plus."""

from __future__ import annotations

import threading
import time

from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import (Button, DataTable, Footer, Header, Input, Label, Markdown,
                             OptionList, RadioButton, RadioSet, RichLog, Select, Static,
                             Switch, TabbedContent, TabPane)
from textual.widgets.option_list import Option

from . import __version__, control, effects, keys, patterns, state
from .cli import NAMED_COLORS, parse_color
from .protocol import DeviceError, Keyboard, find_interfaces

TARGET_OPTIONS = ([(f"key · {n}", c) for n, c in keys.HID_USAGES.items()] +
                  [(f"media · {n}", c) for n, c in keys.MEDIA_CODES.items()])
LEGEND_BY_CODE = {code: legend for code, legend, _, _, _ in keys.PHYSICAL_KEYS}
SWATCHES = ["red", "orange", "yellow", "green", "cyan", "blue", "purple", "pink",
            "white", "off"]
SLEEP_LABELS = ["5 min", "15 min", "30 min", "never"]
TABS = ["tab-light", "tab-rgb", "tab-remap", "tab-macro", "tab-music", "tab-settings"]
UNDO_LIMIT = 50

HELP = f"""\
# openajazz {__version__}

Unofficial. The keyboard can't report its settings back, so this app shows
what it last sent (saved in `~/.config/openajazz`, shared with the CLI).

| key | action |
|---|---|
| `1`–`6` | switch tabs |
| `ctrl+s` | apply the current tab |
| `o` | lights off / on |
| `ctrl+z` / `ctrl+y` | undo / redo in the per-key painter |
| `r` | refresh battery |
| `l` | show / hide the packet log |
| `ctrl+p` | command palette, themes |
| `q` | quit |

**Lighting.** Browse effects on the left; the preview below is an
approximation of what the keyboard draws. Enter or `ctrl+s` applies.

**Per-key RGB.** Pick a brush colour, then click or drag across keys to paint.
*Pick colour* copies a key's colour into the brush; *select* marks keys so a
pattern only fills those. With *live on keyboard* on, every stroke shows up on
the real keyboard right away; upload to store it.

**Remap / macro.** Click the key you want to change on the map. Restoring a
key resets the keymap and re-sends your other remaps and macros, like the
vendor app does.

**Music.** Needs the music extra (`uv tool install 'openajazz[music]'`) and PipeWire/Pulse.
Pick *single colour* to light everything in one colour you choose.
"""


def rgb_hex(rgb) -> str:
    return "{:02x}{:02x}{:02x}".format(*rgb)


def text_color(rgb) -> str:
    r, g, b = rgb
    return "black" if (r * 299 + g * 587 + b * 114) > 128000 else "white"


def key_label(layer: int, code: int) -> str:
    return f"{'Fn+' if layer else ''}{LEGEND_BY_CODE.get(code, keys.code_name(code))}"


# -- widgets ----------------------------------------------------------------

class KeyCap(Static):
    def __init__(self, code: int, legend: str, led: int):
        super().__init__(legend, classes="keycap")
        self.styles.width = patterns.key_width(legend)
        self.code, self.led = code, led
        self.rgb: tuple[int, int, int] | None = None

    @property
    def keymap(self) -> "KeyboardMap":
        return self.parent.parent

    def paint(self, rgb):
        self.rgb = tuple(rgb)
        self.styles.background = "rgb({},{},{})".format(*self.rgb)
        self.styles.color = text_color(self.rgb)

    def on_mouse_down(self, event: events.MouseDown):
        if event.button == 1:
            self.keymap.dragging = True
            self.post_message(KeyboardMap.KeyPressed(self.keymap, self, drag=False))

    def on_enter(self, event: events.Enter):
        if event.node is self and self.keymap.dragging:
            self.post_message(KeyboardMap.KeyPressed(self.keymap, self, drag=True))


class KeyboardMap(Vertical):
    """Clickable 75% layout.  Posts KeyPressed on click and while dragging."""

    class KeyPressed(Message):
        def __init__(self, keymap: "KeyboardMap", cap: KeyCap, drag: bool):
            super().__init__()
            self.keymap, self.cap, self.drag = keymap, cap, drag

        @property
        def control(self):
            return self.keymap

    class StrokeEnded(Message):
        def __init__(self, keymap: "KeyboardMap"):
            super().__init__()
            self.keymap = keymap

        @property
        def control(self):
            return self.keymap

    def __init__(self, **kwargs):
        super().__init__(classes="keymap", **kwargs)
        self.dragging = False

    def compose(self) -> ComposeResult:
        rows: dict[int, list] = {}
        for code, legend, led, row, col in keys.PHYSICAL_KEYS:
            rows.setdefault(row, []).append((col, code, legend, led))
        for row in sorted(rows):
            with Horizontal(classes="keyrow"):
                for _, code, legend, led in sorted(rows[row]):
                    yield KeyCap(code, legend, led)

    def caps(self) -> list[KeyCap]:
        return list(self.query(KeyCap))

    def cap(self, code: int) -> KeyCap:
        return next(c for c in self.caps() if c.code == code)

    def end_stroke(self):
        if self.dragging:
            self.dragging = False
            self.post_message(self.StrokeEnded(self))

    def on_mouse_up(self):
        self.end_stroke()


class KeyboardPreview(Static):
    """Compact colour-only keyboard drawn from the vendor key rectangles."""

    SCALE = 0.125  # terminal columns per vendor pixel

    def __init__(self, **kwargs):
        super().__init__(classes="preview", **kwargs)
        self.frame: dict[int, tuple[int, int, int]] = {}
        rows: dict[int, list] = {}
        x0 = min(x for x, _, _, _ in keys.KEY_RECTS.values())
        for _, _, led, row, _ in keys.PHYSICAL_KEYS:
            x, _, w, _ = keys.KEY_RECTS[led]
            start = round((x - x0) * self.SCALE)
            rows.setdefault(row, []).append((start, max(2, round(w * self.SCALE) - 1), led))
        self.rows = [sorted(rows[r]) for r in sorted(rows)]

    def show(self, frame: dict[int, tuple[int, int, int]]):
        self.frame = frame
        self.refresh()

    def render(self) -> Text:
        text = Text()
        for i, row in enumerate(self.rows):
            col = 0
            for start, width, led in row:
                if start > col:
                    text.append(" " * (start - col))
                    col = start
                r, g, b = self.frame.get(led, (24, 24, 24))
                text.append(" " * width, style=f"on rgb({r},{g},{b})")
                col += width
            if i < len(self.rows) - 1:
                text.append("\n\n")
        return text


class LevelPicker(Widget, can_focus=True):
    """Five clickable blocks; left/right arrows when focused."""

    BINDINGS = [("left", "step(-1)", "less"), ("right", "step(1)", "more")]
    value = reactive(1)

    class Changed(Message):
        def __init__(self, picker: "LevelPicker", value: int):
            super().__init__()
            self.picker, self.value = picker, value

        @property
        def control(self):
            return self.picker

    def __init__(self, value: int, maximum: int = keys.MAX_LEVEL, **kwargs):
        super().__init__(**kwargs)
        self.maximum = maximum
        self.set_reactive(LevelPicker.value, value)

    def render(self) -> Text:
        text = Text()
        for i in range(1, self.maximum + 1):
            filled = i <= self.value
            text.append("███ " if filled else "░░░ ",
                        style="grey42" if self.disabled else
                        ("bold #f0a030" if filled else "grey35"))
        text.append(f" {self.value}/{self.maximum}",
                    style="grey42" if self.disabled else "bold")
        return text

    def watch_value(self, value: int):
        self.post_message(self.Changed(self, value))

    def on_click(self, event: events.Click):
        if not self.disabled and event.x < self.maximum * 4:
            self.value = event.x // 4 + 1

    def action_step(self, delta: int):
        self.value = min(max(self.value + delta, 1), self.maximum)


class Swatch(Static):
    class Picked(Message):
        def __init__(self, rgb):
            super().__init__()
            self.rgb = rgb

    def __init__(self, name: str):
        super().__init__(name, classes="swatch")
        self.rgb = NAMED_COLORS[name]
        self.styles.background = "rgb({},{},{})".format(*self.rgb)
        self.styles.color = text_color(self.rgb)
        self.tooltip = f"#{rgb_hex(self.rgb)}"

    def on_click(self):
        self.post_message(self.Picked(self.rgb))


class Confirm(ModalScreen[bool]):
    BINDINGS = [("escape", "dismiss(False)", "cancel")]

    def __init__(self, question: str):
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self.question)
            with Horizontal(id="dialog-buttons"):
                yield Button("Cancel", id="no")
                yield Button("Yes, do it", id="yes", variant="error")

    @on(Button.Pressed)
    def answer(self, event: Button.Pressed):
        self.dismiss(event.button.id == "yes")


class HelpScreen(ModalScreen):
    BINDINGS = [("escape,question_mark,f1", "dismiss", "close")]

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="help"):
            yield Markdown(HELP)
            yield Label("[dim]esc to close[/]")


# -- app --------------------------------------------------------------------

class AK820App(App):
    TITLE = "openajazz"
    SUB_TITLE = "AK820 Max Plus · unofficial"
    CSS = """
    Screen { background: $background; }
    #statusbar { height: 3; padding: 0 1; border: round $primary; }
    #statusbar > Static { width: auto; margin: 0 3 0 0; }
    TabbedContent { height: 1fr; }
    TabbedContent > ContentSwitcher { height: 1fr; }
    TabPane { height: 1fr; padding: 0 1; }
    #log { height: 8; border: round $secondary; }
    #log.hidden { display: none; }

    .keymap { height: auto; width: auto; margin: 1 0; padding: 0 1;
              border: round $panel-lighten-2; }
    .keyrow { height: 3; width: auto; }
    .keycap { height: 3; content-align: center middle; margin: 0 1 0 0;
              background: #1c1c1c; color: $text-muted; }
    .keycap:hover { text-style: bold; }
    .keycap.selected { outline: round $warning; text-style: bold underline; }
    .keycap.picked { outline: heavy $success; text-style: bold; }
    .keycap.remapped { background: $secondary 60%; color: $text; }
    .keycap.has-macro { background: $accent 50%; color: $text; }
    .preview { width: auto; height: auto; padding: 1 2; border: round $panel-lighten-1;
               margin: 1 0; }

    .row { height: auto; margin: 0 0 1 0; }
    .caption { color: $text-muted; }
    .field { width: 14; padding: 1 0 0 0; }
    LevelPicker { width: 30; height: 1; margin: 1 0 0 0; }
    LevelPicker:focus { background: $boost; }
    Select { width: 34; }

    #light-body { height: 1fr; }
    #mode-list { width: 38; height: 1fr; border: round $panel-lighten-1; }
    #light-params { width: 1fr; padding: 0 2; }
    #mode-title { text-style: bold; color: $accent; margin: 1 0 0 0; }

    #rgb-tools { height: auto; }
    .swatch { width: 8; height: 1; content-align: center middle; margin: 0 1 0 0; }
    #brush, #brush2 { width: 18; height: 3; content-align: center middle; border: tall $panel; }
    #hexcolor, #hexcolor2 { width: 18; margin: 0 2 0 0; }
    #tool { layout: horizontal; width: auto; height: auto; border: none; padding: 0;
            background: transparent; margin: 1 0 0 0; }
    #tool RadioButton { width: auto; margin: 0 3 0 0; background: transparent; }
    #pattern, #preset-load { width: 44; }
    #preset-name { width: 22; }
    #dirty { color: $warning; padding: 1 0 0 1; }
    #live-rgb-row Label { padding: 1 1 0 0; }

    #remap-side, #macro-side { width: 1fr; min-width: 34; padding: 0 1; }
    #remap-side Button, #macro-side Button { min-width: 12; }
    #remap-target { width: 40; }
    #remap-table, #macro-table { height: 10; }
    #macro-preview { height: auto; min-height: 1; margin: 1 0; }

    #music-controls { width: 44; padding: 0 1; }
    #music-side { width: 1fr; }
    #music-bars { height: 3; padding: 0 2; }
    #music-sens { width: 12; }
    #music-swatch { width: 6; height: 3; margin: 0 1 0 0; }
    #music-hex { width: 18; }
    #music-swatches .swatch { width: 4; }
    .music-single-off { display: none; }

    #settings-row { height: auto; }
    #settings-row RadioSet { width: 24; margin: 1 2 1 0; }

    Confirm, HelpScreen { align: center middle; }
    #dialog { width: 62; height: auto; border: thick $error; padding: 1 2;
              background: $surface; }
    #dialog-buttons { height: auto; margin: 1 0 0 0; align-horizontal: right; }
    #help { width: 78; height: 85%; border: thick $primary; padding: 1 2;
            background: $surface; }
    Button { margin: 0 1 0 0; }
    """
    BINDINGS = [
        Binding("ctrl+s", "apply", "Apply"),
        Binding("o", "toggle_lights", "Lights"),
        Binding("r", "refresh_status", "Battery"),
        Binding("l", "toggle_log", "Log"),
        Binding("question_mark,f1", "help", "Help"),
        Binding("q", "quit", "Quit"),
        Binding("ctrl+z", "undo", "Undo", show=False),
        Binding("ctrl+y,ctrl+shift+z", "redo", "Redo", show=False),
        *[Binding(str(i + 1), f"tab({i})", show=False) for i in range(len(TABS))],
    ]

    def __init__(self, device: str | None = None, dry_run: bool = False):
        super().__init__()
        self.device_path, self.dry_run = device, dry_run
        self.kb: Keyboard | None = None
        self.lock = threading.Lock()
        self.state = state.load()
        self.brush = (255, 255, 255)
        self.brush2 = (0, 0, 40)
        self.remap_src: int | None = None
        self.macro_src: int | None = None
        self.undo_stack: list[dict] = []
        self.redo_stack: list[dict] = []
        self.live_rgb = False
        self.visualizer = None
        self.t0 = time.monotonic()

    # -- layout ------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="statusbar"):
            yield Static("[yellow]●[/] connecting…", id="conn")
            yield Static("", id="battery")
            yield Static("", id="power")
            yield Static("", id="lights")
        with TabbedContent():
            with TabPane("1 Lighting", id="tab-light"):
                yield from self.compose_lighting()
            with TabPane("2 Per-key RGB", id="tab-rgb"):
                yield from self.compose_rgb()
            with TabPane("3 Remap", id="tab-remap"):
                yield from self.compose_remap()
            with TabPane("4 Macro", id="tab-macro"):
                yield from self.compose_macro()
            with TabPane("5 Music", id="tab-music"):
                yield from self.compose_music()
            with TabPane("6 Settings", id="tab-settings"):
                yield from self.compose_settings()
        yield RichLog(id="log", markup=True, wrap=True, classes="hidden")
        yield Footer()

    def compose_lighting(self) -> ComposeResult:
        light = self.state["light"]
        with Horizontal(id="light-body"):
            yield OptionList(*[Option(f"{n:>2}  {name}", id=str(n))
                               for n, (name, _) in keys.LIGHT_MODES.items()
                               if n not in (keys.MUSIC_MODE, keys.SCREEN_MODE)],
                             id="mode-list")
            with VerticalScroll(id="light-params"):
                yield Label("", id="mode-title")
                yield Label("", id="mode-hint", classes="caption")
                with Horizontal(classes="row"):
                    yield Label("Brightness", classes="field")
                    yield LevelPicker(light["brightness"], id="brightness")
                with Horizontal(classes="row"):
                    yield Label("Speed", classes="field")
                    yield LevelPicker(light["speed"], id="speed")
                with Horizontal(classes="row"):
                    yield Label("Direction", classes="field")
                    yield Select([("left to right", 0), ("right to left", 1)],
                                 value=light["direction"] if light["direction"] in (0, 1) else 0,
                                 allow_blank=False, id="direction")
                with Horizontal(classes="row"):
                    yield Label("Colour", classes="field")
                    yield Select([(name, i) for i, (name, _) in enumerate(keys.PALETTE)],
                                 value=light["color"] if light["color"] < len(keys.PALETTE)
                                 else keys.RAINBOW, allow_blank=False, id="colorindex")
                yield KeyboardPreview(id="light-preview")
                yield Button("Apply effect", id="apply-light", variant="primary")

    def compose_rgb(self) -> ComposeResult:
        with VerticalScroll():
            yield KeyboardMap(id="rgb-map")
            with Vertical(id="rgb-tools"):
                with Horizontal(classes="row"):
                    for name in SWATCHES:
                        yield Swatch(name)
                with Horizontal(classes="row"):
                    yield Static("", id="brush")
                    yield Input(placeholder="hex, enter", id="hexcolor")
                    yield Static("", id="brush2")
                    yield Input(placeholder="hex, enter", id="hexcolor2")
                with Horizontal(classes="row"):
                    yield Label("Tool", classes="field")
                    with RadioSet(id="tool"):
                        yield RadioButton("paint", value=True, name="paint")
                        yield RadioButton("pick colour", name="pick")
                        yield RadioButton("select", name="select")
                with Horizontal(classes="row"):
                    yield Select([(desc, key) for key, desc in patterns.PATTERNS.items()],
                                 value="rainbow-horizontal", allow_blank=False, id="pattern")
                    yield Button("Fill pattern", id="fill-pattern")
                    yield Button("Clear selection", id="sel-none")
                    yield Button("Undo", id="undo")
                    yield Button("Redo", id="redo")
                with Horizontal(classes="row"):
                    yield Input(placeholder="preset name", id="preset-name")
                    yield Button("Save preset", id="save-preset")
                    yield Select([], prompt="load preset…", id="preset-load")
                with Horizontal(classes="row", id="live-rgb-row"):
                    yield Button("Upload to keyboard", id="upload-rgb", variant="primary")
                    yield Label("live on keyboard")
                    yield Switch(id="live-rgb")
                    yield Label("", id="dirty")

    def compose_remap(self) -> ComposeResult:
        with Horizontal():
            yield KeyboardMap(id="remap-map")
            with VerticalScroll(id="remap-side"):
                yield Label("[b]Key[/b]  click one on the map", id="remap-key")
                with Horizontal(classes="row"):
                    yield Label("Fn layer", classes="field")
                    yield Switch(id="remap-fn")
                yield Label("New function [dim](type to search)[/]")
                yield Select(TARGET_OPTIONS, prompt="choose…", id="remap-target")
                with Horizontal(classes="row"):
                    yield Button("Apply", id="apply-remap", variant="primary")
                    yield Button("Restore", id="restore-key")
                yield Label("[b]Your remaps[/b]")
                yield DataTable(id="remap-table", cursor_type="row")
                yield Button("Reset all keys", id="reset-keys", variant="warning")

    def compose_macro(self) -> ComposeResult:
        with Horizontal():
            yield KeyboardMap(id="macro-map")
            with VerticalScroll(id="macro-side"):
                yield Label("[b]Key[/b]  click one on the map", id="macro-key")
                with Horizontal(classes="row"):
                    yield Label("Fn layer", classes="field")
                    yield Switch(id="macro-fn")
                yield Label("Events")
                yield Label("+key press · -key release · key tap · @ms delay",
                            classes="caption")
                yield Input(placeholder="+lctrl c -lctrl", id="macro-events")
                yield Static(id="macro-preview")
                with Horizontal(classes="row"):
                    yield Label("Repeat", classes="field")
                    yield Input("1", id="macro-repeat", type="integer")
                yield Button("Store macro", id="apply-macro", variant="primary")
                yield Label("[b]Your macros[/b]")
                yield DataTable(id="macro-table", cursor_type="row")

    def compose_music(self) -> ComposeResult:
        with Horizontal():
            with VerticalScroll(id="music-controls"):
                yield Label("[b]Music sync[/b]")
                yield Label("reacts to what your speakers play", classes="caption")
                with Horizontal(classes="row"):
                    yield Label("Style", classes="field")
                    yield Select([("spectrum", "spectrum"), ("pulse", "pulse"),
                                  ("wave", "wave")], value="spectrum", allow_blank=False,
                                 id="music-style")
                with Horizontal(classes="row"):
                    yield Label("Colour", classes="field")
                    yield Select([("rainbow", "rainbow"), ("single colour", "single")],
                                 value="rainbow", allow_blank=False, id="music-color")
                with Horizontal(classes="row", id="music-pick-row"):
                    yield Label("Pick", classes="field")
                    yield Static("", id="music-swatch")
                    yield Input(value="ff5c8a", placeholder="RRGGBB or name", id="music-hex")
                with Horizontal(classes="row", id="music-swatches"):
                    for name in ("red", "orange", "yellow", "green", "cyan", "blue", "purple", "pink", "white"):
                        yield Swatch(name)
                with Horizontal(classes="row"):
                    yield Label("Sensitivity", classes="field")
                    yield LevelPicker(3, id="music-sens-level")
                with Horizontal(classes="row"):
                    yield Button("Start", id="music-start", variant="success")
                    yield Button("Stop", id="music-stop", variant="error", disabled=True)
            with Vertical(id="music-side"):
                yield KeyboardPreview(id="music-preview")
                yield Static("", id="music-bars")

    def compose_settings(self) -> ComposeResult:
        s = self.state["settings"]
        with Horizontal(id="settings-row"):
            with RadioSet(id="sleep"):
                for i, label in enumerate(SLEEP_LABELS, 1):
                    yield RadioButton(label, value=s["sleep"] == i, name=str(i))
            with RadioSet(id="response"):
                for g in (1, 2, 3):
                    yield RadioButton(f"gear {g}", value=s["response"] == g, name=str(g))
            with RadioSet(id="fnswitch"):
                yield RadioButton("normal", value=not s["fn"], name="0")
                yield RadioButton("fn", value=s["fn"], name="1")
        yield Label("[dim]gear 1 is the fastest response. the fn switch had no visible "
                    "effect in hardware tests so far.[/]")
        with Horizontal(classes="row"):
            yield Button("Apply settings", id="apply-settings", variant="primary")
            yield Button("Factory reset", id="factory", variant="error")

    # -- lifecycle ---------------------------------------------------------

    def on_mount(self):
        for rid, title in (("#sleep", "Sleep after"), ("#response", "Key response"),
                           ("#fnswitch", "Fn switch")):
            self.query_one(rid, RadioSet).border_title = title
        self.query_one("#rgb-map", KeyboardMap).border_title = "click or drag to paint"
        self.query_one("#remap-map", KeyboardMap).border_title = "pick a key"
        self.query_one("#macro-map", KeyboardMap).border_title = "pick a key"
        self.query_one("#light-preview", KeyboardPreview).border_title = "preview"
        self.query_one("#music-preview", KeyboardPreview).border_title = "live"
        self.query_one("#remap-table", DataTable).add_columns("key", "→", "function")
        self.query_one("#macro-table", DataTable).add_columns("key", "events", "repeat")

        mode = self.state["light"]["mode"]
        mode_list = self.query_one("#mode-list", OptionList)
        try:
            mode_list.highlighted = mode_list.get_option_index(str(mode))
        except Exception:
            mode_list.highlighted = 0
        self.set_brush(self.brush)
        self.set_brush2(self.brush2)
        saved = state.colors_from_json(self.state["colors"])
        for cap in self.query_one("#rgb-map", KeyboardMap).caps():
            cap.paint(saved.get(cap.led, (0, 0, 0)))
        self.refresh_presets()
        self.refresh_remaps()
        self.refresh_macros()
        self.show_lights_state()
        self.set_music_rgb(self.music_rgb)
        self.show_music_picker()
        self.set_interval(1 / 12, self.animate_preview)
        self.connect()

    def connect(self):
        try:
            self.kb = Keyboard(self.device_path, dry_run=self.dry_run, log=self.log_packet)
        except DeviceError as e:
            self.query_one("#conn", Static).update("[red]●[/] not connected")
            self.log_msg(f"[red]{e}[/]")
            self.notify(str(e), title="keyboard not available", severity="error", timeout=8)
            return
        if self.dry_run:
            self.query_one("#conn", Static).update("[blue]●[/] dry run [dim](nothing is sent)[/]")
        else:
            kind = next((i.connection for i in find_interfaces() if i.path == self.kb.path), "")
            self.query_one("#conn", Static).update(f"[green]●[/] {kind} [dim]{self.kb.path}[/]")
        self.action_refresh_status()
        self.set_interval(5, self.action_refresh_status)

    def on_unmount(self):
        if self.visualizer:
            self.visualizer.stop()
        if self.kb:
            self.kb.close()

    def on_mouse_up(self):
        for m in self.query(KeyboardMap):
            m.end_stroke()

    # -- actions -----------------------------------------------------------

    def action_tab(self, index: int):
        self.query_one(TabbedContent).active = TABS[index]

    def action_toggle_log(self):
        self.query_one("#log").toggle_class("hidden")

    def action_help(self):
        self.push_screen(HelpScreen())

    def action_apply(self):
        handler = {"tab-light": self.apply_light, "tab-rgb": self.upload_rgb,
                   "tab-remap": self.apply_remap, "tab-macro": self.apply_macro,
                   "tab-music": self.music_start, "tab-settings": self.apply_settings}
        handler[self.query_one(TabbedContent).active]()

    def action_toggle_lights(self):
        on = self.state.get("lights_off")
        self.device_call("lights on" if on else "lights off",
                         lambda: control.toggle_lights(self.kb, self.state),
                         self.show_lights_state)

    def show_lights_state(self):
        off = self.state.get("lights_off")
        self.query_one("#lights", Static).update(
            "[dim]○ lights off[/]" if off else "[#f0a030]☀[/] lights on")

    # -- device plumbing ---------------------------------------------------

    def log_msg(self, msg: str):
        self.query_one("#log", RichLog).write(msg)

    def log_packet(self, report: bytes):
        if report[4] in range(0xF0, 0xF6):
            return  # live frames would flood the log
        try:
            self.call_from_thread(self.log_msg,
                                  f"[dim cyan]→ {report.rstrip(bytes(1)).hex(' ')}[/]")
        except RuntimeError:
            self.log_msg(f"[dim cyan]→ {report.rstrip(bytes(1)).hex(' ')}[/]")

    def device_call(self, label: str, fn, on_success=None):
        if self.kb is None:
            self.notify("no keyboard connected", severity="error")
            return
        self.run_device(label, fn, on_success)

    @work(thread=True)
    def run_device(self, label: str, fn, on_success):
        try:
            with self.lock:
                fn()
        except (DeviceError, OSError, ValueError) as e:
            self.call_from_thread(self.finish, label, e, None)
        else:
            self.call_from_thread(self.finish, label, None, on_success)

    def finish(self, label: str, error, on_success):
        if error:
            self.log_msg(f"[red]✗ {label}: {error}[/]")
            self.notify(f"{label}: {error}", severity="error")
            return
        self.log_msg(f"[green]✓[/] {label}")
        self.notify(label, title="sent" if not self.dry_run else "dry run", timeout=3)
        if on_success:
            on_success()
        if not self.dry_run:
            state.save(self.state)

    # -- status ------------------------------------------------------------

    def action_refresh_status(self):
        if self.kb:
            self.poll_status()

    @work(thread=True, exclusive=True, group="status")
    def poll_status(self):
        if self.visualizer and self.visualizer.running:
            return  # don't interleave status queries with the frame stream
        with self.lock:
            try:
                st = self.kb.status()
            except OSError:
                st = None
        self.call_from_thread(self.show_status, st)

    def show_status(self, st):
        power = self.query_one("#power", Static)
        battery = self.query_one("#battery", Static)
        if st is None:
            power.update("[yellow]no reply[/]")
            return
        icon = "⚡" if st.on_charger else "●" if st.online else "○"
        power.update(f"{icon} {st.power_name}")
        if st.battery is None or not st.online:
            battery.update("")
            return
        colour = "green" if st.battery > 50 else "yellow" if st.battery > 20 else "red"
        filled = round(st.battery / 10)
        battery.update(f"battery [{colour}]{'▰' * filled}{'▱' * (10 - filled)}[/] "
                       f"[b]{st.battery}%[/]")

    # -- lighting ----------------------------------------------------------

    def current_mode(self) -> int:
        option = self.query_one("#mode-list", OptionList).highlighted_option
        return int(option.id) if option else self.state["light"]["mode"]

    def light_values(self) -> dict:
        return {"mode": self.current_mode(),
                "brightness": self.query_one("#brightness", LevelPicker).value,
                "speed": self.query_one("#speed", LevelPicker).value,
                "direction": self.query_one("#direction", Select).value,
                "color": self.query_one("#colorindex", Select).value}

    def animate_preview(self):
        t = time.monotonic() - self.t0
        active = self.query_one(TabbedContent).active
        if active == "tab-light":
            light = self.light_values()
            custom = state.colors_from_json(self.state["colors"])
            self.query_one("#light-preview", KeyboardPreview).show(
                effects.frame(**light, t=t, custom=custom))

    @on(OptionList.OptionHighlighted, "#mode-list")
    def mode_highlighted(self):
        mode = self.current_mode()
        name, attr = keys.LIGHT_MODES.get(mode, ("?", 0))
        self.query_one("#mode-title", Label).update(f"{mode}. {name}")
        uses = {"#brightness": keys.ATTR_BRIGHTNESS, "#speed": keys.ATTR_SPEED,
                "#direction": keys.ATTR_DIRECTION, "#colorindex": keys.ATTR_COLOR}
        for selector, bit in uses.items():
            self.query_one(selector).disabled = not attr & bit
        if mode == keys.CUSTOM_MODE:
            self.query_one("#brightness").disabled = False
        hint = ("your per-key colours, edit them in the Per-key RGB tab"
                if mode == keys.CUSTOM_MODE else "preview is approximate")
        self.query_one("#mode-hint", Label).update(hint)

    @on(OptionList.OptionSelected, "#mode-list")
    def mode_selected(self):
        self.apply_light()

    @on(Button.Pressed, "#apply-light")
    def apply_light(self):
        light = self.light_values()
        name = keys.LIGHT_MODES.get(light["mode"], ("?",))[0]
        if light["mode"] == keys.CUSTOM_MODE:
            colors = state.colors_from_json(self.state["colors"])
            fn = lambda: control.upload_custom(self.kb, self.state, colors, light["brightness"])
        else:
            fn = lambda: control.apply_light(self.kb, self.state, light)
        self.device_call(f"effect: {name}", fn, self.show_lights_state)

    # -- per-key -----------------------------------------------------------

    def rgb_map(self) -> KeyboardMap:
        return self.query_one("#rgb-map", KeyboardMap)

    def set_brush(self, rgb):
        self.brush = tuple(rgb)
        brush = self.query_one("#brush", Static)
        brush.styles.background = "rgb({},{},{})".format(*rgb)
        brush.styles.color = text_color(rgb)
        brush.update(f"brush #{rgb_hex(rgb)}")

    def set_brush2(self, rgb):
        self.brush2 = tuple(rgb)
        b2 = self.query_one("#brush2", Static)
        b2.styles.background = "rgb({},{},{})".format(*rgb)
        b2.styles.color = text_color(rgb)
        b2.update(f"second #{rgb_hex(rgb)}")

    def mark_dirty(self, dirty: bool = True):
        self.query_one("#dirty", Label).update("● not uploaded yet" if dirty else "")
        if dirty and self.live_rgb:
            self.push_live_frame()

    def tool(self) -> str:
        pressed = self.query_one("#tool", RadioSet).pressed_button
        return pressed.name if pressed else "paint"

    def current_colors(self) -> state.Colors:
        return {cap.led: cap.rgb or (0, 0, 0) for cap in self.rgb_map().caps()}

    def snapshot(self):
        self.undo_stack.append(self.current_colors())
        del self.undo_stack[:-UNDO_LIMIT]
        self.redo_stack.clear()

    def restore_colors(self, colors: state.Colors):
        for cap in self.rgb_map().caps():
            cap.paint(colors.get(cap.led, (0, 0, 0)))
        self.mark_dirty()

    @on(Button.Pressed, "#undo")
    def action_undo(self):
        if self.undo_stack:
            self.redo_stack.append(self.current_colors())
            self.restore_colors(self.undo_stack.pop())

    @on(Button.Pressed, "#redo")
    def action_redo(self):
        if self.redo_stack:
            self.undo_stack.append(self.current_colors())
            self.restore_colors(self.redo_stack.pop())

    @on(Swatch.Picked)
    def swatch_picked(self, event: Swatch.Picked):
        if self.query_one(TabbedContent).active == "tab-music":
            self.set_music_rgb(event.rgb)
            self.query_one("#music-hex", Input).value = rgb_hex(event.rgb)
        else:
            self.set_brush(event.rgb)

    @on(Input.Submitted, "#hexcolor")
    @on(Input.Submitted, "#hexcolor2")
    def hex_submitted(self, event: Input.Submitted):
        try:
            rgb = parse_color(event.value)
        except Exception as e:
            self.notify(str(e), severity="error")
            return
        (self.set_brush if event.input.id == "hexcolor" else self.set_brush2)(rgb)

    @on(KeyboardMap.KeyPressed, "#rgb-map")
    def rgb_key(self, event: KeyboardMap.KeyPressed):
        tool = self.tool()
        if tool == "paint":
            if not event.drag:
                self.snapshot()
            if event.cap.rgb != self.brush:
                event.cap.paint(self.brush)
                self.mark_dirty()
        elif tool == "pick" and not event.drag:
            self.set_brush(event.cap.rgb or (0, 0, 0))
            self.query_one("#tool", RadioSet).query(RadioButton).first().value = True
        elif tool == "select":
            event.cap.toggle_class("selected")

    @on(Button.Pressed, "#sel-none")
    def select_none(self):
        for cap in self.rgb_map().caps():
            cap.remove_class("selected")

    @on(Button.Pressed, "#fill-pattern")
    def fill_pattern(self):
        self.snapshot()
        colours = patterns.generate(self.query_one("#pattern", Select).value,
                                    self.brush, self.brush2)
        caps = self.rgb_map().caps()
        targets = [c for c in caps if c.has_class("selected")] or caps
        for cap in targets:
            cap.paint(colours[cap.led])
        self.mark_dirty()

    @on(Switch.Changed, "#live-rgb")
    def live_rgb_changed(self, event: Switch.Changed):
        if self.kb is None:
            return
        self.live_rgb = event.value
        if event.value:
            colors = self.current_colors()
            self.device_call("live preview on", lambda: (self.kb.enter_live_mode(),
                                                         self.kb.send_frame(colors)))
        else:
            self.device_call("live preview off, stored lighting back",
                             lambda: control.lights_on(self.kb, self.state)
                             if not self.state.get("lights_off") else self.kb.lights_off())

    @work(thread=True, exclusive=True, group="live")
    def push_live_frame(self):
        colors = self.call_from_thread(self.current_colors)
        with self.lock:
            try:
                self.kb.send_frame(colors)
            except (DeviceError, OSError):
                pass

    @on(Button.Pressed, "#upload-rgb")
    def upload_rgb(self):
        colors = self.current_colors()
        brightness = self.query_one("#brightness", LevelPicker).value

        def saved():
            self.mark_dirty(False)
            self.show_lights_state()
            if self.live_rgb:
                self.query_one("#live-rgb", Switch).value = False
        self.device_call("per-key colours uploaded",
                         lambda: control.upload_custom(self.kb, self.state, colors, brightness),
                         saved)

    def refresh_presets(self):
        self.query_one("#preset-load", Select).set_options(
            [(name, name) for name in state.list_presets()])

    @on(Button.Pressed, "#save-preset")
    def save_preset(self):
        name = self.query_one("#preset-name", Input).value
        try:
            slug = state.save_preset(name, self.current_colors())
        except (OSError, ValueError) as e:
            self.notify(str(e), severity="error")
            return
        self.refresh_presets()
        self.notify(f"saved preset '{slug}'")

    @on(Select.Changed, "#preset-load")
    def load_preset(self, event: Select.Changed):
        if event.value is Select.NULL:
            return
        try:
            colors = state.load_preset(event.value)
        except (OSError, ValueError) as e:
            self.notify(str(e), severity="error")
            return
        self.snapshot()
        self.restore_colors(colors)
        self.notify(f"loaded '{event.value}', upload to send it")

    # -- remap -------------------------------------------------------------

    def remap_layer(self) -> int:
        return 1 if self.query_one("#remap-fn", Switch).value else 0

    def refresh_remaps(self):
        layer = self.remap_layer()
        table = self.query_one("#remap-table", DataTable)
        table.clear()
        remapped = set()
        for key, dst in sorted(self.state["remaps"].items()):
            lay, src = map(int, key.split(":"))
            table.add_row(key_label(lay, src), "→", keys.code_name(dst), key=key)
            if lay == layer:
                remapped.add(src)
        for cap in self.query_one("#remap-map", KeyboardMap).caps():
            cap.set_class(cap.code in remapped, "remapped")
            cap.set_class(cap.code == self.remap_src, "picked")

    @on(Switch.Changed, "#remap-fn")
    def remap_layer_changed(self):
        self.refresh_remaps()

    @on(KeyboardMap.KeyPressed, "#remap-map")
    def remap_key(self, event: KeyboardMap.KeyPressed):
        if event.drag:
            return
        self.remap_src = event.cap.code
        current = self.state["remaps"].get(f"{self.remap_layer()}:{self.remap_src}")
        extra = f"  [dim]now → {keys.code_name(current)}[/]" if current is not None else ""
        self.query_one("#remap-key", Label).update(
            f"[b]Key[/b]  {key_label(self.remap_layer(), self.remap_src)}{extra}")
        self.refresh_remaps()

    @on(DataTable.RowSelected, "#remap-table")
    def remap_row(self, event: DataTable.RowSelected):
        layer, src = map(int, event.row_key.value.split(":"))
        self.query_one("#remap-fn", Switch).value = bool(layer)
        self.remap_src = src
        self.refresh_remaps()

    def keys_changed(self):
        self.refresh_remaps()
        self.refresh_macros()

    @on(Button.Pressed, "#apply-remap")
    def apply_remap(self):
        dst = self.query_one("#remap-target", Select).value
        if self.remap_src is None or dst is Select.NULL:
            self.notify("pick a key on the map and a function", severity="warning")
            return
        layer, src = self.remap_layer(), self.remap_src
        self.device_call(f"{key_label(layer, src)} → {keys.code_name(dst)}",
                         lambda: control.remap(self.kb, self.state, layer, src, dst),
                         self.keys_changed)

    @on(Button.Pressed, "#restore-key")
    def restore_key(self):
        if self.remap_src is None:
            self.notify("pick a key first", severity="warning")
            return
        layer, src = self.remap_layer(), self.remap_src
        self.device_call(f"{key_label(layer, src)} restored",
                         lambda: control.restore_key(self.kb, self.state, layer, src),
                         self.keys_changed)

    @on(Button.Pressed, "#reset-keys")
    def reset_keys(self):
        def done(yes):
            if yes:
                self.device_call("all keys reset",
                                 lambda: control.reset_keys(self.kb, self.state),
                                 self.keys_changed)
        self.push_screen(Confirm("Reset every remap and macro back to default?"), done)

    # -- macro -------------------------------------------------------------

    def macro_layer(self) -> int:
        return 1 if self.query_one("#macro-fn", Switch).value else 0

    def refresh_macros(self):
        layer = self.macro_layer()
        table = self.query_one("#macro-table", DataTable)
        table.clear()
        with_macro = set()
        for key, m in sorted(self.state["macros"].items()):
            lay, src = map(int, key.split(":"))
            table.add_row(key_label(lay, src), m["events"], f"×{m['repeat']}", key=key)
            if lay == layer:
                with_macro.add(src)
        for cap in self.query_one("#macro-map", KeyboardMap).caps():
            cap.set_class(cap.code in with_macro, "has-macro")
            cap.set_class(cap.code == self.macro_src, "picked")

    @on(Switch.Changed, "#macro-fn")
    def macro_layer_changed(self):
        self.refresh_macros()

    @on(KeyboardMap.KeyPressed, "#macro-map")
    def macro_key(self, event: KeyboardMap.KeyPressed):
        if event.drag:
            return
        self.macro_src = event.cap.code
        self.query_one("#macro-key", Label).update(
            f"[b]Key[/b]  {key_label(self.macro_layer(), self.macro_src)}")
        existing = self.state["macros"].get(f"{self.macro_layer()}:{self.macro_src}")
        if existing:
            self.query_one("#macro-events", Input).value = existing["events"]
            self.query_one("#macro-repeat", Input).value = str(existing["repeat"])
        self.refresh_macros()

    def macro_events(self):
        tokens = self.query_one("#macro-events", Input).value.split()
        return control.parse_macro_events(tokens) if tokens else []

    @on(Input.Changed, "#macro-events")
    def preview_macro(self):
        preview = self.query_one("#macro-preview", Static)
        try:
            events_ = self.macro_events()
        except (KeyError, ValueError) as e:
            preview.update(Text(str(e), style="red"))
            return
        text = Text()
        for code, release, delay in events_:
            text.append(f" {'↑' if release else '↓'} {keys.code_name(code)} ",
                        style="black on #e06c75" if release else "black on #98c379")
            text.append(f" {delay}ms  ", style="dim")
        preview.update(text)

    @on(Button.Pressed, "#apply-macro")
    def apply_macro(self):
        raw = self.query_one("#macro-events", Input).value.strip()
        try:
            events_ = self.macro_events()
            repeat = int(self.query_one("#macro-repeat", Input).value or 1)
        except (KeyError, ValueError) as e:
            self.notify(str(e), severity="error")
            return
        if self.macro_src is None or not events_:
            self.notify("pick a key on the map and enter some events", severity="warning")
            return
        layer, src = self.macro_layer(), self.macro_src
        self.device_call(f"macro on {key_label(layer, src)} ({len(events_)} events)",
                         lambda: control.set_macro(self.kb, self.state, layer, src, raw, repeat),
                         self.keys_changed)

    # -- music -------------------------------------------------------------

    # -- music colour --------------------------------------------------------

    music_rgb = (255, 92, 138)

    def music_colour(self):
        """None for rainbow, else the colour picked in the music tab."""
        if self.query_one("#music-color", Select).value == "rainbow":
            return None
        return self.music_rgb

    def set_music_rgb(self, rgb):
        self.music_rgb = tuple(rgb)
        sw = self.query_one("#music-swatch", Static)
        sw.styles.background = "rgb({},{},{})".format(*rgb)
        if self.visualizer:
            self.visualizer.renderer.color = self.music_colour()

    def show_music_picker(self):
        single = self.query_one("#music-color", Select).value == "single"
        for rid in ("#music-pick-row", "#music-swatches"):
            self.query_one(rid).set_class(not single, "music-single-off")
        if self.visualizer:
            self.visualizer.renderer.color = self.music_colour()

    @on(Select.Changed, "#music-color")
    def music_color_changed(self):
        self.show_music_picker()

    @on(Input.Submitted, "#music-hex")
    @on(Input.Changed, "#music-hex")
    def music_hex(self, event):
        try:
            self.set_music_rgb(parse_color(event.value))
        except Exception:
            pass  # half-typed colour, keep the last good one

    @on(Button.Pressed, "#music-start")
    def music_start(self):
        if self.visualizer and self.visualizer.running:
            return
        from .music import AudioError, Visualizer
        color = self.music_colour()
        sensitivity = 0.4 + 0.3 * self.query_one("#music-sens-level", LevelPicker).value
        kb = None if (self.dry_run or self.kb is None) else self.kb
        try:
            self.visualizer = Visualizer(kb, self.query_one("#music-style", Select).value,
                                         color, 30, sensitivity, on_frame=self.music_frame,
                                         on_error=self.music_error)
            with self.lock:
                self.visualizer.start()
        except (AudioError, DeviceError, OSError) as e:
            self.notify(str(e), title="music sync", severity="error", timeout=8)
            self.visualizer = None
            return
        self.query_one("#music-start").disabled = True
        self.query_one("#music-stop").disabled = False
        self.notify("music sync running")

    def music_frame(self, frame, levels):
        def show():
            self.query_one("#music-preview", KeyboardPreview).show(frame)
            self.query_one("#music-bars", Static).update(Text(
                "".join(" ▁▂▃▄▅▆▇█"[min(8, int(lv * 8.99))] * 3 for lv in levels),
                style="bold magenta"))
        try:
            self.call_from_thread(show)
        except RuntimeError:
            pass

    def music_error(self, error):
        try:
            self.call_from_thread(self.notify, str(error), title="music sync",
                                  severity="error")
            self.call_from_thread(self.music_stop)
        except RuntimeError:
            pass

    @on(Button.Pressed, "#music-stop")
    def music_stop(self):
        if self.visualizer:
            self.visualizer.stop()
            self.visualizer = None
        self.query_one("#music-start").disabled = False
        self.query_one("#music-stop").disabled = True
        if self.kb is not None and not self.state.get("lights_off"):
            self.device_call("music sync stopped, lighting restored",
                             lambda: control.lights_on(self.kb, self.state))

    @on(Select.Changed, "#music-style")
    def music_style(self, event: Select.Changed):
        if self.visualizer:
            self.visualizer.renderer.style = event.value

    # -- settings ----------------------------------------------------------

    def radio_value(self, selector: str) -> int:
        pressed = self.query_one(selector, RadioSet).pressed_button
        return int(pressed.name) if pressed else 1

    @on(Button.Pressed, "#apply-settings")
    def apply_settings(self):
        sleep, response = self.radio_value("#sleep"), self.radio_value("#response")
        fn = self.radio_value("#fnswitch") == 1
        self.device_call(f"settings: sleep {SLEEP_LABELS[sleep - 1]}, gear {response}, "
                         f"fn {'on' if fn else 'off'}",
                         lambda: control.apply_settings(self.kb, self.state, sleep, response, fn))

    @on(Button.Pressed, "#factory")
    def factory(self):
        def saved():
            self.keys_changed()
            self.show_lights_state()

        def done(yes):
            if yes:
                self.device_call("factory reset",
                                 lambda: control.factory_reset(self.kb, self.state), saved)
        self.push_screen(Confirm("Restore factory settings? Everything stored on the "
                                 "keyboard gets erased."), done)


def run(device: str | None = None, dry_run: bool = False):
    AK820App(device, dry_run).run()

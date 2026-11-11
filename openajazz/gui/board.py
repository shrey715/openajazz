"""The keyboard drawing: Cairo version of the web app's board, with click and drag."""

from __future__ import annotations

import math

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Pango, PangoCairo  # noqa: E402

from .. import keys  # noqa: E402

KEYS = [(code, legend, led) for code, legend, led, _, _ in keys.PHYSICAL_KEYS]
ARROWS = {"→": 0.0, "↓": math.pi / 2, "←": math.pi, "↑": -math.pi / 2}
MARK_COLORS = {"picked": (0.20, 0.83, 0.60), "selected": (0.96, 0.73, 0.26),
               "remapped": (0.65, 0.55, 0.98), "macro": (0.22, 0.74, 0.97)}

_xs = [v for x, _, w, _ in keys.KEY_RECTS.values() for v in (x, x + w)]
_ys = [v for _, y, _, h in keys.KEY_RECTS.values() for v in (y, y + h)]
PAD = 14
VIEW = (min(_xs) - PAD, min(_ys) - PAD, max(_xs) - min(_xs) + 2 * PAD, max(_ys) - min(_ys) + 2 * PAD)


def _rounded(cr, x, y, w, h, r):
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


class KeyboardView(Gtk.DrawingArea):
    """Draws a frame of LED colours; optionally reports clicked/dragged keys."""

    def __init__(self, labels: bool = True, glow: bool = True, interactive: bool = False):
        super().__init__()
        self.colors: dict[int, tuple[int, int, int]] = {}
        self.marks: dict[int, str] = {}
        self.label_alpha = 0.85 if labels else 0.0
        self.glow = glow
        self.on_key = None  # callback(code, led, drag)
        self._last = None
        self._origin = (0.0, 0.0)
        self.set_draw_func(self._draw)
        self.set_hexpand(True)
        if interactive:
            drag = Gtk.GestureDrag()
            drag.connect("drag-begin", self._begin)
            drag.connect("drag-update", self._update)
            self.add_controller(drag)
            self.set_cursor_from_name("crosshair")

    # -- input ------------------------------------------------------------

    def _geometry(self, width, height):
        vx, vy, vw, vh = VIEW
        s = min(width / vw, height / vh)
        return s, (width - vw * s) / 2 - vx * s, (height - vh * s) / 2 - vy * s

    def key_at(self, x, y):
        s, ox, oy = self._geometry(self.get_width(), self.get_height())
        px, py = (x - ox) / s, (y - oy) / s
        for code, _, led in KEYS:
            kx, ky, w, h = keys.KEY_RECTS[led]
            if kx <= px <= kx + w and ky <= py <= ky + h:
                return code, led
        return None

    def _begin(self, gesture, x, y):
        self._origin = (x, y)
        hit = self.key_at(x, y)
        self._last = hit
        if hit and self.on_key:
            self.on_key(*hit, False)

    def _update(self, gesture, dx, dy):
        hit = self.key_at(self._origin[0] + dx, self._origin[1] + dy)
        if hit and hit != self._last and self.on_key:
            self._last = hit
            self.on_key(*hit, True)

    def show(self, colors, marks=None):
        self.colors = colors
        if marks is not None:
            self.marks = marks
        self.queue_draw()

    # -- drawing ----------------------------------------------------------

    def _draw(self, area, cr, width, height):
        s, ox, oy = self._geometry(width, height)
        vx, vy, vw, vh = VIEW
        cr.translate(ox, oy)
        cr.scale(s, s)

        # case
        _rounded(cr, vx + 2, vy + 2, vw - 4, vh - 4, 14)
        cr.set_source_rgb(0.08, 0.085, 0.10)
        cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.07)
        cr.set_line_width(1.2)
        cr.stroke()

        lit = []
        for code, legend, led in KEYS:
            rgb = self.colors.get(led, (0, 0, 0))
            lit.append((legend, led, rgb, max(rgb) / 255))

        if self.glow:  # soft under-glow: a few widening translucent layers
            for legend, led, rgb, v in lit:
                if v < 0.04:
                    continue
                x, y, w, h = keys.KEY_RECTS[led]
                r, g, b = (c / 255 for c in rgb)
                for grow, alpha in ((7, 0.10), (4, 0.18), (1.5, 0.30)):
                    _rounded(cr, x - grow, y - grow, w - 3 + 2 * grow, h - 3 + 2 * grow, 6 + grow)
                    cr.set_source_rgba(r, g, b, alpha * v)
                    cr.fill()

        layout = PangoCairo.create_layout(cr)
        for legend, led, rgb, v in lit:
            x, y, w, h = keys.KEY_RECTS[led]
            pw, ph = w - 3, h - 3
            _rounded(cr, x, y, pw, ph, 5)
            cr.set_source_rgb(0.08, 0.087, 0.11)
            cr.fill()
            tx, ty, tw, th = x + 2.6, y + 1.8, pw - 5.2, ph - 6
            _rounded(cr, tx, ty, tw, th, 4)
            cr.set_source_rgb(0.17, 0.18, 0.22)
            cr.fill_preserve()
            if v > 0.02:
                cr.set_source_rgba(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255, 0.18 + 0.5 * v)
                cr.fill()
            else:
                cr.new_path()
            mark = self.marks.get(led)
            if mark:
                _rounded(cr, x - 1.2, y - 1.2, pw + 2.4, ph + 2.4, 6)
                cr.set_source_rgb(*MARK_COLORS[mark])
                cr.set_line_width(2.2)
                cr.stroke()
            if self.label_alpha:
                lum = (rgb[0] * 0.299 + rgb[1] * 0.587 + rgb[2] * 0.114) / 255
                dark = v > 0.02 and lum > 0.55
                cr.set_source_rgba(*((0.05, 0.05, 0.07) if dark else (0.94, 0.95, 0.97)),
                                   self.label_alpha)
                cx, cy = tx + tw / 2, ty + th / 2
                if legend in ARROWS:
                    self._arrow(cr, cx, cy, 4.2, ARROWS[legend])
                else:
                    size = 7.6 if len(legend) > 3 else 9.4
                    layout.set_font_description(Pango.FontDescription(f"Inter, Sans Semi-Bold {size}px"))
                    layout.set_text(legend, -1)
                    lw, lh = layout.get_pixel_size()
                    cr.move_to(cx - lw / 2, cy - lh / 2)
                    PangoCairo.show_layout(cr, layout)

    @staticmethod
    def _arrow(cr, cx, cy, r, angle):
        cr.save()
        cr.translate(cx, cy)
        cr.rotate(angle)
        cr.set_line_width(r * 0.32)
        cr.set_line_cap(1)  # round
        cr.move_to(-r, 0)
        cr.line_to(r, 0)
        cr.move_to(r * 0.35, -r * 0.62)
        cr.line_to(r, 0)
        cr.line_to(r * 0.35, r * 0.62)
        cr.stroke()
        cr.restore()

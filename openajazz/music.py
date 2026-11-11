"""Audio-reactive lighting through the live-frame path (like the vendor's music mode).

Audio comes from the monitor of the default output (whatever is playing), via
`parec` or `pw-record`.  Needs numpy: install with the music extra,
e.g. `uv tool install 'openajazz[music]'`.
The renderer styles follow prasoondev/ajazz-linux (MIT).
"""

from __future__ import annotations

import colorsys
import math
import os
import shutil
import subprocess
import threading
import time

from . import keys
from .effects import POS

RATE = 48000
FFT_SIZE = 2048
BANDS = 16
STYLES = ("spectrum", "pulse", "wave")


NEEDS_NUMPY = (
    "music sync needs numpy. Install openajazz with the music extra: "
    "uv tool install --upgrade 'openajazz[music]'  (or: pipx install --force "
    "'openajazz[music]', or pip install 'openajazz[music]'). The AppImage already has it."
)


class AudioError(RuntimeError):
    pass


class AudioSource:
    """Mono s16le capture of what's playing ("monitor") or the microphone ("mic")."""

    def __init__(self, source: str = "monitor"):
        mic = source == "mic"
        if shutil.which("parec"):
            device = "@DEFAULT_SOURCE@" if mic else "@DEFAULT_MONITOR@"
            cmd = ["parec", f"--device={device}", "--format=s16le", f"--rate={RATE}",
                   "--channels=1", "--latency-msec=15", "--raw"]
        elif shutil.which("pw-record"):
            props = [] if mic else ["-P", "{ stream.capture.sink=true }"]
            cmd = ["pw-record", *props, "--format=s16", f"--rate={RATE}", "--channels=1", "-"]
        else:
            raise AudioError("no audio capture tool found (install pipewire-pulse or pulseaudio-utils)")
        # own session: Ctrl-C in the terminal should stop us, not kill the recorder
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     start_new_session=True)
        self.fd = self.proc.stdout.fileno()
        os.set_blocking(self.fd, False)
        self.buf = bytearray()

    def latest(self, nbytes: int) -> bytes:
        while True:
            try:
                chunk = os.read(self.fd, 65536)
            except BlockingIOError:
                break
            if not chunk:
                err = self.proc.stderr.read().decode(errors="replace").strip()
                raise AudioError(f"audio capture stopped: {err or 'recorder exited'}")
            self.buf += chunk
        del self.buf[:max(0, len(self.buf) - nbytes)]
        return bytes(self.buf)

    def close(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(1)
            except subprocess.TimeoutExpired:
                self.proc.kill()


class Analyzer:
    """FFT -> BANDS levels 0..1 with auto-gain and peak-hold falloff."""

    def __init__(self, sensitivity: float = 1.0):
        try:
            import numpy as np
        except ImportError as e:
            raise AudioError(NEEDS_NUMPY) from e
        self.np = np
        self.window = np.hanning(FFT_SIZE).astype(np.float32)
        freqs = np.fft.rfftfreq(FFT_SIZE, 1 / RATE)
        edges = np.geomspace(40.0, 16000.0, BANDS + 1)
        self.bins = []
        for lo, hi in zip(edges[:-1], edges[1:]):
            a = int(np.searchsorted(freqs, lo))
            self.bins.append((a, max(int(np.searchsorted(freqs, hi)), a + 1)))
        self.tilt = (np.sqrt(edges[:-1] * edges[1:]) / 1000.0) ** 0.5
        self.levels = np.zeros(BANDS, dtype=np.float32)
        self.peak_db = -30.0
        self.sensitivity = sensitivity
        self.bass = 0.0

    def update(self, pcm: bytes):
        np = self.np
        x = np.frombuffer(pcm[:len(pcm) // 2 * 2], dtype="<i2").astype(np.float32) / 32768
        if len(x) < FFT_SIZE:
            x = np.pad(x, (FFT_SIZE - len(x), 0))
        spec = np.abs(np.fft.rfft(x[-FFT_SIZE:] * self.window)) / (FFT_SIZE / 4)
        db = 20 * np.log10(np.array([spec[a:b].max() for a, b in self.bins]) * self.tilt + 1e-9)
        top = float(db.max())
        self.peak_db = max(top, self.peak_db - 0.15, -60.0)
        span = 40.0 / self.sensitivity
        lv = np.clip((db - (self.peak_db - span)) / span, 0.0, 1.0)
        if top < -70:
            lv[:] = 0
        self.levels = np.maximum(lv, self.levels * 0.82)
        self.bass = max(float(self.levels[:3].max()), self.bass * 0.85)


class Renderer:
    def __init__(self, style: str = "spectrum", color: tuple[int, int, int] | None = None):
        if style not in STYLES:
            raise ValueError(f"style must be one of {', '.join(STYLES)}")
        self.style, self.color = style, color
        self.history = [0.0] * 32
        self.t0 = time.monotonic()

    def _tint(self, hue: float, value: float):
        value = max(0.0, min(1.0, value))
        if self.color is None:
            r, g, b = colorsys.hsv_to_rgb(hue % 1.0, 1.0, value)
            return int(r * 255), int(g * 255), int(b * 255)
        return tuple(int(c * value) for c in self.color)

    def frame(self, levels, bass: float) -> dict[int, tuple[int, int, int]]:
        t = time.monotonic() - self.t0
        out = {}
        if self.style == "wave":
            self.history = [bass] + self.history[:-1]
        for led, (u, v, _) in POS.items():
            if self.style == "spectrum":
                height = float(levels[min(BANDS - 1, int(u * BANDS))])
                fill = 1.0 - v
                on = max(0.0, min(1.0, (height * 1.15 - fill) * 6 + 1))
                out[led] = self._tint(0.33 * (1 - fill), 0.04 + 0.96 * on) if on else (0, 0, 0)
            elif self.style == "pulse":
                out[led] = self._tint(t * 0.05 + u * 0.3, 0.08 + 0.92 * bass ** 1.5)
            else:
                r = math.hypot(u - 0.5, (v - 0.5) * 0.45)
                e = self.history[min(len(self.history) - 1, int(r * 2 * len(self.history)))]
                out[led] = self._tint(t * 0.04 + r, 0.05 + 0.95 * e ** 1.3)
        return out


class Visualizer:
    """capture -> FFT -> frames on a background thread."""

    def __init__(self, kb, style="spectrum", color=None, fps=30, sensitivity=1.0,
                 on_frame=None, on_error=None, source="monitor"):
        self.kb, self.fps, self.source = kb, fps, source
        self.renderer = Renderer(style, color)
        self.analyzer = Analyzer(sensitivity)
        self.on_frame = on_frame or (lambda frame, levels: None)
        self.on_error = on_error or (lambda e: None)
        self._stop = threading.Event()
        self._thread = None
        self.audio = None

    def start(self):
        self.audio = AudioSource(self.source)
        if self.kb is not None:
            self.kb.enter_live_mode(keys.MUSIC_MODE)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        period, nxt = 1.0 / self.fps, time.monotonic()
        try:
            while not self._stop.is_set():
                self.analyzer.update(self.audio.latest(FFT_SIZE * 2))
                frame = self.renderer.frame(self.analyzer.levels, self.analyzer.bass)
                if self.kb is not None:
                    self.kb.send_frame(frame)
                self.on_frame(frame, list(self.analyzer.levels))
                nxt += period
                delay = nxt - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                else:
                    nxt = time.monotonic()
        except Exception as e:
            self.on_error(e)
        finally:
            self.audio.close()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(2)

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

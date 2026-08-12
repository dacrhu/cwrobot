"""Scrolling mini-waterfall: the RX pitch-tuning aid.

Per Specifikacio.md: the radio's CW sidetone pitch is normally fixed and
pre-configured in the app; the *operator* retunes the radio's own VFO until
the received tone lines up with a marker drawn at that configured pitch.
This widget is that visual aid.

Orientation: frequency runs horizontally (low on the left, high on the
right), time runs vertically and scrolls top-to-bottom (the newest slice
enters at the top and old ones scroll down and off the bottom) -- this
reads naturally left-to-right like the frequency axis on the radio itself,
with history flowing downward like a conventional waterfall.

Rendering approach: a persistent RGBA8888 numpy buffer (time_rows x
freq_bins) is scrolled one row at a time as new spectrum slices arrive
(from the decoder thread, via a Qt signal -- so `push_spectrum_slice` always
runs on the UI thread) and painted via QImage/QPainter. A QTimer decouples
the actual repaint rate from the (independent, audio-driven) row-push rate.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QSize, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from cwrobot.dsp.spectrum import SpectrumSlice

_REPAINT_INTERVAL_MS = 50  # ~20 fps

# A simple dark -> warm-bright sequential colormap (deliberately not a
# rainbow/jet palette, which reads noise as false structure). The last stop
# is a saturated red reserved for the very top of the range: combined with
# CONTRAST_GAMMA and the percentile-based floor/ceil below, only genuinely
# strong, narrow peaks (a real CW tone) reach it, while broadband noise --
# even at moderately high levels -- stays in the blue/cyan/yellow range. This
# is what makes the actual signal pop out as red instead of reading the same
# yellow as the noise floor.
_COLORMAP_STOPS = np.array(
    [
        [8, 8, 20],
        [30, 60, 120],
        [80, 170, 195],
        [255, 220, 120],
        [255, 140, 40],
        [220, 30, 30],
    ],
    dtype=np.float32,
)

# Where each stop above sits along the normalized 0..1 range. Deliberately
# *not* evenly spaced: the dark/blue/cyan stops are stretched across most of
# the range (that's where ordinary noise lives) while yellow/orange/red are
# compressed into the top of it, so reaching red takes a visibly stronger
# signal than reaching yellow. Shift these further right/left to make red
# harder/easier to reach relative to yellow.
_COLORMAP_POSITIONS = np.array([0.0, 0.35, 0.6, 0.8, 0.995, 1.0], dtype=np.float32)


def _build_colormap(n: int = 256) -> np.ndarray:
    xs = np.arange(n, dtype=np.float32)
    stops_x = _COLORMAP_POSITIONS * (n - 1)
    lut = np.zeros((n, 3), dtype=np.uint8)
    for channel in range(3):
        lut[:, channel] = np.interp(xs, stops_x, _COLORMAP_STOPS[:, channel]).astype(np.uint8)
    return lut


_COLORMAP = _build_colormap()

DEFAULT_HALF_SPAN_HZ = 1000  # wide enough to actually see how far off-tune you are

# Auto-leveling step limits, in dB per pushed row (~50 ms/row at the default
# repaint rate). Floor/ceil can always jump instantly toward *more contrast*
# (a quieter floor, a louder ceiling) but only relax slowly the other way --
# this is what lets the display recover after a loud transient (a static
# crash, a brief burst of QRM) instead of being permanently desensitized by
# it for the rest of the session.
FLOOR_MAX_RISE_DB_PER_ROW = 0.08
CEIL_MAX_FALL_DB_PER_ROW = 0.08

# Per-row floor/ceil are taken from these percentiles rather than the literal
# min/max: a single stray bin (a sharp noise spike, or a birdie) shouldn't by
# itself redefine "quietest" or "loudest" for the whole row.
FLOOR_PERCENTILE = 8.0
CEIL_PERCENTILE = 97.0

# Contrast gamma: applied to the normalized (0..1) value before colormapping.
# >1 pushes mid-level bins (broad, moderate-strength noise/QRM) down toward
# the dark end while leaving strong, narrow peaks (a real CW tone) close to
# 1.0 -- i.e. it makes the actual signal visually pop out from a "greyish"
# broadband background instead of both reading as similarly bright.
CONTRAST_GAMMA = 2.4

# How much to dim a row pushed while squelch is closed, so the display
# visually distinguishes "the decoder is treating this as real signal" from
# "this is being rejected as noise" -- directly useful while tuning the
# squelch-sensitivity slider.
SQUELCH_CLOSED_DIM_FACTOR = 0.35


class WaterfallWidget(QWidget):
    def __init__(self, freq_bins: int = 300, time_rows: int = 150, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._freq_bins = freq_bins
        self._time_rows = time_rows
        self._rgba_buffer = np.zeros((time_rows, freq_bins, 4), dtype=np.uint8)
        self._rgba_buffer[:, :, 3] = 255  # fully opaque

        self._center_hz = 600
        self._half_span_hz = DEFAULT_HALF_SPAN_HZ
        self._signal_width_hz: float | None = None
        self._squelch_open = True

        # Auto-leveling (simple AGC-like floor/ceiling tracking) so the
        # display stays readable across very different input signal levels.
        self._floor_db: float | None = None
        self._ceil_db: float | None = None

        # 1/4 shorter than the original 160/260/220 (min/max/sizeHint) --
        # it's a tuning aid, not RxPanel's primary content, so it shouldn't
        # claim more vertical space than it needs (at decoded_text's
        # expense) just because the window got taller.
        self.setMinimumHeight(120)
        self.setMaximumHeight(195)
        # Expanding horizontally + a real sizeHint: without these, Qt's
        # box-layout math falls back to QWidget's tiny default sizeHint for
        # this widget, which -- combined with several sibling rows in
        # RxPanel's layout -- is what let the control rows below get
        # squeezed up against (and at small window sizes, visually crowd)
        # the waterfall instead of shrinking themselves first.
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        self._repaint_timer = QTimer(self)
        self._repaint_timer.timeout.connect(self.update)
        self._repaint_timer.start(_REPAINT_INTERVAL_MS)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        return QSize(500, 165)

    def set_target_pitch(self, hz: int) -> None:
        self._center_hz = hz

    def set_half_span(self, hz: int) -> None:
        self._half_span_hz = hz

    def set_signal_width(self, hz: float) -> None:
        """Width of the band the decoder actually monitors around the target
        pitch, drawn as two guide lines flanking the center marker so it's
        clear how far off-tune is still "close enough"."""
        self._signal_width_hz = hz

    def set_squelch_open(self, is_open: bool) -> None:
        """Reflects the squelch gate in the display itself: rows pushed
        while squelch is closed are dimmed, so it's visible (and tunable,
        alongside the squelch-sensitivity control) which parts of what's on
        screen the decoder is actually treating as a real signal versus
        rejecting as noise."""
        self._squelch_open = is_open

    def _display_range(self) -> tuple[float, float]:
        # Clamp the low end at 0 Hz rather than going negative for low
        # pitches; the marker just won't be exactly centered in that case.
        low = max(0.0, self._center_hz - self._half_span_hz)
        high = self._center_hz + self._half_span_hz
        return low, high

    def push_spectrum_slice(self, slice_: SpectrumSlice) -> None:
        """Add one new time row at the top of the waterfall (older rows
        scroll down). Must be called from the UI thread (connect this as a
        slot to a cross-thread Qt signal, never call it directly from the
        audio/decoder thread)."""
        if len(slice_.freqs_hz) < 2:
            return

        # Resample onto freq_bins columns, low frequency on the left.
        col_freqs = np.linspace(slice_.freqs_hz[0], slice_.freqs_hz[-1], self._freq_bins)
        row_db = np.interp(col_freqs, slice_.freqs_hz, slice_.power_db)

        cur_min, cur_max = (float(v) for v in np.percentile(row_db, [FLOOR_PERCENTILE, CEIL_PERCENTILE]))
        if self._floor_db is None:
            self._floor_db, self._ceil_db = cur_min, cur_max
        else:
            # Floor can always drop instantly (a quieter moment only helps
            # contrast) but rises at most a small step per row; ceil can
            # always jump up instantly (a real signal appearing) but decays
            # at most a small step per row. Without that decay/rise cap in
            # the "less sensitive" direction, a single loud transient would
            # permanently desensitize the whole session's color scale --
            # note the old version had exactly that bug (ceil could only
            # ever increase, floor could only ever decrease).
            self._floor_db = min(cur_min, self._floor_db + FLOOR_MAX_RISE_DB_PER_ROW)
            self._ceil_db = max(cur_max, self._ceil_db - CEIL_MAX_FALL_DB_PER_ROW)

        span = max(self._ceil_db - self._floor_db, 1e-6)
        normalized = np.clip((row_db - self._floor_db) / span, 0.0, 1.0)
        normalized = normalized**CONTRAST_GAMMA
        idx = (normalized * (len(_COLORMAP) - 1)).astype(np.uint8)
        rgb = _COLORMAP[idx]
        if not self._squelch_open:
            rgb = (rgb.astype(np.float32) * SQUELCH_CLOSED_DIM_FACTOR).astype(np.uint8)

        self._rgba_buffer = np.roll(self._rgba_buffer, 1, axis=0)
        self._rgba_buffer[0, :, 0:3] = rgb

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        rect = self.rect()

        buf = self._rgba_buffer
        h, w, _ = buf.shape
        image = QImage(buf.tobytes(), w, h, w * 4, QImage.Format.Format_RGBA8888)
        painter.drawImage(rect, image)

        low, high = self._display_range()
        span = max(high - low, 1)

        # Light gridlines with frequency labels; step adapts so a wide span
        # doesn't turn into unreadable clutter.
        step = 200 if span > 1200 else 100
        painter.setPen(QPen(QColor(255, 255, 255, 60), 1))
        freq = (low // step + 1) * step
        while freq <= high:
            x = rect.width() * (freq - low) / span
            painter.drawLine(int(x), 0, int(x), rect.height())
            painter.drawText(int(x) + 3, rect.height() - 4, f"{freq:.0f}")
            freq += step

        # Bandwidth guides: the band the decoder actually monitors, flanking
        # the center marker -- as long as the signal stays between these two
        # lines, tuning is "close enough" even if not perfectly centered.
        # Vivid, fully-opaque magenta: a hue that doesn't occur anywhere in
        # the colormap above (which runs dark -> blue -> cyan -> warm
        # yellow), so the guides stay unambiguous against any signal level.
        if self._signal_width_hz:
            half_width = self._signal_width_hz / 2.0
            painter.setPen(QPen(QColor(255, 0, 220), 2))
            for edge_hz in (self._center_hz - half_width, self._center_hz + half_width):
                x = rect.width() * (edge_hz - low) / span
                painter.drawLine(int(x), 0, int(x), rect.height())

        # Target-pitch marker: tune the radio's VFO until the signal lines up
        # here. Bright red-white, thicker than the bandwidth guides so the
        # exact target frequency always reads as the most prominent line.
        marker_x = rect.width() * (self._center_hz - low) / span
        painter.setPen(QPen(QColor(255, 40, 40), 3))
        painter.drawLine(int(marker_x), 0, int(marker_x), rect.height())

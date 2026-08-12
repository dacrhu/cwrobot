"""Small rFFT slice computation feeding the waterfall display.

This is a separate, coarser/heavier computation from the Goertzel-based tone
detector (dsp/tone_detector.py) used for actual decoding -- it only needs to
drive a ~15-20 fps UI, so a plain rFFT window is cheap enough and gives a
nicer visual than a handful of Goertzel bins would.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class SpectrumSlice:
    """One column of the waterfall: power values across a frequency band."""

    freqs_hz: np.ndarray  # bin center frequencies, ascending
    power_db: np.ndarray  # same shape as freqs_hz


class SpectrumAnalyzer:
    """Computes a windowed rFFT and slices out a band around a target frequency."""

    def __init__(self, sample_rate: int, fft_size: int = 512) -> None:
        self.sample_rate = sample_rate
        self.fft_size = fft_size
        self._window = np.hanning(fft_size).astype(np.float32)

    def analyze(self, samples: np.ndarray, center_hz: float, half_span_hz: float) -> SpectrumSlice:
        """`samples` must have at least `fft_size` elements; only the most
        recent `fft_size` are used.
        """
        if len(samples) < self.fft_size:
            padded = np.zeros(self.fft_size, dtype=np.float32)
            padded[-len(samples) :] = samples
            windowed = padded * self._window
        else:
            windowed = samples[-self.fft_size :] * self._window

        spectrum = np.fft.rfft(windowed)
        power = np.abs(spectrum) ** 2
        freqs = np.fft.rfftfreq(self.fft_size, d=1.0 / self.sample_rate)

        low, high = center_hz - half_span_hz, center_hz + half_span_hz
        mask = (freqs >= low) & (freqs <= high)

        power_db = 10 * np.log10(np.maximum(power[mask], 1e-12))
        return SpectrumSlice(freqs_hz=freqs[mask], power_db=power_db)

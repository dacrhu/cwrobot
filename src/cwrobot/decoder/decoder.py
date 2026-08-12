"""CwDecoder: the single consumer of the audio RingBuffer, run on a QThread.

The ring buffer is single-producer/single-consumer, so exactly one
component may read from it. CwDecoder is that one reader, and it fans out
audio to every downstream consumer (waterfall spectrum + the actual
dot/dash/character decoding) so the ring buffer only ever needs one reader.

The processing pipeline itself lives in `CwDecoderCore`, a plain (Qt-free)
class, so it's directly unit-testable with synthetic audio (see
tests/test_decoder_synthetic.py) without needing a running Qt event loop or
a real audio device. `CwDecoder(QObject)` is a thin QThread-worker shell
around it: it owns the blocking read loop and turns `CwDecoderCore`'s return
values into Qt signals, which PySide6 auto-queues across the thread
boundary to the UI thread.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QObject, Signal

from cwrobot.audio.ringbuffer import RingBuffer
from cwrobot.decoder.timing import AdaptiveMorseDecoder
from cwrobot.dsp.spectrum import SpectrumAnalyzer, SpectrumSlice
from cwrobot.dsp.tone_detector import PROCESSING_SAMPLE_RATE, GoertzelToneDetector, StreamingDecimator

SPECTRUM_HOP_SECONDS = 0.05  # ~20 waterfall columns/sec
WPM_EMIT_INTERVAL_SECONDS = 0.25  # ~4x/sec, per the plan's UI-refresh cadence


@dataclass
class ProcessedBlock:
    decoded_text: str
    spectrum_slice: SpectrumSlice | None
    wpm: float | None
    squelch_open: bool


class CwDecoderCore:
    """Pure processing pipeline: raw audio samples in, decoded text /
    spectrum slices / WPM estimate out. No Qt, no threads, no I/O -- safe to
    drive directly from tests with synthetic audio."""

    def __init__(
        self,
        sample_rate: int,
        target_pitch_hz: int = 600,
        waterfall_half_span_hz: int = 1000,
    ) -> None:
        self.sample_rate = sample_rate
        self.waterfall_half_span_hz = waterfall_half_span_hz

        self._decimator = StreamingDecimator(input_rate=sample_rate)
        self._tone_detector = GoertzelToneDetector(target_freq_hz=target_pitch_hz)
        self._morse_decoder = AdaptiveMorseDecoder()

        # The waterfall is fed from the same decimated (8 kHz) stream as the
        # tone detector, not the raw high-rate audio: an FFT of a given size
        # has far finer Hz-per-bin resolution at a lower sample rate, so a
        # genuinely narrow, already-filtered radio CW signal actually shows
        # up narrow instead of smeared across several hundred Hz by the
        # window's own frequency resolution.
        self._spectrum = SpectrumAnalyzer(sample_rate=PROCESSING_SAMPLE_RATE)
        self._spectrum_window = np.zeros(self._spectrum.fft_size, dtype=np.float32)
        self._spectrum_hop_samples = int(PROCESSING_SAMPLE_RATE * SPECTRUM_HOP_SECONDS)
        self._spectrum_accumulated = 0

        self._wpm_hop_accumulator_ms = 0.0
        self._auto_bandwidth = True

    @property
    def target_pitch_hz(self) -> float:
        return self._tone_detector.target_freq_hz

    @target_pitch_hz.setter
    def target_pitch_hz(self, hz: float) -> None:
        self._tone_detector.target_freq_hz = hz

    @property
    def current_wpm(self) -> float:
        return self._morse_decoder.current_wpm

    @property
    def effective_bandwidth_hz(self) -> float:
        return self._tone_detector.effective_bandwidth_hz

    @property
    def detection_bin_span(self) -> int:
        """Number of Goertzel bins (each ~sample_rate/window_samples wide) on
        each side of the target pitch that are summed for detection -- the
        knob behind `effective_bandwidth_hz`. User-adjustable (e.g. a UI
        slider) to trade VFO-mistuning tolerance against selectivity."""
        return self._tone_detector.bin_span

    @detection_bin_span.setter
    def detection_bin_span(self, bins: int) -> None:
        self._tone_detector.bin_span = max(0, bins)

    @property
    def squelch_ratio(self) -> float:
        return self._tone_detector.squelch_ratio

    @squelch_ratio.setter
    def squelch_ratio(self, ratio: float) -> None:
        # 0 is a legitimate, deliberate setting -- see GoertzelToneDetector's
        # squelch docstring: a ratio of 0 makes the strict test's threshold
        # (squelch_mean * ratio) collapse to 0, which envelope (always >= 0)
        # essentially never falls below, so _is_squelched() always returns
        # False and squelch is effectively fully open ("squelch off").
        # Only genuinely negative input is clamped away.
        self._tone_detector.squelch_ratio = max(0.0, ratio)

    @property
    def squelch_open(self) -> bool:
        return self._tone_detector.squelch_currently_open

    @property
    def auto_bandwidth(self) -> bool:
        """Whether the Goertzel analysis window auto-tracks the detected
        keying speed (see dsp.tone_detector's ADAPTIVE_WINDOW_K comment)
        instead of staying at the fixed manual bin_span/window. Toggling
        this off snaps the window back to WINDOW_SAMPLES via
        request_manual_window(), through the same safe/deferred resize path
        used for auto-retuning -- never mid-mark."""
        return self._auto_bandwidth

    @auto_bandwidth.setter
    def auto_bandwidth(self, enabled: bool) -> None:
        self._auto_bandwidth = enabled
        if not enabled:
            self._tone_detector.request_manual_window()

    def reset_decoder(self) -> None:
        self._morse_decoder.reset()

    def process_block(self, samples: np.ndarray) -> ProcessedBlock:
        decimated = self._decimator.process(samples)

        decoded_text = self._process_decoding(decimated)
        spectrum_slice = self._process_spectrum(decimated)

        wpm: float | None = None
        self._wpm_hop_accumulator_ms += 1000.0 * len(samples) / self.sample_rate
        if self._wpm_hop_accumulator_ms >= WPM_EMIT_INTERVAL_SECONDS * 1000.0:
            self._wpm_hop_accumulator_ms = 0.0
            wpm = self.current_wpm
            if self._auto_bandwidth and self._morse_decoder.is_calibrated:
                self._tone_detector.request_retune(self._morse_decoder.dot_unit_ms)

        return ProcessedBlock(
            decoded_text=decoded_text,
            spectrum_slice=spectrum_slice,
            wpm=wpm,
            squelch_open=self.squelch_open,
        )

    def _process_decoding(self, decimated: np.ndarray) -> str:
        if len(decimated) == 0:
            return ""
        output = ""
        for hop in self._tone_detector.process(decimated):
            output += self._morse_decoder.feed_hop(hop.is_on, hop.duration_ms)
        return output

    def _process_spectrum(self, decimated: np.ndarray) -> SpectrumSlice | None:
        if len(decimated) == 0:
            return None
        self._update_spectrum_window(decimated)
        self._spectrum_accumulated += len(decimated)
        if self._spectrum_accumulated < self._spectrum_hop_samples:
            return None
        self._spectrum_accumulated = 0
        return self._spectrum.analyze(
            self._spectrum_window,
            center_hz=self.target_pitch_hz,
            half_span_hz=self.waterfall_half_span_hz,
        )

    def _update_spectrum_window(self, samples: np.ndarray) -> None:
        n = len(samples)
        window_len = len(self._spectrum_window)
        if n >= window_len:
            self._spectrum_window[:] = samples[-window_len:]
        else:
            self._spectrum_window[: window_len - n] = self._spectrum_window[n:]
            self._spectrum_window[window_len - n :] = samples


class CwDecoder(QObject):
    spectrum_column = Signal(object)  # dsp.spectrum.SpectrumSlice
    character_decoded = Signal(str)
    wpm_changed = Signal(float)
    squelch_state_changed = Signal(bool)

    def __init__(
        self,
        ring_buffer: RingBuffer,
        sample_rate: int,
        target_pitch_hz: int = 600,
        waterfall_half_span_hz: int = 1000,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._ring_buffer = ring_buffer
        self._core = CwDecoderCore(
            sample_rate=sample_rate,
            target_pitch_hz=target_pitch_hz,
            waterfall_half_span_hz=waterfall_half_span_hz,
        )
        self._running = False
        self._last_squelch_open: bool | None = None

    @property
    def target_pitch_hz(self) -> float:
        return self._core.target_pitch_hz

    @target_pitch_hz.setter
    def target_pitch_hz(self, hz: float) -> None:
        self._core.target_pitch_hz = hz

    @property
    def effective_bandwidth_hz(self) -> float:
        return self._core.effective_bandwidth_hz

    @property
    def detection_bin_span(self) -> int:
        return self._core.detection_bin_span

    @detection_bin_span.setter
    def detection_bin_span(self, bins: int) -> None:
        self._core.detection_bin_span = bins

    @property
    def squelch_ratio(self) -> float:
        return self._core.squelch_ratio

    @squelch_ratio.setter
    def squelch_ratio(self, ratio: float) -> None:
        self._core.squelch_ratio = ratio

    @property
    def auto_bandwidth(self) -> bool:
        return self._core.auto_bandwidth

    @auto_bandwidth.setter
    def auto_bandwidth(self, enabled: bool) -> None:
        self._core.auto_bandwidth = enabled

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        """Blocking loop; intended to run on a dedicated QThread (connect
        `QThread.started` to this method after `moveToThread`)."""
        self._running = True
        while self._running:
            if not self._ring_buffer.wait_for_data(timeout=0.1):
                continue
            samples = self._ring_buffer.read_available()
            if len(samples) == 0:
                continue
            try:
                result = self._core.process_block(samples)
            except Exception:
                # A single bad block must not silently kill the whole
                # decode thread (which would then look like "the app just
                # stopped decoding" with no obvious cause) -- log and keep
                # the audio stream flowing.
                import logging

                logging.getLogger(__name__).exception("CwDecoderCore.process_block failed")
                continue
            if result.decoded_text:
                self.character_decoded.emit(result.decoded_text)
            if result.spectrum_slice is not None:
                self.spectrum_column.emit(result.spectrum_slice)
            if result.wpm is not None:
                self.wpm_changed.emit(result.wpm)
            squelch_open = bool(result.squelch_open)
            if squelch_open != self._last_squelch_open:
                self._last_squelch_open = squelch_open
                self.squelch_state_changed.emit(squelch_open)
